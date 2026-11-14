import copy
import unittest
import tempfile
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
from smc_frozen import freeze,evaluate,select
from signal_bot import smc_setup,smc_hours_ok

H=3600000
def swing_bars(day_start,break_hour):
    """Flat 15m candles with one swing low, then a candle closing below it at `break_hour` UTC."""
    first=day_start+int(break_hour*H)-40*STEP
    rows=[]
    for i in range(60):
        o,h,l,c=100.0,101.0,99.0,100.0
        if i==20:l=97.0
        if i==39:o,h,l,c=100.0,100.5,94.0,95.0
        rows.append(dict(ct=first+(i+1)*STEP-1,o=o,h=h,l=l,c=c,v=1.0))
    return rows

class HoursTests(unittest.TestCase):
    def test_window_is_half_open_and_may_wrap_midnight(self):
        day=20000*86400000
        self.assertTrue(smc_hours_ok(day+7*H,[7,21]));self.assertTrue(smc_hours_ok(day+21*H-1,[7,21]))
        self.assertFalse(smc_hours_ok(day+21*H,[7,21]));self.assertFalse(smc_hours_ok(day+7*H-1,[7,21]))
        self.assertTrue(smc_hours_ok(day+23*H,[21,7]));self.assertTrue(smc_hours_ok(day+3*H,[21,7]))
        self.assertFalse(smc_hours_ok(day+12*H,[21,7]))

    def test_malformed_window_disables_the_filter(self):
        for bad in (None,[],[7],['a','b'],[7,7],[-1,30]):
            self.assertTrue(smc_hours_ok(12*H,bad))

    def test_break_inside_window_is_a_setup_and_outside_is_ignored(self):
        day=20000*86400000
        for hour,expected in ((12,True),(7,True),(21,False),(23,False),(3,False)):
            rows=swing_bars(day,hour);now=rows[-1]['ct']+1
            unfiltered=smc_setup(rows,now,STEP,('BOS',),None)
            self.assertIsNotNone(unfiltered);self.assertEqual(unfiltered['time'],day+hour*H)
            filtered=smc_setup(rows,now,STEP,('BOS',),None,0.0,[7,21])
            self.assertEqual(filtered is not None,expected,hour)
            if expected:self.assertEqual(filtered,unfiltered)

STEP=900000
def setup():return dict(time=STEP,dir=-1,entry=100,sl=110,tp=80,status='pending',expires=STEP*25,kind='BOS')
def bar(i,o,h,l,c,basis=0):
    return dict(ct=(i+1)*STEP-1,o=o-basis,h=h-basis,l=l-basis,c=c-basis,_basis=basis,_source='binance:XAUTUSDT')

