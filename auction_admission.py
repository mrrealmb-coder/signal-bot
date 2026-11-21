"""Entry-only XAU auction check, shared with the BOSFib desktop application."""
import json
import pathlib
import subprocess


def read_flow(cut, direction, method):
    path = pathlib.Path(__file__).with_name('auction_read.mjs')
    try:
        run = subprocess.run(['node', str(path), str(cut), str(direction), method],
                             capture_output=True, text=True, check=True, timeout=9)
        return json.loads(run.stdout)
    except (ValueError, OSError, subprocess.SubprocessError):
        return {'verdict': 'wait', 'pattern': 'data', 'cut': cut, 'error': 'reader'}


# The reading is recorded for every gold entry but does not withhold it (operator, 2026-11-21): on the
# record so far it changed 0R on the bot and BOSFib closes, and it delayed market entries waiting for
# Binance. Set True to let a non-allow verdict withhold the entry again.
GATES_ENTRY = False


def check_entry(state, method, key, signal, now, reader=read_flow, gates=None):
    if key.split('|')[0] != 'XAUUSD':
        return True
    cut = signal['time']
    sid = f"{method}|{key}|{cut}|{signal['dir']}"
    book = state.setdefault('auction_v1', {})
    decision = book.get(sid, {})
    if decision.get('verdict') not in ('allow', 'block', 'expired'):
        if now < cut:
            return False
        if now - cut > (6 * 3600000 if method == 'smc' else 5 * 60000):
            decision = {'verdict': 'expired', 'pattern': 'age', 'cut': cut}
        else:
            decision = reader(cut, signal['dir'], method)
        book[sid] = {**decision, 'entry': signal['entry'], 'sl': signal['sl'], 'tp': signal['tp']}
        if len(book) > 240:
            oldest = min(book, key=lambda name: book[name]['cut'])
            del book[oldest]
    if not (GATES_ENTRY if gates is None else gates):
        return True
    return decision['verdict'] == 'allow'
