"""Exercise the live state machine on historical data, with mock Telegram only."""
import argparse
import bisect
from collections import Counter
import json
from pathlib import Path
import signal_bot as bot
from entry_live import run_entry, SOURCE

ROOT = Path(__file__).parent / 'entry_results'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--research-dir', type=Path, required=True)
    args = parser.parse_args()
    market = args.research_dir / 'movement_results'
    manifest = json.loads((market / 'manifest.json').read_text())
    bars = json.loads((market / 'market' / 'XAUTUSDT_15m.json').read_text())
    minutes = json.loads((ROOT / 'XAUTUSDT_1m.json').read_text())
    for b in bars + minutes:
        b.update(_source=SOURCE, _basis=0.)
    closes = [b['ct'] for b in bars]
    expected = json.loads((ROOT / 'plans.json').read_text())
    state, calls = {}, []
    snapshot = []
    def send(cid, kind, plan):
        calls.append((cid, kind, plan['id']))
        return {'ok': True, 'result': {'message_id': len(calls)}}
    def run(t, minute_rows):
        nonlocal snapshot
        if not snapshot or t % 900000 == 0:
            i = bisect.bisect_left(closes, t)
            snapshot = [b for b in bars[max(0, i-1000):i] if bot.market_open(b['ct']+1-900000)]
        run_entry(state, snapshot, minute_rows, t+1000, ['day','scalp','swing'], send, lambda: None,
                  atr_fn=bot.atr, ema_fn=bot.ema, market_open=bot.market_open, basis=0., basis_fresh=True)
    run(manifest['start']-900000, [])
    for i, b in enumerate(minutes):
        t = b['ct']+1-60000
        if t % 900000 and not state.get('active') and not state.get('outbox'):
            continue
        # Poll at minute start: only its open is known; no future H/L/close leak.
        forming = dict(b, h=b['o'], l=b['o'], c=b['o'])
        minute_rows = minutes[max(0, i-2):i] + [forming]
        run(t, minute_rows)
    run(manifest['cutoff'], minutes[-3:])
    actual = state['history'] + ([state['active']] if state.get('active') else [])
    fills = {r['id']: r for r in actual if 'fill' in r}
    want = {r['id']: r for r in expected if 'fill' in r}
    assert fills.keys() == want.keys(), {'missing': list(want.keys()-fills.keys()), 'extra': list(fills.keys()-want.keys())}
    for key, row in fills.items():
        for field in ('entry','sl','tp','status','fill','exit','ended_at','gross_r'):
            assert row.get(field) == want[key].get(field), (key, field, row.get(field), want[key].get(field))
    assert len(calls) == len(set(calls)), 'Duplicate acknowledged message'
    assert not state['outbox']
    counts = dict(Counter(kind for _,kind,_ in calls))
    result = dict(matching_filled_plans=len(fills), exact_levels_outcomes_match=True,
                  mock_message_counts=counts, duplicate_acknowledged_messages=0, real_messages_sent=0,
                  conditional_setups=len([1 for cid,kind,key in calls if cid=='day' and kind=='setup']),
                  live_history_statuses=dict(Counter(r['status'] for r in actual)))
    (ROOT/'live_parity.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