class FrozenTests(unittest.TestCase):
    def test_levels_and_outcome_invariant_to_basis(self):
        saved=freeze(setup(),[bar(0,95,99,94,96)])
        a=[bar(1,95,99,90,94),bar(2,99,101,98,100),bar(3,105,111,104,110)]
        b=[dict(x,**{k:x[k]-7 for k in ['o','h','l','c']},_basis=7) for x in a]
        self.assertEqual(evaluate(saved,a,STEP*4,STEP),evaluate(saved,b,STEP*4,STEP))
        r=evaluate(saved,b,STEP*4,STEP)
        self.assertEqual((r['entry'],r['sl'],r['tp'],r['status']),(100,110,80,'sl'))

    def test_active_plan_not_overwritten_by_new_structure(self):
        saved=freeze(setup(),[bar(0,95,99,94,96)])
        rec=dict(status='filled',frozen=saved)
        newer=dict(setup(),time=STEP*2,entry=88,sl=99,tp=66)
        result=select(rec,newer,[bar(1,95,99,90,94)],STEP*2,STEP)
        self.assertEqual(result['time'],STEP);self.assertEqual(result['entry'],100)
        self.assertIsNotNone(select(rec,None,[bar(1,95,99,90,94)],STEP*2,STEP))

    def test_new_setup_can_replace_unfilled_limit_as_before(self):
        saved=freeze(setup(),[bar(0,95,99,94,96)])
        newer=dict(setup(),time=STEP*2)
        self.assertEqual(select(dict(status='pending',frozen=saved),newer,[],STEP*2,STEP),newer)

    def test_research_stale_cancels_before_later_fill(self):
        saved=freeze(setup(),[bar(0,95,99,94,96)])
        rows=[bar(1,95,99,89,90),bar(2,99,101,98,100),bar(3,105,111,104,110)]
        self.assertEqual(evaluate(saved,rows,STEP*4,STEP)['status'],'sl')
        self.assertEqual(evaluate(saved,rows,STEP*4,STEP,stale_r=1)['status'],'stale_cancelled')

    def test_partial_candle_cannot_cancel_stale(self):
        saved=freeze(setup(),[bar(0,95,99,94,96)])
        r=evaluate(saved,[bar(1,95,99,89,90)],STEP+1000,STEP,stale_r=1)
        self.assertEqual(r['status'],'pending')

    def test_entry_and_favorable_same_bar_not_retroactive_cancel(self):
        saved=freeze(setup(),[bar(0,95,99,94,96)])
        r=evaluate(saved,[bar(1,99,101,89,90)],STEP*2,STEP,stale_r=1)
        self.assertEqual(r['status'],'filled')

    def test_rounding_and_nonmutation(self):
        s=dict(setup(),sl=110.123456);original=copy.deepcopy(s)
        f=freeze(s,[bar(0,95,99,94,96)])
        self.assertEqual(f['sl'],110.12);self.assertEqual(s,original)

    def test_missing_history_and_source_fail_closed(self):
        f=freeze(setup(),[bar(0,95,99,94,96)])
        with self.assertRaises(ValueError):evaluate(f,[bar(2,95,99,94,96)],STEP*3,STEP)
        with self.assertRaises(ValueError):evaluate(f,[dict(bar(1,95,99,94,96),_source='other')],STEP*2,STEP)

    def test_terminal_outcome_stable(self):
        f=freeze(dict(setup(),status='sl'),[bar(0,95,99,94,96)])
        self.assertEqual(evaluate(f,[],STEP*30,STEP),f)

    def test_main_persists_and_uses_original_levels_in_feed_and_ledger(self):
        import signal_bot as bot
        t=1793934900000
        state={'started':True,'chat_ids':['test'],'owner':'test',**{k:True for k in ('smc_announced','vp_announced','day_announced','mk2_announced','mk3_announced')}}
        config={'watch':[{'pair':'XAUUSD','tf':'15m','ema':False,'smc':True}]}
        original=dict(setup(),time=t,expires=t+24*STEP,level=105,level_time=t-STEP,ob=(100,108),ob_time=t-STEP,fvg=None)
        def invoke(now,basis,candidate):
            rows=[]
            for i in range(-100,3):
                op=t+i*STEP
                if op>now:break
                high=99 if i<=0 else (101 if i==1 else 112)
                rows.append(dict(ct=op+STEP-1,o=98-basis,h=high-basis,l=97-basis,c=98-basis,v=1,_source='binance:XAUTUSDT',_basis=basis))
            with tempfile.TemporaryDirectory() as scratch,ExitStack() as stack:
                stack.enter_context(patch.dict(bot.os.environ,{'TELEGRAM_TOKEN':'fake'}))
                stack.enter_context(patch.object(bot,'load_json',side_effect=lambda path,default:config if path==bot.CONFIG_FILE else state if path==bot.STATE_FILE else {}))
                stack.enter_context(patch.object(bot,'save_state'))
                stack.enter_context(patch.object(bot,'FETCH_FILE',str(Path(scratch)/'fetch.json')))
                stack.enter_context(patch.object(bot.time,'time',return_value=now/1000))
                stack.enter_context(patch.object(bot,'tg',return_value={'result':[]}))
                stack.enter_context(patch.object(bot,'try_send',return_value=True))
                stack.enter_context(patch.object(bot,'gold_basis',return_value={}))
                stack.enter_context(patch.object(bot,'fetch_bars',return_value=rows))
                stack.enter_context(patch.object(bot,'smc_setup',return_value=candidate))
                stack.enter_context(patch.object(bot,'render_chart',return_value=None))
                stack.enter_context(patch.object(bot,'smc_message',return_value='test setup'))
                self.assertEqual(bot.main(),0)
        invoke(t+5000,0,original)
        self.assertEqual(state['smc']['XAUUSD|15m']['frozen']['sl'],110)
        invoke(t+2*STEP+5000,7,None)
        item=next(x for x in state['feed']['signals'] if x['method']=='smc')
        self.assertEqual((item['entry'],item['sl'],item['tp'],item['exit']),(100,110,80,110))
        self.assertEqual(state['smc']['XAUUSD|15m']['status'],'sl')
        self.assertEqual(state['history'][-1]['exit'],110)

if __name__=='__main__':unittest.main()
