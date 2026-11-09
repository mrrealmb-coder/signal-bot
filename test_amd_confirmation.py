import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
import signal_bot as bot
from amd_confirmation import confirmed_plan


class AMDTests(unittest.TestCase):
    def fixture(self):
        start=int(datetime(2026,11,9,2,tzinfo=ZoneInfo('America/New_York')).timestamp()*1000)
        bars=[dict(ct=start+(i+1)*300000-1,o=100,h=120,l=80,c=100,v=1,_source='binance:XAUTUSDT',_basis=5.) for i in range(-40,60)]
        bars += [dict(ct=start+61*300000-1,o=120,h=125,l=118,c=119,v=1,_source='binance:XAUTUSDT',_basis=5.),dict(ct=start+62*300000-1,o=119,h=120,l=115,c=116,v=1,_source='binance:XAUTUSDT',_basis=5.)]
        return bars

    def signal(self,bars,now):
        with patch.object(bot,'atr',return_value=[2]*len(bars)),patch.object(bot,'sma_trend_fn',return_value=lambda t:-1):
            return bot.amd_signal(bars,[],now)

    def test_no_signal_until_confirmation_closed_then_cap6(self):
        bars=self.fixture();t=bars[-1]['ct']+1
        self.assertIsNone(self.signal(bars,t-1))
        p=self.signal(bars,t+1)
        self.assertEqual(p['entry'],116);self.assertEqual(p['tp'],110)
        self.assertEqual(p['sl'],125.2);self.assertEqual(p['time'],t)
        self.assertEqual(p['setup_time'],t-300000);self.assertEqual(p['_basis'],5.)
        self.assertEqual(p['age'],0)

    def test_sl_before_confirmation_cancels(self):
        bars=self.fixture();bars[-1]['h']=126
        self.assertIsNone(self.signal(bars,bars[-1]['ct']+2))

    def test_basis_change_does_not_trigger_false_sl(self):
        p=dict(dir=-1,entry=100,sl=105,tp=94,time=300000,limit_time=3000000,confirmation_version=1,_source='binance:XAUTUSDT',_basis=5.)
        b=dict(ct=599999,o=106,h=107,l=105,c=106,_source='binance:XAUTUSDT',_basis=2.)
        self.assertEqual(bot.amd_outcome([b],p,400000),{})
        b['h']=109
        self.assertEqual(bot.amd_outcome([b],p,400000)['status'],'sl')

    def test_legacy_plan_stays_legacy(self):
        p=dict(dir=-1,entry=100,sl=105,tp=94,time=300000,limit_time=3000000)
        b=dict(ct=599999,o=104,h=106,l=103,c=105)
        self.assertEqual(bot.amd_outcome([b],p,400000)['status'],'sl')

    def test_buy_confirmation_and_near_tp(self):
        p=dict(id='test',direction=1,time=300000,entry=100,sl=90,tp=110,atr=5,limit_time=3000000)
        bars=[dict(ct=299999,h=101,l=98,c=100),dict(ct=599999,h=104,l=99,c=103)]
        q,reason=confirmed_plan(p,bars,[0,300000])
        self.assertEqual(reason,'confirmed');self.assertEqual(q['tp'],110)
        self.assertEqual(q['risk'],13)

    def test_target_touch_before_confirmation_invalid(self):
        p=dict(id='test',direction=1,time=300000,entry=100,sl=90,tp=120,atr=5,limit_time=3000000)
        bars=[dict(ct=299999,h=101,l=98,c=100),dict(ct=599999,h=121,l=99,c=103)]
        self.assertEqual(confirmed_plan(p,bars,[0,300000])[1],'tp_before_confirmation')


if __name__=='__main__':unittest.main()
