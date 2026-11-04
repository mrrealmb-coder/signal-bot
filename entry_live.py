"""Telegram-only experimental entry plans. No broker feed or legacy ledger writes."""
import copy
import math
from entry_plans import build_plan, advance, MINUTE, HOUR
from movement_watch import latest_break, strong_break

VERSION = 'entry-v1'
SOURCE = 'binance:XAUTUSDT'


def fixed_bars(bars, basis):
    """Remove each fetch's current basis and apply the plan's original basis."""
    out = []
    for bar in bars:
        offset = bar.get('_basis')
        if bar.get('_source') != SOURCE or not isinstance(offset, (int, float)) or not math.isfinite(offset):
            raise ValueError('Unverified gold price source')
        item = dict(bar)
        for key in ('o', 'h', 'l', 'c'):
            item[key] = bar[key] + offset - basis
            if not math.isfinite(item[key]):
                raise ValueError('Nonfinite gold price')
        out.append(item)
    return out


def queue(slot, plan, kind, recipients, now_ms):
    key = plan['id'] + ':' + kind
    if key in slot['outbox']:
        return
    slot['outbox'][key] = dict(kind=kind, plan=copy.deepcopy(plan),
                              expires=plan['publish_at'] - 10000 if kind == 'setup' else now_ms + 24 * HOUR,
                              recipients={cid: dict(attempts=0, next_try=0) for cid in sorted(recipients)})


def flush(slot, allowed, deliver, persist, clock):
    for key, item in list(slot['outbox'].items()):
        for cid, attempt in list(item['recipients'].items()):
            now = clock()
            if cid not in allowed or now >= item['expires']:
                del item['recipients'][cid]
                slot['last_delivery_failure'] = dict(reason='expired_or_disabled', at=now, kind=item['kind'])
                persist()
                continue
            if now < attempt['next_try']:
                continue
            attempt.update(attempts=attempt['attempts'] + 1, next_try=now + 15000)
            persist()  # Durable attempt before network I/O.
            try:
                response = deliver(cid, item['kind'], item['plan'])
                result = response.get('result', {}) if response.get('ok') else {}
                if not result.get('message_id'):
                    raise ValueError('Missing Telegram acknowledgement')
            except Exception as exc:
                attempt['last_error'] = type(exc).__name__
                slot['last_delivery_failure'] = dict(reason=type(exc).__name__, at=clock(), kind=item['kind'])
                persist()
                continue
            slot['receipts'][key + ':' + cid] = dict(at=clock(), message_id=result['message_id'])
            if item['kind'] == 'setup':
                plan = slot.get('active')
                if plan and plan['id'] == item['plan']['id']:
                    plan.setdefault('delivered_to', {})[cid] = dict(at=clock(), message_id=result['message_id'])
            del item['recipients'][cid]
            persist()
        if not item['recipients']:
            del slot['outbox'][key]
            persist()


def run_entry(slot, bars, minute_bars, now_ms, recipients, deliver, persist, *,
              atr_fn, ema_fn, market_open, basis, basis_fresh, risk_usd=30,
              daily_direction=None, enabled=True, clock_ms=None):
    """Send conditional setup before next minute; activate exactly at close+60s.

    A level hit before activation cancels the setup. Telegram timeout ambiguity
    remains at-least-once; acknowledged recipients are never deliberately resent.
    """
    clock = (lambda: max(now_ms, int(clock_ms()))) if clock_ms else (lambda: now_ms)
    allowed = set(recipients)
    closed = [b for b in bars if b['ct'] < min(now_ms, b.get('_snapshot_at', now_ms))]
    if not closed:
        return 'no_closed_data'
    last_close = closed[-1]['ct'] + 1
    if 'version' not in slot:
        slot.update(version=VERSION, initialized_at=now_ms, watermark=last_close,
                    status='initialized', outbox={}, receipts={}, history=[], last_by_direction={})
        persist()
        return slot['status']
    if slot['version'] != VERSION:
        raise ValueError('Unknown entry plan version')
    slot['evaluated_at'] = now_ms
    plan = slot.get('active')
    if plan:
        before = plan['status']
        aligned = fixed_bars(minute_bars, plan['basis'])
        for bar in aligned:
            if bar['ct'] >= min(now_ms, bar.get('_snapshot_at', now_ms)):
                break
            advance(plan, bar, market_open)
            if plan['status'] not in ('pending', 'filled'):
                break
        recipients_for_plan = set(plan.get('delivered_to', {})) & allowed
        if plan['status'] not in ('pending', 'filled'):
            queue(slot, plan, 'closed', recipients_for_plan, now_ms)
            slot['outbox'].pop(plan['id'] + ':setup', None)
            slot['outbox'].pop(plan['id'] + ':filled', None)
            slot['history'].append(copy.deepcopy(plan))
            slot['history'] = slot['history'][-500:]
            slot['last_plan'] = copy.deepcopy(plan)
            slot['active'] = None
        elif plan['status'] == 'filled' and before != 'filled':
            queue(slot, plan, 'filled', recipients_for_plan, now_ms)
        slot['status'] = plan['status']
        persist()

    if last_close > slot['watermark']:
        slot['watermark'] = last_close  # No stale backfill after downtime/restart.
        wall = clock()
        if not enabled:
            slot['status'] = 'disabled'
        elif not market_open(wall):
            slot['status'] = 'market_closed'
        elif not basis_fresh:
            slot['status'] = 'basis_stale'
        elif wall >= last_close + MINUTE - 15000:
            slot['status'] = 'late_detection'
        elif not allowed:
            slot['status'] = 'no_recipients'
        else:
            aligned = fixed_bars(closed, basis)
            event = latest_break(aligned, last_close, atr_fn=atr_fn, ema_fn=ema_fn)
            if not event:
                slot['status'] = 'no_break'
            elif not strong_break(event):
                slot['status'] = 'weak_break'
            elif last_close - slot['last_by_direction'].get(str(event['direction']), -10**18) < HOUR:
                slot['status'] = 'cooldown'
            else:
                # Match the research detector cooldown even if the entry plan is rejected.
                slot['last_by_direction'][str(event['direction'])] = last_close
                event['daily_direction'] = daily_direction
                plan, reason = build_plan(event, aligned[-1], risk_usd)
                slot['status'] = reason
                if slot.get('active'):
                    slot['status'] = 'blocked_active_plan'
                elif plan:
                    minute_aligned = fixed_bars(minute_bars, basis)
                    current = [b for b in minute_aligned if b['ct'] + 1 - MINUTE == last_close]
                    crossed = any((b['h'] >= plan['entry'] or b['l'] <= plan['sl']) if event['direction'] == 1
                                  else (b['l'] <= plan['entry'] or b['h'] >= plan['sl']) for b in current)
                    if not current or wall - now_ms > 10000:
                        slot['status'] = 'missing_current_quote'
                    elif crossed:
                        slot['status'] = 'crossed_before_publication'
                    else:
                        plan.update(basis=basis, source=SOURCE, created_at=wall, delivered_to={})
                        slot['active'] = plan
                        slot['status'] = 'pending'
                        queue(slot, plan, 'setup', allowed, now_ms)
        persist()
    flush(slot, allowed, deliver, persist, clock)
    # Bound receipts while keeping an auditable per-plan acknowledgement history.
    slot['receipts'] = {k: v for k, v in slot['receipts'].items() if now_ms - v['at'] < 7 * 24 * HOUR}
    persist()
    return slot['status']
