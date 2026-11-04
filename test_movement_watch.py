import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import signal_bot as bot
import movement_watch as watch


class WatchTests(unittest.TestCase):
    def bars(self,count=60,breaking=False):
        bars=[dict(ct=(i+1)*900000-1,o=100,h=101,l=99,c=100,v=1) for i in range(count)]
        if breaking:bars[-1].update(o=100,h=105,l=100,c=105)
        return bars

    def run_engine(self,slot,bars,now,send=None,save=None,recipients=None,**extra):
        return watch.run_watch(slot,bars,now,['day','scalp','swing'] if recipients is None else recipients,
                               send or Mock(return_value={'ok':True,'result':{'message_id':10}}),
                               save or Mock(),atr_fn=bot.atr,ema_fn=bot.ema,daily_direction=-1,**extra)

    def initialized(self):
        slot={};self.run_engine(slot,self.bars(),60*900000+1000)
        return slot

    def test_activation_does_not_send_existing_break(self):
        slot={};send=Mock()
        self.run_engine(slot,self.bars(breaking=True),60*900000+1000,send)
        self.assertEqual(slot['status'],'initialized');send.assert_not_called()

    def test_closed_new_break_reaches_all_roles_and_deduplicates_after_restart(self):
        slot=self.initialized();send=Mock(return_value={'ok':True,'result':{'message_id':10}})
        bars=self.bars(61,True);now=61*900000+1000
        self.run_engine(slot,bars,now,send)
        self.assertEqual(send.call_count,3)
        self.assertEqual(slot['last_alert']['acknowledged'],3)
        slot=json.loads(json.dumps(slot))
        self.run_engine(slot,bars,now+60000,send)
        self.assertEqual(send.call_count,3)

    def test_timeout_retries_only_unacknowledged_recipient(self):
        slot=self.initialized();calls=[]
        def send(cid,event):
            calls.append(cid)
            if cid=='scalp' and calls.count(cid)==1:raise TimeoutError('token-must-not-be-logged')
            return {'ok':True,'result':{'message_id':17}}
        bars=self.bars(61,True);now=61*900000+1000
        self.run_engine(slot,bars,now,send)
        self.assertEqual(slot['last_alert']['acknowledged'],2)
        slot=json.loads(json.dumps(slot))
        self.run_engine(slot,bars,now+60000,send)
        self.assertEqual(calls,['day','scalp','swing','scalp'])
        self.assertEqual(slot['last_alert']['acknowledged'],3)
        self.assertFalse(slot['pending'])
        self.assertNotIn('token-must',json.dumps(slot))

    def test_missing_message_id_never_counts_as_success(self):
        slot=self.initialized()
        self.run_engine(slot,self.bars(61,True),61*900000+1000,Mock(return_value={'ok':True,'result':{}}))
        self.assertEqual(slot['last_alert']['acknowledged'],0)
        self.assertTrue(slot['pending'])

    def test_pending_event_is_saved_before_network(self):
        slot=self.initialized();saved=[]
        def save():saved.append(copy.deepcopy(slot))
        def deliver(cid,event):
            self.assertIn(event['id'],saved[-1]['pending'])
            self.assertEqual(saved[-1]['pending'][event['id']]['recipients'][cid]['attempts'],1)
            return {'ok':True,'result':{'message_id':3}}
        self.run_engine(slot,self.bars(61,True),61*900000+1000,deliver,save)

    def test_stale_new_bar_and_expired_queue_are_not_sent(self):
        slot=self.initialized();send=Mock(side_effect=TimeoutError())
        bars=self.bars(61,True);now=61*900000
        self.run_engine(slot,bars,now+1000,send)
        self.assertEqual(send.call_count,3)
        self.run_engine(slot,bars,now+180000,send)
        self.assertEqual(send.call_count,3);self.assertFalse(slot['pending'])
        late=self.initialized();send.reset_mock()
        self.run_engine(late,bars,now+180001,send)
        send.assert_not_called();self.assertEqual(late['status'],'stale_bar')

    def test_slow_first_delivery_does_not_send_stale_to_second_chat(self):
        slot=self.initialized();now=61*900000+1000;clock=[now]
        def send(cid,event):
            clock[0]+=180000
            return {'ok':True,'result':{'message_id':3}}
        deliver=Mock(side_effect=send)
        self.run_engine(slot,self.bars(61,True),now,deliver,clock_ms=lambda:clock[0])
        self.assertEqual(deliver.call_count,1)

    def test_removed_recipient_gets_no_retry(self):
        slot=self.initialized();now=61*900000+1000;send=Mock(side_effect=TimeoutError())
        bars=self.bars(61,True)
        self.run_engine(slot,bars,now,send,recipients=['scalp'])
        send.reset_mock()
        self.run_engine(slot,bars,now+60000,send,recipients=[])
        send.assert_not_called();self.assertFalse(slot['pending'])

    def test_off_roles_are_excluded_but_scalp_is_included(self):
        self.assertEqual(watch.active_recipients(['a','b','c','d','e'],{'a':'off','b':'day','c':'swing','d':'scalp'}),['b','c','d','e'])

    def test_same_direction_cooldown_and_opposite_alert(self):
        slot=self.initialized();base=self.bars(61,True);now=61*900000+1000
        self.run_engine(slot,base,now)
        def event(direction,t):return dict(time=t,direction=direction,reference=105,atr=2,kind='BOS',
                                         body_atr=1,directional_close=1,ema_slope_aligned=True)
        send=Mock(return_value={'ok':True,'result':{'message_id':1}})
        with patch.object(watch,'latest_break',return_value=event(1,62*900000)):
            self.run_engine(slot,self.bars(62),62*900000+1000,send)
        self.assertEqual(slot['status'],'cooldown');send.assert_not_called()
        with patch.object(watch,'latest_break',return_value=event(-1,63*900000)):
            self.run_engine(slot,self.bars(63),63*900000+1000,send)
        self.assertEqual(send.call_count,3)

    def test_unclosed_future_candle_cannot_trigger(self):
        bars=self.bars(61,True)
        self.assertIsNone(watch.latest_break(bars,60*900000,atr_fn=bot.atr,ema_fn=bot.ema))
        self.assertEqual(watch.latest_break(bars,61*900000,atr_fn=bot.atr,ema_fn=bot.ema)['direction'],1)

    def test_message_and_update_do_not_claim_trade_entry(self):
        event=dict(time=61*900000,direction=1,daily_direction=-1,reference=105,kind='BOS')
        message=bot.movement_message(event)
        self.assertIn('Оролт, SL/TP баталгаажаагүй',message)
        self.assertIn('зөрж байна',message)
        slot=self.initialized();state={'movement_watch':{'XAUUSD|15m':slot}}
        cfg={'watch':[{'pair':'XAUUSD','tf':'15m','ema':False,'smc':False,'movement_watch':True}]}
        text=bot.update_text(cfg,state,60*900000+1000,Mock(side_effect=AssertionError('no trade inspection needed')))
        self.assertIn('Хөдөлгөөний хяналт',text)

    def test_atomic_save_failure_preserves_previous_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json';path.write_text('{"old": true}')
            with patch.object(bot,'STATE_FILE',str(path)),patch.object(bot.os,'replace',side_effect=OSError('disk')):
                with self.assertRaises(OSError):bot.save_state({'new':True})
            self.assertEqual(json.loads(path.read_text()),{'old':True})

    def test_main_integration_preserves_trade_state_and_never_broadcasts_backfill(self):
        now=1793800800000+1000
        config={'watch':[{'pair':'XAUUSD','tf':'15m','ema':False,'smc':False,'movement_watch':True}]}
        state={'owner':'owner','chat_ids':['owner','day','swing','scalp'],'chat_roles':{'owner':'off','day':'day','swing':'swing','scalp':'scalp'},
               'smc':{'old':{'status':'pending'}},'vp':{'old':{'status':'open'}},'history':[{'sentinel':True}],
               **{k:True for k in ('started','smc_announced','vp_announced','day_announced','mk2_announced','mk3_announced')}}
        before=copy.deepcopy(state)
        bars=[dict(ct=now-1001-(60-i)*900000,o=100,h=101,l=99,c=100,v=1) for i in range(61)]
        with tempfile.TemporaryDirectory() as directory,ExitStack() as stack:
            stack.enter_context(patch.dict(bot.os.environ,{'TELEGRAM_TOKEN':'offline-test'}))
            stack.enter_context(patch.object(bot,'load_json',side_effect=lambda path,default:config if path==bot.CONFIG_FILE else state if path==bot.STATE_FILE else {}))
            stack.enter_context(patch.object(bot,'save_state'))
            stack.enter_context(patch.object(bot,'FETCH_FILE',str(Path(directory)/'fetch.json')))
            stack.enter_context(patch.object(bot.time,'time',return_value=now/1000))
            tg=stack.enter_context(patch.object(bot,'tg',return_value={'ok':True,'result':[]}))
            stack.enter_context(patch.object(bot,'gold_basis',return_value={}))
            stack.enter_context(patch.object(bot,'fetch_bars',return_value=bars))
            stack.enter_context(patch.object(bot,'fetch_binance',return_value=bars))
            stack.enter_context(patch.object(bot.urllib.request,'urlopen',side_effect=AssertionError('network forbidden')))
            self.assertEqual(bot.main(),0)
        self.assertEqual(state['movement_watch']['XAUUSD|15m']['status'],'initialized')
        for key in ('smc','vp','history'):self.assertEqual(before[key],state[key])
        self.assertFalse(any(c.args[1]=='sendMessage' for c in tg.call_args_list))


if __name__=='__main__':unittest.main()
