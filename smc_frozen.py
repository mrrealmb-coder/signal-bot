"""Freeze published gold SMC levels and replay their existing lifecycle.

No messages/network/state writes. Stale-limit cancellation is research-only.
"""
import copy
import math

def freeze(setup,bars,dec=2):
    last=bars[-1]
    if last.get('_source')!='binance:XAUTUSDT' or not math.isfinite(last.get('_basis',float('nan'))):
        raise ValueError('SMC requires verified XAUT basis')
    saved=copy.deepcopy(setup)
    for k in ('entry','sl','tp'):saved[k]=round(saved[k],dec)
    saved['_frozen_version']=1;saved['_basis']=last['_basis'];saved['_source']=last['_source']
    return saved

def evaluate(saved,bars,now_ms,step_ms,stale_r=None):
    """Same pending/fill/SL-first OHLC convention as legacy SMC.

    Optional stale_r is NEVER enabled by the live caller. For research it cancels
    only on a closed candle wholly before entry, at >=stale_r original risk.
    """
    out=copy.deepcopy(saved)
    if saved.get('status') in ('tp','sl','expired','stale_cancelled'):return out
    rows=[]
    for b in bars:
        if b['ct']+1-step_ms<saved['time']:continue
        if b.get('_source')!=saved['_source'] or not math.isfinite(b.get('_basis',float('nan'))):
            raise ValueError('SMC tracking source or basis unavailable')
        x=dict(b)
        for k in ('o','h','l','c'):x[k]=b[k]+b['_basis']-saved['_basis']
        rows.append(x)
    if not rows or rows[0]['ct']+1-step_ms>saved['time']:
        raise ValueError('SMC tracking history incomplete')
    d=out['dir'];en=out['entry'];stop=out['sl'];tp=out['tp'];risk=abs(en-stop)
    out.update(status='pending',fill_time=None,close_time=None)
    for b in rows:
        op=b['ct']+1-step_ms
        if op>=now_ms:break
        if out['status']=='pending':
            if op>=out['expires']:out['status']='expired';break
            touched=b['l']<=en if d==1 else b['h']>=en
            if not touched:
                favorable=(b['h']-en) if d==1 else (en-b['l'])
                if stale_r is not None and b['ct']<now_ms and favorable>=stale_r*risk:
                    out.update(status='stale_cancelled',close_time=b['ct']+1);break
                continue
            out.update(status='filled',fill_time=op)
            # Fill bar: SL only. OHLC cannot tell whether TP traded before or after the fill; TP from the next bar.
            if b['l']<=stop if d==1 else b['h']>=stop:
                out.update(status='sl',close_time=b['ct']+1);break
            continue
        if b['l']<=stop if d==1 else b['h']>=stop:
            out.update(status='sl',close_time=b['ct']+1);break
        if b['h']>=tp if d==1 else b['l']<=tp:
            out.update(status='tp',close_time=b['ct']+1);break
    if out['status']=='pending' and now_ms>=out['expires']:out['status']='expired'
    return out

def select(rec,candidate,bars,now_ms,step_ms):
    saved=rec.get('frozen') if rec else None
    if saved and (rec.get('status') == 'filled' or candidate and candidate['time']==saved['time']
                  or rec.get('status') == 'pending' and candidate is None):
        return evaluate(saved,bars,now_ms,step_ms)
    return candidate
