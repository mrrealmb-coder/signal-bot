"""Movement notifications only. No orders, trade ledger, or broker feed writes."""
import math

VERSION = 'movement-watch-v1'
STEP_MS = 900000
FRESH_MS = 180000
COOLDOWN_MS = 3600000


def latest_break(bars, now_ms, *, atr_fn, ema_fn, swing=3):
    closed = [b for b in bars if b['ct'] < now_ms]
    if len(closed) < 50 or closed[-1]['ct'] + 1 != now_ms:
        return None
    high = low = None
    trend, latest = 0, None
    for i, bar in enumerate(closed):
        pivot = i - swing
        if pivot >= swing:
            window = closed[pivot - swing:i + 1]
            if closed[pivot]['h'] >= max(x['h'] for x in window):
                high = {'price': closed[pivot]['h'], 'broken': False}
            if closed[pivot]['l'] <= min(x['l'] for x in window):
                low = {'price': closed[pivot]['l'], 'broken': False}
        if high and not high['broken'] and bar['c'] > high['price']:
            latest = dict(idx=i, direction=1, kind='CHoCH' if trend == -1 else 'BOS', level=high['price'])
            high['broken'], trend = True, 1
        elif low and not low['broken'] and bar['c'] < low['price']:
            latest = dict(idx=i, direction=-1, kind='CHoCH' if trend == 1 else 'BOS', level=low['price'])
            low['broken'], trend = True, -1
    if latest is None or latest['idx'] != len(closed) - 1:
        return None
    at = atr_fn(closed, 14)[-1]
    if not math.isfinite(at) or at <= 0:
        return None
    last, direction = closed[-1], latest['direction']
    span = last['h'] - last['l']
    ema = ema_fn([b['c'] for b in closed], 20)
    return dict(latest, time=now_ms, reference=last['c'], atr=at,
                body_atr=abs(last['c'] - last['o']) / at,
                directional_close=((last['c'] - last['l']) if direction == 1 else (last['h'] - last['c'])) / span if span > 0 else 0,
                ema_slope_aligned=(ema[-1] - ema[-4]) * direction > 0)


def strong_break(event):
    return event['body_atr'] >= .5 and event['directional_close'] >= .7 and event['ema_slope_aligned']


def active_recipients(chat_ids, roles):
    # Day, Swing, Scalp and approved default-role chats receive the same observation.
    return sorted({str(cid) for cid in chat_ids if roles.get(str(cid)) != 'off'})


def run_watch(slot, bars, now_ms, recipients, deliver, persist, *, atr_fn, ema_fn, daily_direction=None, clock_ms=None):
    """Persist before network; acknowledge per chat; retry only while the event is fresh.

    Delivery is at-least-once: Telegram has no idempotency key. A timeout after
    server acceptance can duplicate a retry. Confirmed recipients are not resent.
    """
    allowed = set(recipients)
    clock = (lambda: max(now_ms, int(clock_ms()))) if clock_ms else (lambda: now_ms)
    closed = [b for b in bars if b['ct'] < now_ms]
    if not closed:
        return 'no_closed_data'
    last_close = closed[-1]['ct'] + 1
    if 'watermark' not in slot:
        slot.update(version=VERSION, initialized_at=now_ms, watermark=last_close,
                    evaluated_at=now_ms, status='initialized', last_by_direction={}, pending={}, receipts={})
        persist()  # First activation never backfills an old break.
        return 'initialized'
    if slot.get('version') != VERSION:
        raise ValueError('Unknown movement-watch state version')

    # Process one latest closed candle only. Downtime never creates a historical alert burst.
    if last_close > slot['watermark']:
        previous = slot['watermark']
        slot.update(watermark=last_close, evaluated_at=now_ms)
        if last_close - previous > STEP_MS:
            slot['skipped_bars'] = slot.get('skipped_bars', 0) + (last_close - previous) // STEP_MS - 1
        if now_ms - last_close > FRESH_MS:
            slot['status'] = 'stale_bar'
        else:
            event = latest_break(closed, last_close, atr_fn=atr_fn, ema_fn=ema_fn)
            if event is None:
                slot['status'] = 'no_break'
            elif not strong_break(event):
                slot['status'] = 'weak_break'
            elif last_close - slot['last_by_direction'].get(str(event['direction']), -10**18) < COOLDOWN_MS:
                slot['status'] = 'cooldown'
            else:
                event['daily_direction'] = daily_direction
                event_id = f"{last_close}:{event['direction']}"
                event['id'] = event_id
                slot['last_by_direction'][str(event['direction'])] = last_close
                slot['last_alert'] = dict(event, acknowledged=0, recipients=len(allowed))
                slot['pending'][event_id] = {'event': event, 'expires': last_close + FRESH_MS,
                                            'recipients': {cid: {'attempts': 0, 'next_try': 0} for cid in sorted(allowed)}}
                slot['status'] = 'queued' if allowed else 'no_recipients'
        persist()

    for event_id, item in list(slot['pending'].items()):
        if clock() >= item['expires']:
            slot['last_delivery_failure'] = {'id': event_id, 'reason': 'expired', 'at': now_ms,
                                             'remaining': len(item['recipients'])}
            del slot['pending'][event_id]
            persist()
            continue
        for cid, attempt in list(item['recipients'].items()):
            wall_ms = clock()
            if wall_ms >= item['expires']:
                break  # A slow previous request must not make later recipients receive stale alerts.
            receipt_key = event_id + ':' + cid
            if cid not in allowed or receipt_key in slot['receipts']:
                del item['recipients'][cid]
                persist()
                continue
            if attempt['next_try'] > wall_ms:
                continue
            attempt['attempts'] += 1
            attempt['next_try'] = wall_ms + 60000
            persist()
            try:
                response = deliver(cid, item['event'])
                message_id = response.get('result', {}).get('message_id') if response.get('ok') else None
                if not message_id:
                    raise ValueError('Telegram response lacks delivery acknowledgement')
            except Exception as exc:  # Keep transient failures local; do not remove recipients.
                attempt['last_error'] = type(exc).__name__
                slot['last_delivery_failure'] = {'id': event_id, 'reason': type(exc).__name__, 'at': now_ms}
                persist()
                continue
            slot['receipts'][receipt_key] = {'at': now_ms, 'message_id': message_id}
            del item['recipients'][cid]
            if slot.get('last_alert', {}).get('id') == event_id:
                slot['last_alert']['acknowledged'] += 1
            persist()
        if not item['recipients']:
            del slot['pending'][event_id]
            persist()
    old_receipts = [key for key, value in slot['receipts'].items() if now_ms - value['at'] > 7 * 86400000]
    for key in old_receipts:
        del slot['receipts'][key]
    if old_receipts:
        persist()
    return slot['status']
