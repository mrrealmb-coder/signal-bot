"""Independent prospective Breakout paper detector; no live-state dependency.

Hourly processing of newly closed public candles. Signal-time decisions use only
then-closed candles; publication is a modeled +60s, not actual Telegram delivery.
"""
import bisect
import json
import time
from pathlib import Path
from movement_watch import latest_break,strong_break
from entry_plans import build_plan
from signal_bot import atr,ema
from profile_shadow import fetch_minutes,enroll,update,report,market_open,ACTIVE,DAY,MINUTE

STEP=15*MINUTE
VERSION='independent-breakout-paper-v1'

def tick(j,now,fetch=fetch_minutes,detect=latest_break):
    end=now//MINUTE*MINUTE
    if not j:
        j.update(version=VERSION,started_at=now,last_run=now,watermark=now//STEP*STEP,
            last_by_direction={},records={},errors={},decisions={},source_mode='independent raw XAUTUSDT',skipped=[])
        return
    if j['version']!=VERSION:raise ValueError('Unknown paper version')
    earliest=max(j['watermark'],now//STEP*STEP-2*DAY)
    if earliest>j['watermark']:
        j['skipped'].append(dict(start=j['watermark'],end=earliest,reason='downtime exceeds 48h'))
        j['watermark']=earliest
        for r in j['records'].values():
            if r.get('complete') or r['signal_time']>=earliest:continue
            for p in [r['baseline'],r['profile'],r['observer']]:
                if p and p['status'] in ACTIVE:p.update(status='data_gap',ended_at=earliest)
            r['complete']=True
    start=earliest-1000*STEP
    raw=fetch(start,end)
    if [b['ct'] for b in raw]!=list(range(start+MINUTE-1,end,MINUTE)):
        raise ValueError('Incomplete paper source range')
    times=[b['ct']+1-MINUTE for b in raw];m15=[]
    for i in range(0,len(raw)-14,15):
        a=raw[i:i+15]
        m15.append(dict(o=a[0]['o'],h=max(b['h'] for b in a),l=min(b['l'] for b in a),c=a[-1]['c'],v=sum(b['v'] for b in a),ct=a[-1]['ct']))
    def progress(until):
        for r in j['records'].values():
            if r.get('complete'):continue
            plans=[p for p in [r['baseline'],r['profile'],r['observer']] if p and p['status'] in ACTIVE]
            first=min(p['last_ct']+1 for p in plans)
            if first<start:raise ValueError('Pending paper history unavailable')
            stop=min(until,r['baseline']['expires']+6*3600000)
            if stop>first:update(r,raw[bisect.bisect_left(times,first):bisect.bisect_left(times,stop)])
    for i,b in enumerate(m15):
        t=b['ct']+1
        if t<=j['watermark']:continue
        progress(t)  # advance only to decision time, never to the end of the batch
        j['watermark']=t
        if not market_open(t):continue
        event=detect(m15[max(0,i-999):i+1],t,atr_fn=atr,ema_fn=ema)
        if not event or not strong_break(event):continue
        d=str(event['direction'])
        if t-j['last_by_direction'].get(d,-10**18)<3600000:continue
        j['last_by_direction'][d]=t
        p,why=build_plan(event,b)
        if any(r['baseline']['status'] in ACTIVE for r in j['records'].values()):why='blocked_active_plan'
        if p is None or why!='ok':
            j['decisions'][str(t)]=why;continue
        p.update(basis=0,source='binance:XAUTUSDT')
        day=t//DAY*DAY
        prior=raw[bisect.bisect_left(times,day-DAY):bisect.bisect_left(times,day)]
        r=enroll(p,prior,now);r['origin']='independent_paper';j['records'][p['id']]=r
        j['decisions'][str(t)]='paper_setup'
    progress(end)
    j['last_run']=now

def status(j):
    text=report(j).replace('Prospective Profile TP shadow test','Independent Breakout paper test')
    text=text.replace('New acknowledged signals:','Independently detected paper setups:')
    text=text.replace('Historical plans before initialization excluded.',
        'Uses raw XAUT prices, NOT spot-adjusted broker prices. No state.json or Telegram acknowledgement dependency. Signals before this independent test initialization excluded.')
    text+='\nModeled activation is signal close +60s; processing is delayed, so this is not a live execution-latency test.\n'
    text+=f"Skipped downtime windows: {len(j['skipped'])}. Detector watermark: {j['watermark']}.\n"
    return text

def main():
    path=Path('breakout_paper_state.json');j=json.loads(path.read_text()) if path.exists() else {}
    now=int(time.time()*1000);failed=False
    try:tick(j,now);j['errors'].pop('runner',None)
    except Exception as exc:
        j.setdefault('errors',{})['runner']=dict(at=now,error=type(exc).__name__);failed=True
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(j,indent=2),encoding='utf-8');tmp.replace(path)
    Path('BREAKOUT_PAPER_STATUS.md').write_text(status(j),encoding='utf-8')
    print(f"Independent paper: {len(j['records'])} setups, {len(j['errors'])} errors")
    if failed:raise SystemExit(1)

if __name__=='__main__':main()
