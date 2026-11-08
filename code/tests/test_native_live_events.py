import copy
import queue
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
from native_harness_api.native_live_events import LiveEventPump, checked_event
from native_harness_api.bridge_client import NodeClient
from native_harness_api.public_sfe_adapter import PublicSfeAdapter
from test_public_sfe_adapter import B, G, task, success


class LiveEventsTests(unittest.TestCase):
    def test_replacement_order_not_delta_guess(self):
        events = []
        pump = LiveEventPump(lambda seq, event: events.append((seq, event)) or True)
        for text in ('abc', 'ab', ''):
            self.assertTrue(pump.enqueue({'type':'text_snapshot', 'text':text}))
        self.assertTrue(pump.finish())
        self.assertEqual(events, [(n+1, {'type':'text_snapshot','text':v}) for n,v in enumerate(('abc','ab',''))])

    def test_payload_copy(self):
        seen=[];e={'type':'text_snapshot','text':'old'}
        pump=LiveEventPump(lambda seq,event: seen.append(event) or True)
        pump.enqueue(e);e['text']='new';self.assertTrue(pump.finish());self.assertEqual(seen[0]['text'],'old')

    def test_reject_raw_fields_and_unknown_status(self):
        for event in ({'type':'tool_call','secret':'x'}, {'type':'text_snapshot','text':'x','_meta':{}},
                      {'type':'native_status','state':'done'}, {'type':'native_status','state':'running','stop_reason':'end_turn'},
                      {'type':'native_status','state':'idle','stop_reason':'other'}, {'type':'text_snapshot','text':'x'*262145}):
            with self.assertRaises(ValueError):checked_event(event)

    def test_error_no_retry(self):
        calls=[]
        def publish(seq,event):calls.append(seq);raise RuntimeError('private')
        pump=LiveEventPump(publish);pump.enqueue({'type':'native_status','state':'running'})
        self.assertFalse(pump.finish());self.assertEqual(calls,[1]);self.assertFalse(pump.enqueue({'type':'native_status','state':'idle'}))

    def test_queue_overflow_bounded_no_block(self):
        entered=threading.Event();release=threading.Event()
        def publish(*args):entered.set();release.wait(2);return True
        pump=LiveEventPump(publish)
        self.assertTrue(pump.enqueue({'type':'native_status','state':'running'}));self.assertTrue(entered.wait(1))
        for _ in range(64):self.assertTrue(pump.enqueue({'type':'native_status','state':'running'}))
        self.assertFalse(pump.enqueue({'type':'native_status','state':'running'}));release.set();self.assertFalse(pump.finish())

    def test_finish_timeout_preserves_worker_reference(self):
        entered=threading.Event();release=threading.Event()
        def publish(*args):entered.set();release.wait(2);return True
        pump=LiveEventPump(publish);pump.enqueue({'type':'native_status','state':'running'});self.assertTrue(entered.wait(1))
        self.assertFalse(pump.finish(.001));self.assertTrue(pump.thread.is_alive());release.set();pump.thread.join(1)

    def test_close_race_does_not_drop_last_queued_event(self):
        pump=LiveEventPump.__new__(LiveEventPump)
        pump.failed=threading.Event();pump.closed=threading.Event()
        pump.accepted=0;seen=[]
        pump.publish=lambda seq,event: seen.append(seq) or True
        class RaceQueue:
            first=True
            pending=True
            def get(self, timeout):
                if self.first:
                    self.first=False;pump.closed.set();raise queue.Empty()
                if self.pending:
                    self.pending=False;return (1,{'type':'native_status','state':'running'})
                raise queue.Empty()
            def empty(self):return not self.pending
        pump.queue=RaceQueue();pump._work()
        self.assertEqual(seen,[1]);self.assertEqual(pump.accepted,1)

    def test_count_and_bytes_budgets(self):
        for field,value in (('count',512),('bytes',16*1024*1024)):
            pump=LiveEventPump(lambda *args: True);setattr(pump,field,value)
            self.assertFalse(pump.enqueue({'type':'text_snapshot','text':'x'}));self.assertFalse(pump.finish())

    def test_ack_binds_exact_identity(self):
        client=NodeClient('http://127.0.0.1:8000','x'*32)
        ack=dict(remote_task_id='t',claim_id='c',start_grant_id='g',event_seq=1,accepted=True)
        client.http.request=Mock(return_value=ack)
        self.assertTrue(client.event('t','c','g',1,{'type':'text_snapshot','text':'a'}))
        for key,value in (('remote_task_id','other'),('event_seq',True),('accepted',1),('extra','x')):
            bad=copy.deepcopy(ack);bad[key]=value;client.http.request.return_value=bad
            with self.assertRaises(Exception):client.event('t','c','g',1,{'type':'text_snapshot','text':'a'})

    def test_live_delivery_before_native_return_no_replay(self):
        with tempfile.TemporaryDirectory() as root:
            delivered=threading.Event();seen=[];calls=[]
            def publish(seq,event):seen.append((seq,event));delivered.set();return True
            def invoke(**kw):
                calls.append(1);self.assertTrue(kw['on_event']({'type':'text_snapshot','text':'partial'}))
                self.assertTrue(delivered.wait(1),'must deliver before invoke returns')
                self.assertFalse(kw['cancellation'].is_set());return success()
            adapter=PublicSfeAdapter(Path(root).resolve(),binding=B,invoke=invoke)
            self.assertEqual(adapter.start(task(),G,publish_event=publish)['outcome'],'succeeded')
            self.assertEqual(adapter.start(task(),G,publish_event=publish)['outcome'],'succeeded')
            self.assertEqual(calls,[1]);self.assertEqual(len(seen),1)

    def test_failed_delivery_forces_pending_without_rerun(self):
        with tempfile.TemporaryDirectory() as root:
            calls=[]
            def invoke(**kw):
                calls.append(1);kw['on_event']({'type':'text_snapshot','text':'partial'})
                self.assertTrue(kw['cancellation'].wait(1));return success()
            adapter=PublicSfeAdapter(Path(root).resolve(),binding=B,invoke=invoke)
            self.assertEqual(adapter.start(task(),G,publish_event=lambda *args:False)['outcome'],'pending')
            self.assertEqual(adapter.start(task(),G,publish_event=lambda *args:True)['outcome'],'pending')
            self.assertEqual(calls,[1])

    def test_runner_routes_live_event_to_original_claim_and_grant(self):
        from test_node import Client, policy
        from native_harness_api.node import Runner, Journal
        with tempfile.TemporaryDirectory() as root:
            root=Path(root).resolve();native=root/'native';native.mkdir()
            client=Client();client.event=Mock(return_value=True)
            def invoke(**kw):
                kw['on_event']({'type':'text_snapshot','text':'during'})
                return success()
            adapter=PublicSfeAdapter(native,binding=policy().bindings[0],invoke=invoke)
            runner=Runner(client,Journal(root/'journal'),adapter,policy=policy())
            self.assertEqual(runner.run_once()['outcome'],'succeeded')
            client.event.assert_called_once_with('task-1','claim-task-1','grant-task-1',1,
                {'type':'text_snapshot','text':'during'})
            self.assertEqual(client.starts,1)
            self.assertEqual(runner.run_once()['outcome'],'idle')
            self.assertEqual(client.event.call_count,1)
