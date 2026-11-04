"""Frozen entry protocol replay. Output files include every accepted/rejected event."""
import argparse
import bisect
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import statistics
from entry_plans import build_plan, advance
from signal_bot import market_open

ROOT = Path(__file__).parent / 'entry_results'
UB = timezone(timedelta(hours=8))


def stamp(ms):
    return datetime.fromtimestamp(ms / 1000, UB).strftime('%m/%d %H:%M')


def summarize(rows):
    closed = [r for r in rows if 'gross_r' in r]
    bounds = [r['r_bounds'] for r in rows if 'r_bounds' in r]
    net = {str(cost): sum(r['gross_r'] - cost / r['risk'] for r in closed) for cost in (.30, .60)}
    return dict(events=len(rows), statuses=dict(Counter(r['status'] for r in rows)),
                filled=sum('fill' in r for r in rows), resolved=len(closed),
                winners=sum(r['gross_r'] - .60 / r['risk'] > 0 for r in closed),
                net_r=net, net_r_including_ambiguities_lower=net['0.6'] + sum(x[0] for x in bounds),
                net_r_including_ambiguities_upper=net['0.6'] + sum(x[1] for x in bounds),
                gross_pips=sum(r['gross_pips'] for r in closed),
                net_pips_at_060=sum(r['gross_pips'] - 6 for r in closed),
                median_favorable_pips_upper=statistics.median(r['favorable_pips_upper'] for r in closed) if closed else None,
                median_adverse_pips_upper=statistics.median(r['adverse_pips_upper'] for r in closed) if closed else None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--research-dir', required=True, type=Path)
    args = parser.parse_args()
    source = args.research_dir / 'movement_results'
    manifest = json.loads((source / 'manifest.json').read_text())
    events = json.loads((source / 'alerts.json').read_text())['strong_watch']
    triggers = {b['ct'] + 1: b for b in json.loads((source / 'market' / 'XAUTUSDT_15m.json').read_text())}
    bars = json.loads((ROOT / 'XAUTUSDT_1m.json').read_text())
    times = [b['ct'] + 1 - 60000 for b in bars]
    assert len(set(times)) == len(times)
    rows = []
    busy_until = 0
    for event in events:
        row = dict(signal_time=event['time'], direction=event['direction'])
        if event['time'] < busy_until:
            rows.append(dict(row, status='blocked_active_plan'))
            continue
        plan, reason = build_plan(event, triggers[event['time']])
        if plan is None:
            rows.append(dict(row, status=reason))
            continue
        # Publication is one minute later. Old levels hit during that minute
        # are rejected, so the replay cannot claim an entry before the message.
        i = bisect.bisect_left(times, event['time'])
        for bar in bars[i:]:
            status = advance(plan, bar, market_open)
            if status not in ('pending', 'filled'):
                break
        busy_until = plan.get('ended_at', manifest['cutoff'])
        rows.append(plan)
    summary = summarize(rows)
    summary['this_week'] = summarize([r for r in rows if r['signal_time'] >= manifest['week']])
    summary['protocol'] = 'ENTRY_PROTOCOL.md (frozen before replay)'
    summary['period'] = [manifest['start'], manifest['cutoff']]
    summary['source'] = 'Raw XAUTUSDT, not OANDA or broker bid/ask'
    (ROOT / 'plans.json').write_text(json.dumps(rows, indent=2))
    (ROOT / 'summary.json').write_text(json.dumps(summary, indent=2))
    lines = ['# Entry / SL / TP replay', '',
             'Exploratory retrospective test; raw XAUTUSDT, not OANDA/broker execution.', '',
             '```json', json.dumps(summary, indent=2), '```', '',
             '## This week (UTC+8)', '',
             '| Time | Side | Entry | SL | TP | Status | Gross pips |',
             '|---|---|---:|---:|---:|---|---:|']
    for r in rows:
        if r['signal_time'] < manifest['week']:
            continue
        lines.append('| ' + ' | '.join([stamp(r['signal_time']), 'BUY' if r['direction'] == 1 else 'SELL',
                     *[f"{r[k]:.2f}" if k in r else '—' for k in ('entry', 'sl', 'tp')], r['status'],
                     f"{r['gross_pips']:.1f}" if 'gross_pips' in r else '—']) + ' |')
    lines += ['', 'Costs: $0.60 per oz = 6 pips per completed paper trade. Excursion columns are',
              'upper bounds because entry/exit-minute extrema may precede/follow the fill/exit.',
              'Ambiguous trades are shown as bounds, never relabeled as wins.']
    (ROOT / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    print('\n'.join(lines[lines.index('## This week (UTC+8)'):]))


if __name__ == '__main__':
    main()
