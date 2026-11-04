import copy
import json
import unittest
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import entry_live as live
from entry_plans import build_plan, advance, HOUR, MINUTE
import signal_bot as bot

T = 60 * 900000


def event(direction=1):
    return dict(time=T, direction=direction, atr=2., level=101. if direction == 1 else 103.,
                kind='BOS', body_atr=2, directional_close=.9, ema_slope_aligned=True, daily_direction=-1)


def trigger():
    return dict(ct=T-1, o=100, h=105, l=99, c=104, _source=live.SOURCE, _basis=5.)


def minute(start, o=104, h=104.5, l=103.5, c=104):
    return dict(ct=start+MINUTE-1, o=o, h=h, l=l, c=c, _source=live.SOURCE, _basis=5.)


class PlanTests(unittest.TestCase):
    def plan(self, direction=1):
        plan, reason = build_plan(event(direction), trigger())
        self.assertEqual(reason, 'ok')
        return plan

    def armed(self):
        plan = self.plan()
        advance(plan, minute(T), lambda _: True)
        return plan

    def test_directional_rounding_tp_and_budget_never_round_up(self):
        for direction in (1, -1):
            p = self.plan(direction)
            self.assertGreater((p['entry']-p['sl'])*direction, 0)
            self.assertAlmostEqual((p['tp']-p['entry'])*direction, 2*p['risk'])
            self.assertLessEqual(p['modeled_risk_usd'], 30)
            self.assertLessEqual(p['lots']*100*(p['risk']+.60), 30+1e-10)
            self.assertGreater((p['lots']+.01)*100*(p['risk']+.60), 30)

    def test_rejects_minimum_lot_over_budget_and_bad_inputs(self):
        p, reason = build_plan(event(), trigger(), risk_usd=.1)
        self.assertIsNone(p)
        self.assertEqual(reason, 'minimum_lot_over_budget')
        for change in ({'atr': float('nan')}, {'atr': -1}, {'direction': 0}, {'level': 110}):
            p, _ = build_plan(dict(event(), **change), trigger())
            self.assertIsNone(p)

    def test_before_activation_cross_cannot_count_as_fill(self):
        p = self.plan()
        advance(p, minute(T, h=106), lambda _: True)
        self.assertEqual(p['status'], 'crossed_before_publication')
        self.assertNotIn('fill', p)

    def test_entry_then_tp_requires_post_activation_path(self):
        p = self.armed()
        advance(p, minute(T+MINUTE, h=106), lambda _: True)
        self.assertEqual(p['status'], 'filled')
        advance(p, minute(T+2*MINUTE, o=106, h=115, l=105, c=114), lambda _: True)
        self.assertEqual(p['status'], 'tp')
        self.assertAlmostEqual(p['gross_r'], 2)

    def test_sl_before_entry_cancels_and_both_hit_stays_ambiguous(self):
        for h, wanted in ((104.5, 'cancelled'), (106, 'ambiguous_entry_sl')):
            p = self.armed()
            advance(p, minute(T+MINUTE, h=h, l=100), lambda _: True)
            self.assertEqual(p['status'], wanted)
            self.assertNotIn('gross_r', p)

    def test_filled_both_hit_is_ambiguous_and_duplicate_bar_is_noop(self):
        p = self.armed()
        advance(p, minute(T+MINUTE, h=106), lambda _: True)
        old = copy.deepcopy(p)
        advance(p, minute(T+MINUTE, h=200, l=1), lambda _: True)
        self.assertEqual(p, old)
        advance(p, minute(T+2*MINUTE, o=106, h=115, l=100), lambda _: True)
        self.assertEqual(p['status'], 'ambiguous_sl_tp')
        self.assertNotIn('gross_r', p)
        self.assertLess(p['r_bounds'][0], 0)
        self.assertGreater(p['r_bounds'][1], 0)

    def test_gapped_entry_and_stop_use_worse_open(self):
        p = self.armed()
        advance(p, minute(T+MINUTE, o=107, h=108, l=106, c=107), lambda _: True)
        self.assertEqual(p['fill'], 107)
        advance(p, minute(T+2*MINUTE, o=99, h=100, l=98, c=99), lambda _: True)
        self.assertEqual(p['exit'], 99)
        self.assertLess(p['gross_r'], -1)

    def test_pending_expiry_and_six_hour_hold(self):
        p = self.armed()
        for n in range(1, 61):
            advance(p, minute(T+n*MINUTE), lambda _: True)
        self.assertEqual(p['status'], 'expired')
        p = self.armed()
        advance(p, minute(T+MINUTE, h=106), lambda _: True)
        for n in range(2, 361):
            advance(p, minute(T+n*MINUTE, o=106, h=107, l=105.5, c=106), lambda _: True)
        self.assertEqual(p['status'], 'time_exit')
        self.assertEqual(p['ended_at'], p['hold_until'])

    def test_missing_minute_is_not_silently_bridged(self):
        p = self.armed()
        advance(p, minute(T+2*MINUTE, h=120), lambda _: True)
        self.assertEqual(p['status'], 'data_gap')
        self.assertNotIn('gross_r', p)

    def test_friday_close_exits_filled_and_cancels_pending(self):
        for filled in (False, True):
            p = self.armed()
            b = minute(T+MINUTE, h=106 if filled else 104.5)
            advance(p, b, lambda t: t < T+2*MINUTE)
            self.assertEqual(p['status'], 'time_exit' if filled else 'cancelled_market_close')

    def test_sell_path_is_symmetric(self):
        buy = self.plan()
        sell = dict(buy, direction=-1, entry=-buy['entry'], sl=-buy['sl'], tp=-buy['tp'])
        for b in (minute(T), minute(T+MINUTE, h=106), minute(T+2*MINUTE, o=106, h=115, l=105, c=114)):
            mirror = dict(b, o=-b['o'], h=-b['l'], l=-b['h'], c=-b['c'])
            advance(buy, b, lambda _: True)
            advance(sell, mirror, lambda _: True)
        self.assertEqual(buy['status'], sell['status'])
        self.assertAlmostEqual(buy['gross_r'], sell['gross_r'])


