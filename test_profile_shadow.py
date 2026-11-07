import copy
import json
import unittest
from profile_shadow import DAY,MINUTE,enroll,update,tick,profile_levels,report,market_open

T=1793934900000  # Friday Nov 6 2026, 03:15 UTC

def plan():
    return dict(id=f'entry-v1:{T}:1',direction=1,kind='BOS',signal_time=T,
        publish_at=T+MINUTE,expires=T+3600000,entry=100,sl=95,tp=110,
        risk=5,atr=10,lots=.05,basis=0,source='binance:XAUTUSDT',delivered_to={'test':{'message_id':1}})

def prior(price=120):
    day=T//DAY*DAY
    return [dict(ct=t+MINUTE-1,o=price,h=price,l=price,c=price,v=1) for t in range(day-DAY,day,MINUTE)]

def bar(i,o,h,l,c):return dict(ct=T+(i+1)*MINUTE-1,o=o,h=h,l=l,c=c,v=1)

class ShadowTests(unittest.TestCase):
    def test_init_no_backfill_or_fetch(self):
        state={};live=dict(entry_plans={'XAUUSD|15m':dict(active=plan())});original=copy.deepcopy(live)
        tick(state,live,T+5000,lambda *_:self.fail('No fetch during initialization'))
        tick(state,live,T+6000,lambda *_:self.fail('Old signal must be excluded'))
        self.assertEqual(state['records'],{});self.assertEqual(live,original)

    def test_pair_targets_frozen_and_reach_not_censored(self):
        p=plan();original=copy.deepcopy(p);r=enroll(p,prior(),T+1000)
        self.assertEqual(r['profile_status'],'eligible');self.assertEqual(r['profile']['tp'],118.5)
        bars=[bar(0,99,99.9,98,99),bar(1,100,101,99,100),bar(2,104,111,103,110),bar(3,110,121,109,120),bar(4,100,103,94,95)]
        update(r,bars)
        self.assertEqual(r['baseline']['status'],'tp');self.assertEqual(r['profile']['status'],'tp')
        self.assertEqual(r['observer']['status'],'sl');self.assertEqual(r['reach'],21)
        self.assertEqual(p,original);self.assertTrue(r['complete'])
        snapshot=copy.deepcopy(r);update(r,bars);self.assertEqual(r,snapshot)

    def test_basis_normalization(self):
        p=plan();p['basis']=-10
        r=enroll(p,prior(110),T+1000)
        self.assertEqual(r['profile']['tp'],118.5)
        update(r,[bar(0,89,89.9,88,89),bar(1,90,91,89,90)])
        self.assertEqual(r['baseline']['fill'],100)

    def test_no_level_and_too_close_retained(self):
        self.assertEqual(enroll(plan(),prior(90),T)['profile_status'],'no_level')
        self.assertEqual(enroll(plan(),prior(104),T)['profile_status'],'too_close')
        self.assertIsNone(enroll(plan(),prior(104),T)['profile'])

    def test_missing_prior_day_rejected(self):
        with self.assertRaises(ValueError):enroll(plan(),prior()[:-1],T)

    def test_ambiguous_not_win(self):
        r=enroll(plan(),prior(),T)
        update(r,[bar(0,99,99.9,98,99),bar(1,100,101,99,100),bar(2,100,120,94,110)])
        self.assertEqual(r['baseline']['status'],'ambiguous_sl_tp')
        self.assertEqual(r['profile']['status'],'ambiguous_sl_tp')
        self.assertNotIn('gross_r',r['baseline']);self.assertEqual(r['reach'],0)

    def test_fill_minute_sl_zero_reach(self):
        r=enroll(plan(),prior(),T)
        update(r,[bar(0,99,99.9,98,99),bar(1,100,109,94,96)])
        self.assertEqual(r['reach'],0);self.assertTrue(r['reach_complete'])

    def test_gap_fill_known_distance_only(self):
        r=enroll(plan(),prior(),T)
        update(r,[bar(0,99,99.9,98,99),bar(1,101,109,100,105)])
        self.assertEqual(r['reach'],1)

    def test_journal_roundtrip_dedup_and_no_live_mutation(self):
        j={};tick(j,{},T-1000)
        live=dict(entry_plans={'XAUUSD|15m':dict(active=plan(),history=[plan()])});before=copy.deepcopy(live)
        tick(j,live,T+1000,lambda start,end:prior())
        self.assertEqual(len(j['records']),1);self.assertEqual(live,before)
        restored=json.loads(json.dumps(j));tick(restored,live,T+2000,lambda *_:self.fail('Duplicate fetch'))
        self.assertEqual(restored['records'],j['records']);self.assertIn('completed resolved pairs: 0',report(j))

    def test_error_visible_and_retryable(self):
        j={};tick(j,{},T-1000);live=dict(entry_plans={'XAUUSD|15m':dict(active=plan())})
        def broken(*_):raise ValueError('missing')
        tick(j,live,T+1000,broken);self.assertTrue(j['errors']);self.assertFalse(j['records'])
        tick(j,live,T+2000,lambda *_:prior());self.assertFalse(j['errors']);self.assertEqual(len(j['records']),1)

    def test_unacknowledged_signal_excluded(self):
        j={};tick(j,{},T-1000);p=plan();p['delivered_to']={}
        tick(j,dict(entry_plans={'XAUUSD|15m':dict(active=p)}),T+1000,lambda *_:self.fail('No fetch'))
        self.assertFalse(j['records'])

    def test_market_rule_and_empty_volume(self):
        self.assertTrue(market_open(T));self.assertEqual(profile_levels([]),[])

if __name__=='__main__':unittest.main()
