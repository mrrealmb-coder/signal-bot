"""Pure experimental gold entry plans and chronological paper evaluation.

No Telegram, credentials, broker/feed writes or network access in this module.
"""
import math

MINUTE = 60000
HOUR = 60 * MINUTE
COST_RESERVE = .60


def cents(value, up):
    return (math.ceil(value * 100 - 1e-8) if up else math.floor(value * 100 + 1e-8)) / 100


def build_plan(event, trigger, risk_usd=30):
    """Return (plan, reason); levels use the input series' fixed price coordinates."""
    d, at = event['direction'], event['atr']
    values = [at, event['level'], trigger['h'], trigger['l'], risk_usd]
    if d not in (-1, 1) or not all(math.isfinite(v) for v in values) or at <= 0 or risk_usd <= 0:
        return None, 'invalid_input'
    if trigger['ct'] + 1 != event['time']:
        return None, 'trigger_time_mismatch'
    entry = cents((trigger['h'] if d == 1 else trigger['l']) + d * .05 * at, d == 1)
    sl = cents(event['level'] - d * .20 * at, d == -1)
    risk = (entry - sl) * d
    if not .5 * at <= risk <= 3 * at:
        return None, 'risk_distance'
    lots = math.floor(risk_usd / ((risk + COST_RESERVE) * 100) * 100 + 1e-9) / 100
    if lots < .01:
        return None, 'minimum_lot_over_budget'
    tp = round(entry + d * 2 * risk, 2)
    return dict(id=f"entry-v1:{event['time']}:{d}", direction=d, kind=event['kind'],
                signal_time=event['time'], publish_at=event['time'] + MINUTE,
                expires=event['time'] + HOUR, entry=entry, sl=sl, tp=tp,
                risk=risk, atr=at, lots=lots, modeled_risk_usd=round(lots * 100 * (risk + COST_RESERVE), 2),
                status='pending', last_ct=event['time'] - 1, daily_direction=event.get('daily_direction')), 'ok'


def terminal(plan, status, at, exit_price=None):
    plan.update(status=status, ended_at=at)
    if exit_price is not None:
        plan['exit'] = exit_price
        plan['gross_pips'] = (exit_price - plan['fill']) * plan['direction'] / .1
        plan['gross_r'] = (exit_price - plan['fill']) * plan['direction'] / plan['risk']
    return status


def advance(plan, bar, market_open):
    """Consume one *closed* minute. Data gaps and unknown intrabar order stay unknown.

    No interpolated fills, no resolving both-hit candles with favorable ordering.
    Stops are paper levels, not proof of a broker fill.
    """
    if plan['status'] not in ('pending', 'filled'):
        return plan['status']
    ct = bar['ct']
    if ct <= plan['last_ct']:
        return plan['status']
    op = ct + 1 - MINUTE
    if op != plan['last_ct'] + 1:
        return terminal(plan, 'data_gap', op)
    plan['last_ct'] = ct
    d = plan['direction']
    o, hi, lo = d * bar['o'], max(d * bar['h'], d * bar['l']), min(d * bar['h'], d * bar['l'])
    en, sl, tp = d * plan['entry'], d * plan['sl'], d * plan['tp']
    if op < plan['publish_at']:
        if hi >= en or lo <= sl:
            return terminal(plan, 'crossed_before_publication', ct + 1)
        return plan['status']
    if not market_open(op):
        return terminal(plan, 'market_closed', op)
    if plan['status'] == 'pending':
        if op >= plan['expires']:
            return terminal(plan, 'expired', op)
        if o <= sl:
            return terminal(plan, 'cancelled', op)
        if hi >= en and lo <= sl and o < en:
            plan['r_bounds'] = [-1 - COST_RESERVE / plan['risk'], 0]
            return terminal(plan, 'ambiguous_entry_sl', ct + 1)
        if hi < en:
            if lo <= sl:
                return terminal(plan, 'cancelled', ct + 1)
            if not market_open(ct + 1):
                return terminal(plan, 'cancelled_market_close', ct + 1)
            return plan['status']
        fill = max(en, o)
        plan.update(status='filled', fill=d * fill, filled_at=op, filled_bar=ct,
                    hold_until=op + 6 * HOUR, favorable_pips_upper=0, adverse_pips_upper=0)
    fill = d * plan['fill']
    # On the entry minute, some extrema can predate the fill. Explicit upper bounds.
    plan['favorable_pips_upper'] = max(plan['favorable_pips_upper'], max(0, hi - fill) / .1)
    plan['adverse_pips_upper'] = max(plan['adverse_pips_upper'], max(0, fill - lo) / .1)
    if o <= sl:
        return terminal(plan, 'sl', op, d * o)
    if o >= tp:
        return terminal(plan, 'tp', op, plan['tp'])
    if hi >= tp and lo <= sl:
        plan['r_bounds'] = [(sl - fill - COST_RESERVE) / plan['risk'],
                            (tp - fill - COST_RESERVE) / plan['risk']]
        return terminal(plan, 'ambiguous_sl_tp', ct + 1)
    if lo <= sl:
        return terminal(plan, 'sl', ct + 1, plan['sl'])
    if hi >= tp:
        return terminal(plan, 'tp', ct + 1, plan['tp'])
    if ct + 1 >= plan['hold_until'] or not market_open(ct + 1):
        return terminal(plan, 'time_exit', ct + 1, bar['c'])
    return plan['status']
