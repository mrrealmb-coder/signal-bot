import copy
import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bot', pathlib.Path(__file__).with_name('signal_bot.py'))
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)


class UpdateTests(unittest.TestCase):
    def test_owner_and_approved_group_queue_once_without_changing_roles(self):
        state = {'owner': '1', 'chat_ids': ['1', '-2'], 'chat_roles': {'-2': 'scalp'}}
        messages = [(1, '/update'), (-2, '/update@my_bot'), (-2, '/update')]
        updates = {'result': [{'update_id': i, 'message': {'chat': {'id': cid}, 'from': {'id': 1}, 'text': text}}
                              for i, (cid, text) in enumerate(messages)]}
        with patch.object(bot, 'tg', return_value=updates), patch.object(bot, 'try_send') as send:
            bot.sync_chats('fake', state)
        self.assertEqual(state['update_requests'], ['1', '-2'])
        self.assertEqual(state['chat_roles'], {'-2': 'scalp'})
        send.assert_not_called()

    def test_unapproved_chat_cannot_read_status_or_auto_enroll(self):
        state = {'owner': '1', 'chat_ids': ['1']}
        updates = {'result': [{'update_id': 1, 'message': {'chat': {'id': 9}, 'text': '/update'}}]}
        with patch.object(bot, 'tg', return_value=updates), patch.object(bot, 'try_send') as send:
            bot.sync_chats('fake', state)
        self.assertNotIn('update_requests', state)
        self.assertEqual(state['chat_ids'], ['1'])
        self.assertNotIn('9', state['pending'])
        self.assertIn('зөвшөөрсөн', send.call_args.args[2])

    def test_no_signal_and_direction_are_explained(self):
        now = 1793800000000
        config = {'watch': [{'pair': 'XAUUSD', 'tf': '15m', 'ema': False, 'smc': True}]}
        text = bot.update_text(config, {}, now, lambda w, m: ([{'ct': now - 1, 'c': 4000}], None, -1))
        self.assertIn('Шинэ дохио гараагүй', text)
        self.assertIn('буурах', text)
        self.assertIn('4,000.00', text)

    def test_data_failure_is_not_reported_as_no_signal(self):
        def fail(w, m):
            raise RuntimeError('secret-token-must-not-leak')
        text = bot.update_text({'watch': [{'pair': 'XAUUSD', 'tf': '15m', 'ema': False}]}, {}, 1793800000000, fail)
        self.assertIn('баталж чадсангүй', text)
        self.assertNotIn('Шинэ дохио гараагүй', text)
        self.assertNotIn('secret-token', text)

    def test_stale_data_does_not_claim_current_signal(self):
        now = 1793800000000
        text = bot.update_text({'watch': [{'pair': 'XAUUSD', 'tf': '15m', 'ema': False}]}, {}, now,
                               lambda w, m: ([{'ct': now - 86400000, 'c': 4000}], None, 1))
        self.assertIn('Өгөгдөл хуучирсан', text)
        self.assertNotIn('Шинэ дохио гараагүй', text)

    def test_pending_signal_includes_levels_without_mutating_it(self):
        now = 1793800000000
        signal = {'time': now - 1000, 'dir': 1, 'status': 'pending', 'entry': 4000, 'sl': 3990, 'tp': 4020}
        before = copy.deepcopy(signal)
        text = bot.update_text({'watch': [{'pair': 'XAUUSD', 'tf': '15m', 'ema': False}]}, {}, now,
                               lambda w, m: ([{'ct': now - 1, 'c': 4001}], signal, 1))
        self.assertIn('Limit хүлээгдэж', text)
        self.assertIn('Entry 4,000.00 · SL 3,990.00 · TP 4,020.00', text)
        self.assertEqual(signal, before)

    def test_main_checks_all_four_methods_and_replies_only_to_requesting_chat(self):
        from contextlib import ExitStack
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        now = 1793800000000
        config = {'watch': [
            {'pair': 'XAUUSD', 'tf': '15m', 'ema': False, 'smc': True, 'smc_htf': '1h', 'smc_sma': True, 'va80': True},
            {'pair': 'XAUUSD', 'tf': '1h', 'ema': False, 'smc': False, 'vp': True},
            {'pair': 'XAUUSD', 'tf': '5m', 'ema': False, 'smc': False, 'amd': True}]}
        state = {'owner': '1', 'chat_ids': ['1', '-2'], 'chat_roles': {'-2': 'scalp'},
                 **{k: True for k in ('started', 'smc_announced', 'vp_announced', 'day_announced', 'mk2_announced', 'mk3_announced')}}
        updates = {'result': [{'update_id': 1, 'message': {'chat': {'id': -2}, 'text': '/update@my_bot'}}]}
        def bars(sym, tf, *args):
            step = bot.TF[tf][0] * 1000
            return [{'ct': now - (300 - i) * step, 'o': 4000, 'h': 4002, 'l': 3998, 'c': 4000, 'v': 1}
                    for i in range(301)]
        def load(path, default):
            return config if path == bot.CONFIG_FILE else state if path == bot.STATE_FILE else {}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(bot.os.environ, {'TELEGRAM_TOKEN': 'fake'}))
            stack.enter_context(patch.object(bot, 'load_json', side_effect=load))
            stack.enter_context(patch.object(bot, 'save_state'))
            stack.enter_context(patch.object(bot, 'FETCH_FILE', str(pathlib.Path(scratch.name) / 'last_fetch.json')))
            stack.enter_context(patch.object(bot.time, 'time', return_value=now / 1000))
            stack.enter_context(patch.object(bot, 'tg', return_value=updates))
            send = stack.enter_context(patch.object(bot, 'try_send', return_value=True))
            stack.enter_context(patch.object(bot, 'gold_basis', return_value={}))
            stack.enter_context(patch.object(bot, 'fetch_bars', side_effect=bars))
            stack.enter_context(patch.object(bot, 'fetch_binance', side_effect=bars))
            for name in ('smc_setup', 'vp_signal', 'amd_signal', 'va80_signal'):
                stack.enter_context(patch.object(bot, name, return_value=None))
            self.assertEqual(bot.main(), 0)
        replies = [call.args for call in send.call_args_list if 'Одоогийн төлөв' in call.args[2]]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0][1], '-2')
        for method in ('SMC', 'Volume Profile', 'AMD', 'VA 80%'):
            self.assertIn(method, replies[0][2])
        self.assertNotIn('баталж чадсангүй', replies[0][2])
        self.assertNotIn('update_requests', state)


if __name__ == '__main__':
    unittest.main()
