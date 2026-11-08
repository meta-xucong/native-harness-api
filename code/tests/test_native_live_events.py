import copy
from pathlib import Path
import tempfile
import threading
import time
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
        self.assertEqual(events[-1][1], {'type':'text_snapshot','text':''})
        self.assertEqual([seq for seq,_ in events], list(range(1,len(events)+1)))

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

    def test_close_flushes_latest_queued_event(self):
        seen=[];pump=LiveEventPump(lambda seq,event: seen.append((seq,event)) or True)
        pump.next_snapshot=10**15
        self.assertTrue(pump.enqueue({'type':'text_snapshot','text':'last'}))
        self.assertTrue(pump.finish())
        self.assertEqual(seen,[(1,{'type':'text_snapshot','text':'last'})])

    def test_status_barriers_keep_order_while_snapshots_coalesce(self):
        seen=[];entered=threading.Event();release=threading.Event()
        def publish(seq,event):
            if seq==1:entered.set();release.wait(2)
            seen.append((seq,event));return True
        pump=LiveEventPump(publish)
        pump.enqueue({'type':'native_status','state':'running'});self.assertTrue(entered.wait(1))
        for text in ('a','ab','abc'):pump.enqueue({'type':'text_snapshot','text':text})
        pump.enqueue({'type':'native_status','state':'requires_action'})
        for text in ('ab',''):pump.enqueue({'type':'text_snapshot','text':text})
        pump.enqueue({'type':'native_status','state':'idle','stop_reason':'end_turn'})
        release.set();self.assertTrue(pump.finish())
        self.assertEqual([event for _,event in seen],[
            {'type':'native_status','state':'running'}, {'type':'text_snapshot','text':'abc'},
            {'type':'native_status','state':'requires_action'}, {'type':'text_snapshot','text':''},
            {'type':'native_status','state':'idle','stop_reason':'end_turn'}])
        self.assertEqual([seq for seq,_ in seen],[1,2,3,4,5])

    def test_4000_callbacks_with_slow_publisher_keep_full_latest_text(self):
        seen=[];entered=threading.Event();release=threading.Event()
        def publish(seq,event):
            if seq==1:
                entered.set()
                if not release.wait(60):raise RuntimeError('SYNTHETIC_BARRIER_TIMEOUT')
            seen.append((seq,event));return True
        pump=LiveEventPump(publish)
        pump.enqueue({'type':'native_status','state':'running'});self.assertTrue(entered.wait(1))
        text=''
        try:
            for _ in range(4000):
                text+='x'*64
                self.assertTrue(pump.enqueue({'type':'text_snapshot','text':text}))
            self.assertEqual(len(pump.queue),1)
        finally:
            release.set()
        self.assertTrue(pump.finish())
        self.assertEqual(seen[-1][1]['text'],text)
        self.assertLessEqual(pump.count,3)
        self.assertLess(pump.bytes,300000)

    def test_pacing_retains_but_does_not_publish_quota_every_callback(self):
        seen=[];pump=LiveEventPump(lambda seq,event:seen.append(event) or True,horizon=3600)
        pump.next_snapshot=10**15
        for i in range(100):self.assertTrue(pump.enqueue({'type':'text_snapshot','text':str(i)}))
        self.assertEqual(pump.count,0);self.assertEqual(pump.bytes,0)
        self.assertTrue(pump.finish());self.assertEqual(seen,[{'type':'text_snapshot','text':'99'}])

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

    def test_running_pump_delivers_intermediate_without_finish_even_long_horizon(self):
        seen=[];first=threading.Event();second=threading.Event()
        def publish(seq,event):
            seen.append((time.monotonic(),event))
            (first if seq==1 else second).set()
            return True
        pump=LiveEventPump(publish,horizon=3600)
        before=time.monotonic();pump.enqueue({'type':'text_snapshot','text':'first'})
        self.assertTrue(first.wait(1.5),'first snapshot withheld until finish')
        pump.enqueue({'type':'text_snapshot','text':'second'})
        self.assertTrue(second.wait(2),'intermediate snapshot delayed by long horizon')
        self.assertFalse(pump.closed.is_set())
        self.assertEqual(seen[-1][1]['text'],'second')
        self.assertLess(seen[-1][0]-seen[0][0],2)
        self.assertTrue(pump.finish())

    def test_public_protocol_4000_chunks_coalesce_without_losing_final_text(self):
        from test_public_sfe_long_events import LongSourceEventTests
        from test_public_sfe_session import unpack, response, event, terminal
        from test_public_sfe_live_events import update
        from native_harness_api.beta15_reducer import REQUEST_ID
        seen=[];pump=LiveEventPump(lambda seq,value: seen.append((seq,value)) or True)
        try:
            protocol=LongSourceEventTests().selected(pump.enqueue)
            req=unpack(protocol.prompt('synthetic'))[0]
            response(protocol,req,{'messageId':'input','_meta':{REQUEST_ID:'run'}})
            for _ in range(4000):event(protocol,update(text='x'*32)['update'])
            event(protocol,terminal())
            self.assertEqual(protocol.evidence()['outcome'],'succeeded')
            self.assertEqual(protocol.evidence()['turn']['text'],'x'*128000)
        finally:
            flushed=pump.finish()
        self.assertTrue(flushed)
        snapshots=[value for _,value in seen if value['type']=='text_snapshot']
        self.assertEqual(snapshots[-1]['text'],'x'*128000)
        self.assertEqual(seen[-1][1],{'type':'native_status','state':'idle','stop_reason':'end_turn'})
        self.assertLess(pump.count,512);self.assertLess(pump.bytes,16*1024*1024)

    def test_owned_reader_applies_bounded_backpressure_to_valid_burst(self):
        import io
        from native_harness_api.public_sfe_owned_session import PublicSfeOwnedSession
        engine=PublicSfeOwnedSession(object())
        thread=threading.Thread(target=engine._read,args=(io.BytesIO(b'{}\n'*1000),),daemon=True)
        thread.start()
        try:
            deadline=time.monotonic()+2
            while engine._frames.qsize()<512 and time.monotonic()<deadline:time.sleep(.001)
            self.assertEqual(engine._frames.qsize(),512)
            self.assertIsNone(engine._io_fault)
            values=[]
            for _ in range(1001):values.append(engine._frames.get(timeout=2))
            thread.join(2)
            self.assertFalse(thread.is_alive());self.assertIsNone(engine._io_fault)
            self.assertEqual(values[:-1],[b'{}']*1000);self.assertIsNone(values[-1])
            self.assertLessEqual(engine._frames.maxsize,512)
        finally:
            engine._closing=True;thread.join(2)

    def test_owned_reader_full_queue_close_is_bounded(self):
        import io
        from native_harness_api.public_sfe_owned_session import PublicSfeOwnedSession
        engine=PublicSfeOwnedSession(object())
        for _ in range(512):engine._frames.put_nowait(b'old')
        thread=threading.Thread(target=engine._read,args=(io.BytesIO(b'new\n'),),daemon=True)
        thread.start();engine._closing=True;thread.join(2)
        self.assertFalse(thread.is_alive());self.assertLessEqual(engine._frames.qsize(),512)

    def test_owned_self_authored_fast_burst_with_slow_dispatch(self):
        import os
        import sys
        from test_public_sfe_owned_session import CHILD, SyntheticTransport, digest
        from native_harness_api.public_sfe_owned_session import PublicSfeOwnedSession
        from native_harness_api.supervised_acp_transport import SupervisedACPTransport
        class SlowDispatch(PublicSfeOwnedSession):
            def _dispatch(self, raw):
                time.sleep(.0003)
                return super()._dispatch(raw)
        seen=[];pump=LiveEventPump(lambda seq,event: seen.append((seq,event)) or True)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root).resolve();engine=SlowDispatch(SupervisedACPTransport() if os.name=='nt' else SyntheticTransport())
            env={k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC')}
            env['SFE_FIXTURE_MODE']='long_burst'
            try:
                result=engine.run([sys.executable,'-u',str(CHILD.resolve())],path,env,
                    workspace=str(path),model_id='synthetic-b',prompt='synthetic',
                    expected_account_digest=digest(),timeout=45,cleanup_timeout=5,on_event=pump.enqueue)
            finally:
                flushed=pump.finish()
            self.assertTrue(flushed);self.assertEqual(result['outcome'],'succeeded',result)
            self.assertTrue(result['cleanup_confirmed'])
            snapshots=[event for _,event in seen if event['type']=='text_snapshot']
            self.assertEqual(snapshots[-1]['text'],'x'*64000)
            self.assertIsNone(engine._io_fault)
