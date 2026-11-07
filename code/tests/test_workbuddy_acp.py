"""Real subprocess protocol tests against explicit synthetic peer only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from native_harness_api.workbuddy_acp import ACPClient, ACPError

PEER=Path(__file__).parent/'fakes'/'workbuddy_acp_peer.py'
class ACPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.count=self.root/'count'
    def client(self, mode='ok', timeout=3, handler=None):
        c=ACPClient([sys.executable,str(PEER),mode,str(self.count)],self.root,dict(os.environ),timeout,handler)
        self.addCleanup(c.close);c.start();return c
    def ready(self, mode='ok', **kwargs):
        c=self.client(mode,**kwargs);c.initialize();c.new_session(self.root);c.set_model('fake-a');return c
    def test_real_subprocess_identity_model_and_single_send(self):
        c=self.ready();events=[]
        result=c.prompt('test',lambda:events.append('durable'))
        self.assertEqual(events,['durable']);self.assertEqual(result['text'],'synthetic answer')
        self.assertEqual(result['model_id'],'fake-a');self.assertEqual(self.count.read_text(),'1')
        with self.assertRaises(ACPError):c.prompt('again',lambda:None)
        self.assertFalse(c.close()['descendants_stopped'])
    def test_config_model_readback(self):
        c=self.ready('config');self.assertEqual(c.model_id,'fake-a')
    def test_empty_ack_and_model_mismatch_fail(self):
        for mode in ('config_empty','empty_model','mismatch'):
            with self.subTest(mode=mode):
                c=self.client(mode);c.initialize();c.new_session()
                with self.assertRaises(ACPError):c.set_model('fake-a')
        self.assertFalse(self.count.exists())
    def test_unknown_model_rejected_before_send(self):
        c=self.client();c.initialize();c.new_session()
        with self.assertRaises(ACPError):c.set_model('unavailable')
        self.assertFalse(self.count.exists())
    def test_intent_failure_means_no_prompt_and_no_retry(self):
        c=self.ready()
        def fail():raise RuntimeError('private')
        with self.assertRaises(ACPError):c.prompt('test',fail)
        with self.assertRaises(ACPError):c.prompt('test',lambda:None)
        self.assertFalse(self.count.exists())
    def test_unsafe_mode_blocks_prompt(self):
        c=self.ready('unsafe')
        with self.assertRaises(ACPError):c.prompt('test',lambda:None)
        self.assertFalse(self.count.exists())
    def test_permission_once_and_reject(self):
        for decision in ('allow_once','reject_once'):
            c=self.ready('permission',handler=lambda p:decision);r=c.prompt('test',lambda:None)
            answer=json.loads(Path(str(self.count)+'.approval').read_text())
            self.assertEqual(answer['result']['outcome']['optionId'],'once' if decision=='allow_once' else 'no')
            self.assertEqual(r['permissions'],[decision]);c.close()
    def test_permission_always_rejected(self):
        c=self.ready('permission',handler=lambda p:'allow_always')
        with self.assertRaises(ACPError):c.prompt('test',lambda:None)
        self.assertFalse(Path(str(self.count)+'.approval').exists())
    def test_wrong_session_and_late_pre_prompt_event(self):
        for mode in ('wrong_session','late'):
            with self.subTest(mode=mode):
                with self.assertRaises(ACPError):self.ready(mode)
        self.assertFalse(self.count.exists())
    def test_model_change_during_turn_is_failure(self):
        c=self.ready('changed')
        with self.assertRaises(ACPError):c.prompt('test',lambda:None)
    def test_after_final_event_not_in_output(self):
        c=self.ready('after_final');r=c.prompt('test',lambda:None)
        self.assertEqual(r['text'],'synthetic answer')
    def test_timeout_and_eof_never_resend(self):
        for mode in ('timeout','eof'):
            with self.subTest(mode=mode):
                c=self.ready(mode)
                # Exercise the prompt deadline, not variable Windows startup cost.
                c.deadline=time.monotonic()+.4
                before=int(self.count.read_text()) if self.count.exists() else 0
                with self.assertRaises(ACPError):c.prompt('test',lambda:None)
                with self.assertRaises(ACPError):c.prompt('test',lambda:None)
                self.assertEqual(int(self.count.read_text()),before+1);c.close()
    def test_auth_error_is_bounded_and_no_credentials_collected(self):
        c=self.client('auth');c.initialize()
        with self.assertRaises(ACPError) as e:c.new_session()
        self.assertEqual(e.exception.code,'ACP_AUTH_REQUIRED');self.assertNotIn('secret',str(e.exception))
    def test_invalid_json_and_output_caps(self):
        for mode in ('badjson','flood','stderr'):
            with self.subTest(mode=mode):
                c=self.client(mode)
                with self.assertRaises(ACPError):c.initialize()
    def test_slow_intent_commit_is_synchronous_then_deadline_blocks_prompt(self):
        import time
        import threading
        c=self.ready()
        c.deadline=time.monotonic()+.2
        caller = threading.get_ident()
        callback_threads = []
        committed = []
        def commit():
            callback_threads.append(threading.get_ident())
            time.sleep(.3)
            committed.append(True)
        with self.assertRaises(ACPError) as exc:
            c.prompt('test',commit)
        self.assertEqual(exc.exception.code, 'ACP_TIMEOUT')
        self.assertEqual(callback_threads, [caller])
        self.assertEqual(committed, [True])
        self.assertFalse(self.count.exists())
        self.assertTrue(c.close()['host_exited'])
    def test_durable_intent_keeps_calling_thread_and_lock(self):
        import threading
        c=self.ready()
        caller=threading.get_ident()
        lock=threading.RLock()
        checked=[]
        def commit():
            self.assertEqual(threading.get_ident(),caller)
            self.assertTrue(lock._is_owned())
            checked.append(True)
        with lock:
            c.prompt('test',commit)
        self.assertEqual(checked,[True])

    def test_permission_handler_timeout_does_not_late_approve(self):
        import time
        c=self.ready('permission',handler=lambda p:(time.sleep(.4) or 'allow_once'))
        c.deadline=time.monotonic()+.2
        with self.assertRaises(ACPError):c.prompt('test',lambda:None)
        time.sleep(.25)
        self.assertFalse(Path(str(self.count)+'.approval').exists())
    def test_concurrent_prompt_attempts_send_once(self):
        from concurrent.futures import ThreadPoolExecutor
        c=self.ready()
        def invoke(_):
            try:return c.prompt('test',lambda:None)
            except ACPError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(invoke,range(2)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.count.read_text(),'1')

    def test_empty_end_turn_is_not_success(self):
        c=self.ready('empty')
        with self.assertRaises(ACPError) as exc:c.prompt('test',lambda:None)
        self.assertEqual(exc.exception.code,'ACP_EMPTY_OUTPUT')

    @staticmethod
    def enqueue_mode(c, mode):
        c.frames.put_nowait((json.dumps({'jsonrpc':'2.0','method':'session/update',
            'params':{'sessionId':c.session_id,'update':{'sessionUpdate':'current_mode_update',
            'currentModeId':mode}}})+'\n').encode())
    def test_only_actual_default_mode_admitted(self):
        for mode in ('plan','ask','dontAsk','bypassPermissions','fullAccess'):
            with self.subTest(mode=mode):
                c=self.ready();c.session['modes']['currentModeId']=mode
                with self.assertRaises(ACPError):c.prompt('test',lambda:None)
                self.assertFalse(self.count.exists());c.close()
    def test_already_queued_unsafe_mode_prevents_intent_and_send(self):
        c=self.ready();self.enqueue_mode(c,'fullAccess');intents=[]
        with self.assertRaises(ACPError):c.prompt('test',lambda:intents.append(True))
        self.assertEqual(intents,[]);self.assertFalse(self.count.exists())
    def test_control_arriving_during_intent_prevents_send(self):
        c=self.ready();commits=[]
        def commit():
            commits.append(True)
            self.enqueue_mode(c,'fullAccess')
        with self.assertRaises(ACPError):c.prompt('test',commit)
        self.assertEqual(commits,[True]);self.assertFalse(self.count.exists())
    def test_config_model_source_removal_and_ambiguity_fail_closed(self):
        for mode in ('config_removed','config_ambiguous'):
            with self.subTest(mode=mode):
                c=self.ready(mode)
                with self.assertRaises(ACPError):c.prompt('test',lambda:None)
                c.close()
    def test_config_model_source_id_cannot_silently_change(self):
        c=self.ready('config');c.session['configOptions'][0]['id']='different-model-selector'
        with self.assertRaises(ACPError):c.prompt('test',lambda:None)
        self.assertFalse(self.count.exists())

    def test_config_mode_source_removal_ambiguity_and_id_change_fail(self):
        for mode in ('config_mode_removed','config_mode_ambiguous','config_mode_changed'):
            with self.subTest(mode=mode):
                c=self.ready(mode)
                with self.assertRaises(ACPError) as exc:c.prompt('test',lambda:None)
                self.assertEqual(exc.exception.code,'ACP_PERMISSION_MODE_UNVERIFIED')
                c.close()
    def test_dual_mode_sources_cannot_fall_back_in_either_direction(self):
        for remove in ('legacy','config'):
            with self.subTest(remove=remove):
                c=self.ready('config')
                if remove=='legacy':c.session.pop('modes')
                else:c.session['configOptions']=[c.session['configOptions'][0]]
                with self.assertRaises(ACPError):c.prompt('test',lambda:None)
                self.assertFalse(self.count.exists());c.close()
    def test_dual_mode_disagreement_is_rejected(self):
        c=self.ready('config')
        c.session['modes']['currentModeId']='plan'
        with self.assertRaises(ACPError):c.prompt('test',lambda:None)
        self.assertFalse(self.count.exists())

    def test_native_cancel_reported_not_success_stop(self):
        c=self.ready('cancelled');self.assertEqual(c.prompt('test',lambda:None)['stop_reason'],'cancelled')

if __name__=='__main__':unittest.main()
