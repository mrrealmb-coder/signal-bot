"""Prospective paired TP observation. No Telegram, broker, or live-state writes.

Public Binance data, acknowledged entry-v1 plans read from state.json, separate
durable journal. Entry/SL/basis frozen at original signal. No historical backfill.
"""
import copy
import json
import math
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from entry_plans import advance

VERSION='profile-shadow-v1'
DAY=86400000
MINUTE=60000
ACTIVE=('pending','filled')

def market_open(t):
    wd=(t//DAY+3)%7;hr=(t//3600000)%24
    return not(wd==5 or wd==4 and hr>=21 or wd==6 and hr<22)

def profile_levels(bars):
    bins=defaultdict(float)
    for b in bars:bins[math.floor((b['h']+b['l']+b['c'])/3)]+=b['v']
    if not bins or sum(bins.values())<=0:return []
    a=b=poc=max(sorted(bins),key=bins.get);mass=bins[poc];required=.7*sum(bins.values())
    while mass<required and (a>min(bins) or b<max(bins)):
        left=bins.get(a-1,0) if a>min(bins) else -1
        right=bins.get(b+1,0) if b<max(bins) else -1
        if right>left:b+=1;mass+=max(0,right)
        else:a-=1;mass+=max(0,left)
    return [a,poc+.5,b+1]

def fetch_minutes(start,end):
    """Complete closed range or error; never silently bridge missing data."""
    rows=[]
    for first in range(start,end,1000*MINUTE):
        last=min(first+1000*MINUTE,end)
        url=f'https://data-api.binance.vision/api/v3/klines?symbol=XAUTUSDT&interval=1m&startTime={first}&endTime={last-1}&limit=1000'
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url,timeout=20) as response:raw=json.load(response)
                part=[dict(o=float(r[1]),h=float(r[2]),l=float(r[3]),c=float(r[4]),v=float(r[5]),ct=int(r[6])) for r in raw]
                if [b['ct'] for b in part]!=list(range(first+MINUTE-1,last,MINUTE)):raise ValueError('Missing source minutes')
                if not all(all(math.isfinite(b[k]) for k in ('o','h','l','c','v')) and b['l']<=min(b['o'],b['c'])<=max(b['o'],b['c'])<=b['h'] and b['v']>=0 for b in part):raise ValueError('Invalid source values')
                rows.extend(part);break
            except Exception:
                if attempt==2:raise
                time.sleep(attempt+1)
    return rows

def initial_plan(p):
    keys=('id','direction','kind','signal_time','publish_at','expires','entry','sl','tp','risk','atr','lots','basis','source')
    q={k:p[k] for k in keys}
    if q['source']!='binance:XAUTUSDT' or not all(math.isfinite(q[k]) for k in ['basis','entry','sl','tp','risk','atr']):raise ValueError('Invalid plan source/levels')
    if q['direction'] not in (-1,1) or q['risk']<=0 or q['atr']<=0:raise ValueError('Invalid plan risk')
    q.update(status='pending',last_ct=p['signal_time']-1)
    return q

def enroll(p,prior,now):
    base=initial_plan(p);d=base['direction'];basis=base['basis']
    day=base['signal_time']//DAY*DAY
    if [b['ct'] for b in prior]!=list(range(day-DAY+MINUTE-1,day,MINUTE)):raise ValueError('Incomplete prior UTC day')
    levels=[v-basis for v in profile_levels(prior)]
    forward=[v for v in levels if d*(v-base['entry'])>0]
    level=min(forward,key=lambda v:d*(v-base['entry'])) if forward else None
    distance=d*(level-base['entry'])-.15*base['atr'] if level is not None else None
    status='no_level' if distance is None else ('too_close' if distance-.6<base['risk'] else 'eligible')
    candidate=copy.deepcopy(base) if status=='eligible' else None
    if candidate:candidate['tp']=base['entry']+d*distance
    observer=copy.deepcopy(base);observer['tp']=d*1e12
    return dict(id=base['id'],enrolled_at=now,signal_time=base['signal_time'],basis=basis,
        profile_status=status,profile_levels=levels,profile_level=level,
        profile_distance=distance,baseline=base,profile=candidate,observer=observer,
        reach=0.,measurement='conservative pre-original-SL favorable reach; not exact reversal')

def step_plan(p,bar):
    if p['status'] not in ACTIVE:return
    if bar['ct']<=p['last_ct']:return
    op=bar['ct']+1-MINUTE
    if p['status']=='pending' and op>=p['publish_at'] and p['direction']*bar['o']>=p['direction']*p['tp']:
        p.update(status='target_passed_before_fill',ended_at=op,last_ct=bar['ct']);return
    advance(p,bar,market_open)

