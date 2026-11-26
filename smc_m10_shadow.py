"""Prospective gold SMC M10 paper journal. One slot, independent of live orders."""
import math


MINUTE = 60_000
TTL = 6 * 60 * MINUTE
SOURCE = 'binance:XAUTUSDT'
VERSION = 1
HISTORY_LIMIT = 1000


class MissingMinute(ValueError):
    pass


class Tape:
    def __init__(self, bars, now, market_open):
        self.by_open = {}
        self.now = now
        self.market_open = market_open
        self.current = now // MINUTE * MINUTE
        for bar in bars:
            op = bar['ct'] + 1 - MINUTE
            if op % MINUTE == 0 and op <= self.current:
                # Duplicate rows do not prove which quote was observed.
                self.by_open[op] = None if op in self.by_open else bar

    def prices(self, op, basis):
        bar = self.by_open.get(op)
        if not bar or bar.get('_source') != SOURCE:
            raise MissingMinute('missing XAUT minute')
        # A cached intraminute snapshot cannot later be treated as a closed candle.
        snapshot = bar.get('_snapshot_at', self.now if op < self.current else None)
        if snapshot is None or snapshot > self.now or snapshot < min(self.current, op + MINUTE):
            raise MissingMinute('stale XAUT snapshot')
        if not all(isinstance(bar.get(k), (int, float)) and math.isfinite(bar[k])
                   for k in ('_basis', 'o', 'l', 'h', 'c')):
            raise MissingMinute('invalid XAUT quote')
        if not 0 < bar['l'] <= min(bar['o'], bar['c']) <= max(bar['o'], bar['c']) <= bar['h']:
            raise MissingMinute('invalid XAUT OHLC')
        offset = bar['_basis'] - basis
        return bar['o'] + offset, bar['l'] + offset, bar['h'] + offset


def touched(plan, low, high):
    return low <= plan['entry'] if plan['dir'] == 1 else high >= plan['entry']


def archive(journal, plan, status, now):
    plan.update(status=status, decided_at=now)
    journal['records'].append(plan)
    if len(journal['records']) > HISTORY_LIMIT:
        del journal['records'][:-HISTORY_LIMIT]
    if journal['active'] is plan:
        journal['active'] = None


def update_bar(journal, plan, tape, op, complete):
    opening, low, high = tape.prices(op, plan['_basis'])
    d = plan['dir']
    if plan['status'] == 'pending':
        if not touched(plan, low, high):
            return
        if op < plan['seen_at']:
            # This candle straddles our observation. Even the earlier REST
            # snapshot cannot locate the touch relative to recording the plan.
            archive(journal, plan, 'observation_minute_touch', tape.now)
            return
        plan.update(status='filled', fill_bar=op, fill_seen_at=tape.now)
    if not complete:
        return
    stopped = low <= plan['sl'] if d == 1 else high >= plan['sl']
    target = high >= plan['tp'] if d == 1 else low <= plan['tp']
    if not stopped and not (target and op > plan['fill_bar']):
        return
    # Model a limit fill at its stated price, give gaps through stops their
    # worse open, and award a target only from the minute after the fill.
    exit_price = plan['tp']
    if stopped:
        exit_price = min(opening, plan['sl']) if d == 1 else max(opening, plan['sl'])
    gain = d * (exit_price - plan['entry'])
    risk = abs(plan['entry'] - plan['sl'])
    plan.update(exit_bar=op, exit_price=exit_price, gross_r=gain/risk,
                net_r=(gain - 0.60)/risk, stress_r=(gain - 0.90)/risk)
    archive(journal, plan, 'sl' if stopped else 'tp', tape.now)


