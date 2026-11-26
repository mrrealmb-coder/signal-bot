import copy
import unittest
from unittest.mock import patch

import smc_m10_shadow as shadow


M = shadow.MINUTE
T = 400 * M


def bar(t, low=101., high=104., opening=102., basis=2., asof=None):
    return dict(ct=t+M-1, o=opening, c=opening, l=low, h=high, _basis=basis,
                _source=shadow.SOURCE, _snapshot_at=t+M if asof is None else asof)


def setup(t=T, **changes):
    return dict(dict(time=t, dir=1, entry=100.004, sl=98., tp=104., status='pending',
                     _source=shadow.SOURCE, _basis=2.), **changes)


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.journal = {}
        self.observe(None, [], T-1)

    def observe(self, candidate, bars, now, primary='clear'):
        shadow.tick(self.journal, candidate, bars, now, primary, lambda t: True)

    def publish(self):
        self.observe(setup(), [bar(T, asof=T+1)], T+1)
        self.assertEqual(self.journal['active']['entry'], 100.)

    def test_no_bootstrap_backfill_and_no_idle_mutations(self):
        j = {}
        shadow.tick(j, setup(), [bar(T, asof=T+1)], T+1, 'clear', lambda t: True)
        self.assertIsNone(j['active'])
        before = copy.deepcopy(j)
        shadow.tick(j, setup(), [bar(T, asof=T+2)], T+2, 'clear', lambda t: True)
        self.assertEqual(before, j)

    def test_prospective_rounded_limit_and_quote_required(self):
        self.observe(setup(), [bar(T, 100., asof=T+1)], T+1)
        self.assertEqual(self.journal['records'][-1]['status'], 'crossed_before_observation')
        self.observe(setup(T+10*M), [bar(T+10*M, asof=T+10*M-1)], T+10*M+1)
        self.assertEqual(self.journal['records'][-1]['status'], 'missing_observation_quote')

    def test_observation_minute_unresolved_and_retry_deduplicated(self):
        self.publish()
        self.observe(setup(), [bar(T, low=99., asof=T+20)], T+20)
        self.assertEqual(self.journal['records'][0]['status'], 'observation_minute_touch')
        self.assertNotIn('net_r', self.journal['records'][0])
        self.observe(setup(), [], T+4*M)
        self.assertEqual(len(self.journal['records']), 1)

    def test_rebased_tp_uses_frozen_levels_and_two_costs(self):
        self.publish()
        # Basis rose by one: adjusted low 99 now corresponds to frozen 100.
        bars = [bar(T), bar(T+M, 99., 104., basis=3.),
                bar(T+2*M, 100., 103., basis=3.), bar(T+3*M, asof=T+3*M)]
        self.observe(None, bars, T+3*M)
        trade, = self.journal['records']
        self.assertEqual((trade['status'], trade['fill_bar'], trade['exit_bar']), ('tp', T+M, T+2*M))
        self.assertAlmostEqual(trade['net_r'], 1.7)
        self.assertAlmostEqual(trade['stress_r'], 1.55)
        self.assertEqual(trade['fill_seen_at'], T+3*M)

    def test_sl_first_gap_and_sell(self):
        s = setup(dir=-1, entry=110., sl=112., tp=106.)
        self.observe(s, [bar(T, asof=T)], T)
        bars = [bar(T), bar(T+M, low=105., high=114., opening=113.), bar(T+2*M, asof=T+2*M)]
        self.observe(None, bars, T+2*M)
        trade, = self.journal['records']
        self.assertEqual((trade['status'], trade['exit_price']), ('sl', 113.))
        self.assertAlmostEqual(trade['net_r'], -1.8)

    def test_m15_preempts_only_pending_and_consumes_conflicting_setup(self):
        self.publish()
        self.observe(setup(T+M), [bar(T), bar(T+M, asof=T+M+5)], T+M+5, 'pending')
        self.assertEqual([r['status'] for r in self.journal['records']], ['superseded_by_m15', 'm15_occupied'])
        self.observe(setup(T+M), [], T+2*M)
        self.assertIsNone(self.journal['active'])

    def test_filled_position_survives_m15_and_retains_first_overlap(self):
        self.publish()
        ts = T+M+5
        self.observe(None, [bar(T), bar(T+M, low=99., asof=ts)], ts, 'pending')
        self.assertEqual(self.journal['active']['status'], 'filled')
        self.assertEqual(self.journal['active']['first_m15_overlap_at'], ts)
        self.observe(None, [bar(T+M, low=99., asof=ts+1)], ts+1, 'filled')
        self.assertEqual(self.journal['active']['first_m15_overlap_at'], ts)
        self.observe(None, [bar(T+M, low=99.), bar(T+2*M), bar(T+3*M, asof=T+3*M)], T+3*M)
        self.assertEqual(self.journal['records'][0]['status'], 'tp')

    def test_unverified_priority_is_never_assumed_flat(self):
        self.observe(setup(), [bar(T, asof=T+1)], T+1, 'unknown')
        self.assertEqual(self.journal['records'][0]['status'], 'primary_unknown')
        self.assertIsNone(self.journal['active'])

    def test_known_missing_minute_is_unresolved(self):
        self.publish()
        self.observe(None, [bar(T+M, low=97.), bar(T+2*M, asof=T+2*M)], T+2*M)
        self.assertEqual(self.journal['records'][0]['status'], 'data_gap')
        self.assertNotIn('net_r', self.journal['records'][0])

    def test_closed_market_minutes_do_not_create_missing_fills(self):
        self.publish()
        current = T + 20*M
        shadow.tick(self.journal, None, [bar(T), bar(current, asof=current)], current,
                    'clear', lambda t: t == T or t >= current)
        self.assertEqual(self.journal['active']['status'], 'pending')

    def test_six_hour_deadline_is_fixed_to_signal_not_observation(self):
        now = T+M+1
        self.observe(setup(), [bar(T), bar(T+M, asof=now)], now)
        end = T+shadow.TTL
        bars = [bar(t) for t in range(T+M, end, M)] + [bar(end, low=97., asof=end)]
        self.observe(None, bars, end)
        result, = self.journal['records']
        self.assertEqual((result['status'], result['expires']), ('expired', end))
        self.assertNotIn('net_r', result)

    def test_cannot_use_earlier_partial_snapshot_as_closed_history(self):
        self.publish()
        self.observe(None, [bar(T, asof=T+10), bar(T+M, low=97., asof=T+M)], T+M)
        self.assertEqual(self.journal['records'][0]['status'], 'data_gap')

    def test_bounded_history_with_monotone_setup_watermark(self):
        with patch.object(shadow, 'HISTORY_LIMIT', 2):
            for op in (T, T+M, T+2*M):
                self.observe(setup(op), [], op, 'unknown')
        self.assertEqual([r['time'] for r in self.journal['records']], [T+M, T+2*M])
        self.observe(setup(T), [], T+3*M)
        self.assertEqual(self.journal['last_setup'], T+2*M)


if __name__ == '__main__':
    unittest.main()
