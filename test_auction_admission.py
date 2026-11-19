import unittest
from unittest.mock import Mock
from auction_admission import check_entry
from signal_bot import update_text


class AuctionTests(unittest.TestCase):
    def test_only_complete_decisions_are_reused(self):
        cut = 1800000000000
        signal = dict(time=cut, entry=100, sl=101, tp=98, dir=-1)
        state = {}
        reader = Mock(side_effect=[
            dict(verdict='wait', pattern='data', cut=cut),
            dict(verdict='block', pattern='reclaim', cut=cut)])
        for _ in range(3):
            self.assertFalse(check_entry(state, 'smc', 'XAUUSD|15m', signal, cut, reader))
        self.assertEqual(reader.call_count, 2)
        config = {'watch': [dict(pair='XAUUSD', tf='15m', ema=False)]}
        msg = update_text(config, state, cut, lambda w, m: ([{'ct': cut-1, 'c': 100}], signal, 1))
        self.assertIn('эсрэг хөдөлгөөн', msg)
        self.assertNotIn('Entry ', msg)

    def test_late_market_setup_never_runs_reader(self):
        reader = Mock()
        signal = dict(time=600000, dir=1, entry=100, sl=99, tp=102)
        self.assertFalse(check_entry({}, 'amd', 'XAUUSD|5m', signal, 901000, reader))
        reader.assert_not_called()
