"""Compare production notifier decisions to the previously frozen research events."""
import argparse
import bisect
import json
from pathlib import Path
import signal_bot as bot
from movement_watch import run_watch


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--research-dir',required=True,type=Path)
    args=parser.parse_args()
    source=args.research_dir/'movement_results'
    manifest=json.loads((source/'manifest.json').read_text())
    expected=json.loads((source/'alerts.json').read_text())['strong_watch']
    rows=json.loads((source/'market'/'XAUTUSDT_15m.json').read_text())
    closes=[b['ct'] for b in rows]
    state={};sent=[]
    def past(t):
        i=bisect.bisect_left(closes,t)
        return [b for b in rows[max(0,i-1000):i] if bot.market_open(b['ct']+1-900000)]
    def send(cid,event):
        sent.append((cid,event['time'],event['direction']))
        return {'ok':True,'result':{'message_id':len(sent)}}
    def run(t):
        run_watch(state,past(t),t+1000,['day','scalp','swing'],send,lambda:None,
                  atr_fn=bot.atr,ema_fn=bot.ema,daily_direction=-1)
    run(manifest['start']-900000)
    for t in range(manifest['start'],manifest['cutoff']+1,900000):
        if bot.market_open(t):
            run(t)
            run(t)  # Repeated polling of the same candle cannot resend an acknowledged event.
    want={(r['time'],r['direction']) for r in expected}
    got={(t,d) for _,t,d in sent}
    assert want==got, {'missing':sorted(want-got),'extra':sorted(got-want)}
    assert len(sent)==len(want)*3
    assert len(set(sent))==len(sent)
    result={'expected_events':len(want),'production_events':len(got),'mock_acknowledgements':len(sent),
            'duplicate_deliveries':len(sent)-len(set(sent)),'live_messages_sent':0,'cutoff':manifest['cutoff']}
    output=Path(__file__).parent/'live_test_results';output.mkdir(exist_ok=True)
    (output/'replay_parity.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