def advance(journal, tape):
    plan = journal['active']
    if not plan or not tape.by_open:
        return
    op = plan['next_minute']
    while op <= tape.current:
        if plan['status'] == 'pending' and op >= plan['expires']:
            archive(journal, plan, 'expired', tape.now)
            return
        if tape.market_open(op):
            try:
                update_bar(journal, plan, tape, op, op < tape.current)
            except MissingMinute:
                # A current quote can be retried. A hole before later *closed*
                # evidence makes any subsequent fill/exit indeterminate.
                if op < tape.current and any(t > op for t in tape.by_open):
                    archive(journal, plan, 'data_gap', tape.now)
                return
            if journal['active'] is None:
                return
        if op == tape.current:
            return
        op += MINUTE
        plan['next_minute'] = op


def rejection(plan, tape, candidate):
    if plan['time'] < 0 or plan['time'] % MINUTE or plan['time'] > tape.now:
        return 'invalid_setup'
    if tape.now >= plan['expires']:
        return 'expired_before_observation'
    if candidate.get('status', 'pending') != 'pending':
        return 'not_pending_at_observation'
    if candidate.get('_source') != SOURCE or not all(
        isinstance(candidate.get(k), (int, float)) and math.isfinite(candidate[k])
        for k in ('entry', 'sl', 'tp', '_basis')
    ):
        return 'invalid_setup'
    plan.update({k: round(candidate[k], 2) for k in ('entry', 'sl', 'tp')})
    plan.update(_source=SOURCE, _basis=candidate['_basis'])
    d = candidate.get('dir')
    if d not in (-1, 1) or not d * plan['sl'] < d * plan['entry'] < d * plan['tp']:
        return 'invalid_setup'
    if not tape.market_open(tape.current):
        return 'market_closed'
    try:
        for op in range(plan['time'], tape.current + 1, MINUTE):
            if tape.market_open(op):
                _, low, high = tape.prices(op, plan['_basis'])
                if touched(plan, low, high):
                    return 'crossed_before_observation'
    except MissingMinute:
        return 'missing_observation_quote'
    return None


def tick(journal, candidate, fine_bars, now_ms, primary_status, market_open):
    """Advance one observation with M1 XAUT basis-adjusted bars and a current quote.

    'clear'/None = no primary, 'pending'/'filled' = M15 occupied. Any other
    primary state is unknown. Returns nothing; the caller persists the journal.
    Candle timestamps and observation times are distinct UTC milliseconds.
    """
    if not journal:
        journal.update(version=VERSION, started_at=now_ms, last_setup=None, active=None, records=[])
        return
    if journal['version'] != VERSION:
        raise ValueError('unknown M10 journal version')
    armed = journal['started_at']
    last = journal['last_setup']
    fresh = (candidate is not None and isinstance(candidate.get('time'), int)
             and armed <= candidate['time'] <= now_ms and (last is None or candidate['time'] > last))
    if not fresh and journal['active'] is None:
        return
    tape = Tape(fine_bars, now_ms, market_open)
    advance(journal, tape)
    active = journal['active']
    occupied = primary_status in ('pending', 'filled')
    if occupied and active:
        if active['status'] == 'filled':
            active.setdefault('first_m15_overlap_at', now_ms)
        else:
            # Without a current quote we cannot establish that the old limit
            # was still pending when the primary took priority.
            verified = False
            if active['next_minute'] == tape.current:
                try:
                    tape.prices(tape.current, active['_basis'])
                    verified = True
                except MissingMinute:
                    pass
            archive(journal, active, 'superseded_by_m15' if verified else 'priority_quote_missing', now_ms)
    if not fresh:
        return
    journal['last_setup'] = candidate['time']
    plan = dict(time=candidate['time'], seen_at=now_ms, dir=candidate.get('dir'),
                expires=candidate['time'] + TTL)
    skip = rejection(plan, tape, candidate)
    if occupied:
        skip = 'm15_occupied'
    elif primary_status not in (None, 'clear'):
        skip = 'primary_unknown'
    elif journal['active'] and journal['active']['status'] == 'filled':
        skip = 'm10_occupied'
    if skip:
        archive(journal, plan, skip, now_ms)
        return
    if journal['active']:
        archive(journal, journal['active'], 'superseded_by_m10', now_ms)
    plan.update(status='pending', next_minute=tape.current)
    journal['active'] = plan