def update(record,raw):
    for b in raw:
        bar=dict(b)
        for k in ['o','h','l','c']:bar[k]-=record['basis']
        for name in ['baseline','profile']:
            if record[name]:step_plan(record[name],bar)
        obs=record['observer']
        if obs['status'] not in ACTIVE or bar['ct']<=obs['last_ct']:continue
        step_plan(obs,bar)
        if 'filled_at' not in obs:continue
        op=bar['ct']+1-MINUTE;d=obs['direction']
        if op==obs['filled_at']:
            # The actual gap fill is known; the rest of this candle's path is not.
            if obs['status'] not in ['sl','ambiguous_sl_tp']:
                record['reach']=max(record['reach'],d*(obs['fill']-obs['entry']),0)
            continue
        if obs['status']=='data_gap':continue
        favorable=(d*bar['o'] if min(d*bar['h'],d*bar['l'])<=d*obs['sl'] else max(d*bar['h'],d*bar['l']))-d*obs['entry']
        record['reach']=max(record['reach'],favorable,0)
    obs=record['observer']
    if obs['status'] in ['sl','time_exit']:
        record['reach_complete']=True
        record['baseline_error_pips']=abs(abs(record['baseline']['tp']-obs['entry'])-record['reach'])/.1
        if record['profile_distance'] is not None and record['profile_distance']>0:
            record['profile_error_pips']=abs(record['profile_distance']-record['reach'])/.1
        record['large_move']=record['reach']>=2*obs['risk']
    record['complete']=all(p is None or p['status'] not in ACTIVE for p in [record['baseline'],record['profile'],obs])

def tick(journal,live,now,fetch=fetch_minutes):
    if not journal:
        journal.update(version=VERSION,started_at=now,records={},errors={},last_run=now)
        return  # No earlier plans are admitted, including currently open ones.
    if journal.get('version')!=VERSION:raise ValueError('Unknown shadow version')
    slot=live.get('entry_plans',{}).get('XAUUSD|15m',{});plans=list(slot.get('history',[]))
    if slot.get('active'):plans.append(slot['active'])
    for p in plans:
        if p['signal_time']<journal['started_at'] or not p.get('delivered_to') or p['id'] in journal['records']:continue
        try:
            day=p['signal_time']//DAY*DAY
            record=enroll(p,fetch(day-DAY,day),now)
            journal['records'][p['id']]=record;journal['errors'].pop(p['id'],None)
        except Exception as exc:journal['errors'][p['id']]=dict(at=now,error=type(exc).__name__)
    for key,record in journal['records'].items():
        if record.get('complete'):continue
        try:
            active=[p for p in [record['baseline'],record['profile'],record['observer']] if p and p['status'] in ACTIVE]
            start=min(p['last_ct']+1 for p in active)
            end=min(now//MINUTE*MINUTE,record['baseline']['expires']+6*3600000)
            if start<end:update(record,fetch(start,end))
            journal['errors'].pop(key,None)
        except Exception as exc:journal['errors'][key]=dict(at=now,error=type(exc).__name__)
    journal['last_run']=now

def report(j):
    records=list(j['records'].values());eligible=[r for r in records if r['profile_status']=='eligible']
    paired=[r for r in eligible if r.get('complete') and 'gross_r' in r['baseline'] and 'gross_r' in r['profile']]
    stamp=lambda t:datetime.fromtimestamp(t/1000,timezone.utc).isoformat()
    lines=['# Prospective Profile TP shadow test','',f"Started (UTC): {stamp(j['started_at'])}",f"Last checked (UTC): {stamp(j['last_run'])}",
      '',f"New acknowledged signals: {len(records)}; profile eligible: {len(eligible)}; completed resolved pairs: {len(paired)}; fetch/validation errors: {len(j['errors'])}.",
      'No Telegram messages, no broker orders, no change to original Entry/SL/TP. Prior UTC-day XAUT candle-volume proxy, not broker footprint. Historical plans before initialization excluded.',
      '', '|Plan|Signal UTC|Profile gate|2R result|Profile result|Reach pip|', '|---|---|---|---|---|---:|']
    for r in records:
        lines.append(f"|{r['id']}|{stamp(r['signal_time'])}|{r['profile_status']}|{r['baseline']['status']}|{r['profile']['status'] if r['profile'] else 'not admitted'}|{r['reach']/.1:.1f}{'' if r.get('reach_complete') else ' (incomplete)'}|")
    if paired:
        lines+=['','Same completed pairs, $0.60/oz modeled costs:']
        for name in ['baseline','profile']:
            net=sum(r[name]['gross_r']-.6/r[name]['risk'] for r in paired)
            errors=[r[name+'_error_pips'] for r in paired if name+'_error_pips' in r]
            lines.append(f"- {name}: net {net:+.2f}R; mean reach error {sum(errors)/len(errors):.2f} pip." if errors else f'- {name}: net {net:+.2f}R; reach incomplete.')
    lines+=['','Ambiguous or data-gap outcomes are retained in profile_shadow_state.json and excluded from resolved-pair totals. Unequal entry availability and no-level/too-close skips are not wins. This is a delayed paper observation of frozen signal-time rules, not proof of live executions.']
    return '\n'.join(lines)+'\n'

def main():
    path=Path('profile_shadow_state.json');journal=json.loads(path.read_text()) if path.exists() else {}
    live=json.loads(Path('state.json').read_text())
    tick(journal,live,int(time.time()*1000))
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(journal,indent=2),encoding='utf-8');temp.replace(path)
    Path('PROFILE_SHADOW_STATUS.md').write_text(report(journal),encoding='utf-8')
    print(f"Shadow records={len(journal['records'])}, errors={len(journal['errors'])}")
    if journal['errors']:raise SystemExit(1)

if __name__=='__main__':main()
