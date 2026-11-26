import copy
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import signal_bot as bot


MINUTE = 60_000
CLOSE = 1_794_907_200_000  # XAUT session, 2026-11-17 09:20 UTC


class TelegramM10Integration(unittest.TestCase):
    def setUp(self):
        config = json.loads(Path(bot.CONFIG_FILE).read_text(encoding='utf-8'))
        gold = next(w for w in config['watch'] if (w['pair'], w['tf']) == ('XAUUSD', '15m'))
        self.assertTrue(gold['smc_m10_shadow'])
        self.watch = dict(gold, day=False, dtc=False, va80=False)
        self.config = {'watch': [self.watch]}
        self.state = {
            'started': True, 'smc_announced': True, 'chat_ids': ['test-chat'],
            'chat_roles': {'test-chat': 'all'}, 'smc': {}, 'feed': [], 'ledger': [],
            'report_week': bot.datetime.now(bot.UB).strftime('%G-W%V'),
        }
        self.now = CLOSE - MINUTE
        patches = self.enterContext(ExitStack())
        scratch = patches.enter_context(tempfile.TemporaryDirectory())
        patches.enter_context(patch.dict(os.environ, {'TELEGRAM_TOKEN': 'test', 'TWELVEDATA_KEY': ''}))
        patches.enter_context(patch.dict(bot.BASIS, {}, clear=True))
        patches.enter_context(patch.object(bot, 'FETCH_FILE', str(Path(scratch) / 'fetch.json')))
        patches.enter_context(patch.object(bot, 'load_json', side_effect=self.load))
        self.save = patches.enter_context(patch.object(bot, 'save_state'))
        patches.enter_context(patch.object(bot, 'sync_chats'))
        patches.enter_context(patch.object(bot.time, 'time', side_effect=lambda: self.now / 1000))
        patches.enter_context(patch.object(bot, 'gold_basis', side_effect=self.basis))
        patches.enter_context(patch.object(bot, 'fetch_binance', side_effect=self.bars))
        patches.enter_context(patch.object(bot, 'fetch_bars', side_effect=self.bars))
        self.structure = patches.enter_context(patch.object(bot, 'fetch_closed_xaut_m10', side_effect=self.ten))
        patches.enter_context(patch.object(bot, 'smc_setup', side_effect=self.setup_for_step))
        self.send = patches.enter_context(patch.object(bot, 'try_send'))
        self.photo = patches.enter_context(patch.object(bot, 'try_send_photo'))
        self.http = patches.enter_context(patch.object(bot, 'http_json'))

    def load(self, path, default):
        return {bot.CONFIG_FILE: self.config, bot.STATE_FILE: self.state}.get(path, default)

    def basis(self, cache, minute, key):
        cache['gold_basis'] = {'minute': minute, 'XAUTUSDT': 1.0}
        return cache['gold_basis']

    def bar(self, op, step):
        return dict(ct=op + step - 1, o=105.0, h=106.0, l=104.0, c=105.0, v=20,
                    _source='binance:XAUTUSDT', _basis=1.0, _snapshot_at=self.now)

    def bars(self, symbol, interval, *unused):
        step = bot.TF[interval][0] * 1000
        current = self.now // step * step
        return [self.bar(current - i * step, step) for i in range(330, -1, -1)]

    def ten(self, now, **kwargs):
        step = 10 * MINUTE
        return [self.bar(now // step * step - step, step)]

    def setup_for_step(self, bars, now, step, *args):
        if step == 10 * MINUTE:
            return dict(time=bars[-1]['ct'] + 1, status='pending', dir=1, entry=100.0, sl=98.0, tp=104.0)
        return None

    def poll(self):
        self.assertEqual(bot.main(), 0)
        self.save.assert_called_with(self.state)
        self.send.assert_not_called()
        self.photo.assert_not_called()
        self.http.assert_not_called()
        self.assertEqual(self.state['feed'], [])
        self.assertEqual(self.state['ledger'], [])

    def test_next_close_only_creates_one_shadow_limit_while_primary_is_clear(self):
        self.poll()
        self.structure.assert_called_once()
        self.assertIsNone(self.state['smc_m10_shadow']['active'])
        self.assertEqual(self.state['smc_m10_shadow']['records'], [])
        self.structure.reset_mock()
        self.now = CLOSE + MINUTE + 5_000
        self.poll()
        self.poll()
        self.structure.assert_called_once()
        active = self.state['smc_m10_shadow']['active']
        self.assertEqual((active['time'], active['expires']), (CLOSE, CLOSE + 6 * 60 * MINUTE))
        self.assertEqual(active['status'], 'pending')
        self.assertEqual(self.state['smc'], {})

    def test_published_primary_reserves_the_slot(self):
        for status in ('pending', 'filled'):
            with self.subTest(status=status):
                primary = {'XAUUSD|15m': {'time': CLOSE - 30 * MINUTE, 'status': status, 'sent': True}}
                self.state['smc'] = copy.deepcopy(primary)
                self.state.pop('smc_m10_shadow', None)
                self.now = CLOSE - MINUTE
                self.poll()
                self.now = CLOSE + MINUTE + 5_000
                self.poll()
                journal = self.state['smc_m10_shadow']
                self.assertIsNone(journal['active'])
                self.assertEqual(journal['records'][-1]['status'], 'm15_occupied')
                self.assertEqual(self.state['smc'], primary)

    def test_switch_off_skips_new_observations(self):
        self.watch['smc_m10_shadow'] = False
        self.poll()
        self.now = CLOSE + MINUTE + 5_000
        self.poll()
        self.structure.assert_not_called()
        self.assertNotIn('smc_m10_shadow', self.state)

    def test_source_failure_does_not_release_a_primary_slot(self):
        self.poll()
        self.structure.reset_mock()
        self.now = CLOSE + MINUTE
        normal = self.bars

        def stale(symbol, interval, *unused):
            rows = normal(symbol, interval)
            return rows[:-1] if interval == '1h' else rows

        with patch.object(bot, 'fetch_bars', side_effect=stale):
            self.poll()
        self.structure.assert_not_called()
        journal = self.state['smc_m10_shadow']
        self.assertIsNone(journal['active'])
        self.assertEqual(journal['observation_error']['reason'], 'primary_unavailable')

    def test_temporary_structure_failure_retries_same_close_once_recovered(self):
        self.poll()
        self.now = CLOSE + MINUTE
        self.structure.side_effect = ValueError('missing bar')
        self.poll()
        journal = self.state['smc_m10_shadow']
        self.assertLess(journal['checked_close'], CLOSE)
        self.assertIsNone(journal['active'])
        self.structure.side_effect = self.ten
        self.poll()
        self.assertEqual(journal['active']['time'], CLOSE)
        self.assertNotIn('observation_error', journal)