class LiveTests(unittest.TestCase):
    def run_engine(self, slot, now=T+1000, bars=None, mins=None, send=None, save=None, **kwargs):
        with patch.object(live, 'latest_break', return_value=event()):
            return live.run_entry(slot, bars if bars is not None else [trigger()],
                                  mins if mins is not None else [minute(T)], now,
                                  ['day', 'scalp', 'swing'], send or Mock(return_value={'ok': True, 'result': {'message_id': 7}}),
                                  save or Mock(), atr_fn=bot.atr, ema_fn=bot.ema, market_open=lambda _: True,
                                  basis=5., basis_fresh=kwargs.pop('basis_fresh', True), **kwargs)

    def initialized(self):
        slot = {}
        self.run_engine(slot, now=T-900000+1000, bars=[dict(trigger(), ct=T-900000-1)])
        return slot

    def test_first_activation_does_not_backfill(self):
        slot = {}
        send = Mock()
        self.run_engine(slot, send=send)
        send.assert_not_called()
        self.assertEqual(slot['status'], 'initialized')

    def test_setup_has_levels_and_is_deduplicated_after_restart(self):
        slot = self.initialized()
        send = Mock(return_value={'ok': True, 'result': {'message_id': 7}})
        self.run_engine(slot, send=send)
        self.assertEqual(send.call_count, 3)
        msg = bot.entry_message('setup', slot['active'])
        for text in ('Entry:', 'SL:', 'TP:', 'Лот:', 'Идэвхжих:', 'Оролт хүчинтэй:', 'BUY STOP'):
            self.assertIn(text, msg)
        slot = json.loads(json.dumps(slot))
        self.run_engine(slot, now=T+2000, send=send)
        self.assertEqual(send.call_count, 3)
        self.assertEqual(len(slot['active']['delivered_to']), 3)

    def test_ack_required_attempt_persisted_before_network_retry_only_failed(self):
        slot = self.initialized()
        saved, calls = [], []
        def save():
            saved.append(copy.deepcopy(slot))
        def send(cid, kind, plan):
            calls.append(cid)
            attempt = saved[-1]['outbox'][plan['id']+':setup']['recipients'][cid]
            self.assertGreater(attempt['attempts'], 0)
            if cid == 'scalp' and calls.count(cid) == 1:
                return {'ok': True, 'result': {}}
            return {'ok': True, 'result': {'message_id': 5}}
        self.run_engine(slot, send=send, save=save)
        self.assertEqual(len(slot['active']['delivered_to']), 2)
        self.run_engine(slot, now=T+17000, send=send, save=save)
        self.assertEqual(calls, ['day', 'scalp', 'swing', 'scalp'])
        self.assertEqual(len(slot['active']['delivered_to']), 3)

    def test_no_late_setup_retry_and_no_closure_to_unacknowledged_chat(self):
        slot = self.initialized()
        calls = []
        def send(cid, kind, plan):
            calls.append((cid, kind))
            if cid == 'scalp':
                raise TimeoutError('never-log-this-secret')
            return {'ok': True, 'result': {'message_id': 1}}
        self.run_engine(slot, send=send)
        self.run_engine(slot, now=T+MINUTE+1000, mins=[minute(T, h=106)], send=send)
        self.assertEqual(calls, [('day', 'setup'), ('scalp', 'setup'), ('swing', 'setup'),
                                 ('day', 'closed'), ('swing', 'closed')])
        self.assertNotIn('never-log', json.dumps(slot))
        self.assertFalse(slot['active'])

    def test_current_cross_stale_basis_and_late_detection_do_not_send(self):
        for kwargs, reason in ((dict(mins=[minute(T, h=106)]), 'crossed_before_publication'),
                               (dict(basis_fresh=False), 'basis_stale'),
                               (dict(now=T+45000), 'late_detection'),
                               (dict(mins=[]), 'missing_current_quote')):
            slot = self.initialized()
            send = Mock()
            self.run_engine(slot, send=send, **kwargs)
            self.assertEqual(slot['status'], reason)
            send.assert_not_called()

    def test_request_started_before_candle_close_cannot_confirm_that_candle(self):
        slot = self.initialized()
        send = Mock()
        previous = dict(trigger(), ct=T-900000-1)
        incomplete = dict(trigger(), _snapshot_at=T-1000)
        self.run_engine(slot, bars=[previous, incomplete], send=send)
        send.assert_not_called()
        self.assertEqual(slot['watermark'], T-900000)

    def test_source_mismatch_and_basis_change(self):
        b = minute(T)
        shifted = dict(b, **{key: b[key]-10 for key in ('o', 'h', 'l', 'c')}, _basis=15.)
        self.assertEqual(live.fixed_bars([shifted], 5.)[0]['h'], b['h'])
        with self.assertRaises(ValueError):
            live.fixed_bars([dict(b, _source='twelvedata')], 5.)
        slot = self.initialized()
        self.run_engine(slot)
        self.run_engine(slot, now=T+MINUTE+1000, mins=[shifted])
        self.assertEqual(slot['active']['status'], 'pending')

    def test_slow_delivery_stops_remaining_setup_sends_before_activation(self):
        slot = self.initialized()
        clock = [T+1000]
        def send(*args):
            clock[0] += MINUTE
            return {'ok': True, 'result': {'message_id': 1}}
        sender = Mock(side_effect=send)
        self.run_engine(slot, send=sender, clock_ms=lambda: clock[0])
        self.assertEqual(sender.call_count, 1)

    def test_disabled_method_still_manages_existing_plan(self):
        slot = self.initialized()
        self.run_engine(slot)
        self.run_engine(slot, now=T+MINUTE+1000, mins=[minute(T, h=106)], enabled=False)
        self.assertFalse(slot['active'])
        self.assertEqual(slot['history'][-1]['status'], 'crossed_before_publication')

    def test_update_shows_levels_delivery_and_staleness(self):
        slot = self.initialized()
        self.run_engine(slot)
        text = bot.entry_status(slot, T+1000000)
        self.assertIn('Entry', text)
        self.assertIn('3 чат', text)
        self.assertIn('хуучирсан', text)

    def test_binance_carries_basis_without_changing_ohlc(self):
        raw = [[0, '110', '112', '108', '111', '20', 59999]]
        with patch.dict(bot.BASIS, {'XAUTUSDT': 5.}), patch.object(bot, 'http_json', return_value=raw):
            bar = bot.fetch_binance('XAUTUSDT', '1m')[0]
        self.assertEqual([bar[k] for k in ('o', 'h', 'l', 'c')], [105,107,103,106])
        self.assertEqual(bar['_basis'], 5.)
        self.assertEqual(bar['_source'], live.SOURCE)

    def test_main_entry_initializes_without_backfill_or_legacy_ledger_mutation(self):
        now = 1793800800000+1000
        config = {'watch': [{'pair':'XAUUSD','tf':'15m','ema':False,'smc':False,'entry_plan':True}]}
        state = {'owner':'owner','chat_ids':['owner','day','swing','scalp'],
                 'chat_roles':{'owner':'off','day':'day','swing':'swing','scalp':'scalp'},
                 'smc':{'old':{'status':'pending'}},'vp':{'old':{'status':'open'}},
                 'history':[{'sentinel':True}], 'feed':[{'sentinel':True}],
                 **{k:True for k in ('started','smc_announced','vp_announced','day_announced','mk2_announced','mk3_announced')}}
        before = copy.deepcopy(state)
        bars = [dict(ct=now-1001-(60-i)*900000,o=100,h=101,l=99,c=100,v=1,
                     _source=live.SOURCE, _basis=5.) for i in range(61)]
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.dict(bot.os.environ, {'TELEGRAM_TOKEN':'offline-test'}))
            stack.enter_context(patch.object(bot,'load_json',side_effect=lambda path,default:config if path==bot.CONFIG_FILE else state if path==bot.STATE_FILE else {}))
            stack.enter_context(patch.object(bot,'save_state'))
            stack.enter_context(patch.object(bot,'FETCH_FILE',str(Path(directory)/'fetch.json')))
            stack.enter_context(patch.object(bot.time,'time',return_value=now/1000))
            tg = stack.enter_context(patch.object(bot,'tg',return_value={'ok':True,'result':[]}))
            stack.enter_context(patch.object(bot,'gold_basis',return_value={}))
            stack.enter_context(patch.object(bot,'fetch_bars',return_value=bars))
            stack.enter_context(patch.object(bot,'fetch_binance',return_value=bars))
            stack.enter_context(patch.object(bot.urllib.request,'urlopen',side_effect=AssertionError('network forbidden')))
            self.assertEqual(bot.main(), 0)
        self.assertEqual(state['entry_plans']['XAUUSD|15m']['status'],'initialized')
        for key in ('smc','vp','history','feed'):
            self.assertEqual(before[key], state[key])
        self.assertFalse(any(c.args[1]=='sendMessage' for c in tg.call_args_list))


if __name__ == '__main__':
    unittest.main()
