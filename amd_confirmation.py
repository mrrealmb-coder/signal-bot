import bisect


def confirmed_plan(p,bars,times):
    i=bisect.bisect_left(times,p['time'])-1
    if i<0 or times[i]!=p['time']-300000:return None,'missing_signal_bar'
    d=p['direction'];level=bars[i]['h'] if d==1 else bars[i]['l']
    for j in range(i+1,min(i+4,len(bars))):
        b=bars[j]
        if times[j]!=p['time']+(j-i-1)*300000:return None,'data_gap'
        if min(d*b['h'],d*b['l'])<=d*p['sl']:return None,'sl_before_confirmation'
        if max(d*b['h'],d*b['l'])>=d*p['tp']:return None,'tp_before_confirmation'
        if d*(b['c']-level)<=0:continue
        e=b['c'];risk=d*(e-p['sl']);room=d*(p['tp']-e);t=b['ct']+1
        if risk<=0 or risk>6*p['atr'] or room<=.5*risk:return None,'insufficient_room'
        if t+60000>=p['limit_time']:return None,'expired'
        return dict(p,id=p['id']+':confirm',time=t,entry=e,risk=risk,original_time=p['time']),'confirmed'
    return None,'timeout'
