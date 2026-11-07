import copy
import unittest
from breakout_paper import tick,STEP,MINUTE,status

T=1793934900000

def source(start,end):
    return [dict(ct=t+MINUTE-1,o=100,h=101,l=99,c=100,v=1) for t in range(start,end,MINUTE)]

def detector(bars,t,**kw):
    assert bars[-1]['ct']+1==t
    return dict(direction=1,atr=2,level=98,time=t,kind='BOS',body_atr=1,directional_close=.9,ema_slope_aligned=True)

class PaperTests(unittest.TestCase):
    def test_init_ignores_past_without_live_state(self):
        j={};tick(j,T+1,lambda *_:self.fail('No fetch at init'))
        self.assertEqual(j['watermark'],T);self.assertEqual(j['records'],{})

    def test_generates_without_telegram_and_deduplicates(self):
        j={};tick(j,T+1)
        tick(j,T+STEP+MINUTE+1,source,detector)
        self.assertEqual(len(j['records']),1)
        r=next(iter(j['records'].values()))
        self.assertEqual(r['origin'],'independent_paper');self.assertEqual(r['baseline']['basis'],0)
        before=copy.deepcopy(j['records']);tick(j,T+STEP+MINUTE+1,source,detector)
        self.assertEqual(j['records'],before)
        self.assertIn('Independently detected paper setups: 1',status(j))

    def test_does_not_advance_active_trade_into_future_before_decision(self):
        j={};tick(j,T+1)
        def filled_source(start,end):
            rows=source(start,end)
            for x in rows:
                if x['ct']+1-MINUTE>=T+STEP+MINUTE:x.update(h=101.2,c=101)
            return rows
        tick(j,T+5*STEP+MINUTE+1,filled_source,detector)
        self.assertEqual(list(j['decisions'].values()),['paper_setup','blocked_active_plan'])

    def test_missing_minutes_do_not_advance_watermark(self):
        j={};tick(j,T+1)
        with self.assertRaises(ValueError):tick(j,T+STEP+1,lambda a,b:source(a,b)[1:],detector)
        self.assertEqual(j['watermark'],T)

    def test_future_candle_does_not_change_first_plan(self):
        a={};b={};tick(a,T+1);tick(b,T+1)
        tick(a,T+STEP+MINUTE+1,source,detector)
        def changed(start,end):
            bars=source(start,end)
            for x in bars:
                if x['ct']+1-MINUTE>=T+STEP+MINUTE:x.update(o=500,h=501,l=499,c=500)
            return bars
        tick(b,T+2*STEP+1,changed,detector)
        p=next(iter(a['records'].values()))['baseline'];q=next(iter(b['records'].values()))['baseline']
        for key in ['entry','sl','tp','signal_time','publish_at']:self.assertEqual(p[key],q[key])

if __name__=='__main__':unittest.main()
