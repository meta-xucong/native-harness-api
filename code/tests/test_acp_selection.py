"""Deterministic in-memory peer fixtures; no vendor or account execution."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from native_harness_api.workbuddy_acp import ACPClient, ACPError

def config(value='a', **overrides):
    option = dict(id='selector',category='model',type='select',currentValue=value,
                  options=[dict(value='a'),dict(value='b')])
    option.update(overrides)
    return [option]

def event(options):
    return dict(jsonrpc='2.0',method='session/update',params=dict(
        sessionId='sid',update=dict(sessionUpdate='config_option_update',configOptions=options)))

def mode(value):
    return dict(jsonrpc='2.0',method='session/update',params=dict(
        sessionId='sid',update=dict(sessionUpdate='current_mode_update',currentModeId=value)))

def ack(value):
    return dict(jsonrpc='2.0',id=1,result=value)

class SelectionTests(unittest.TestCase):
    def client(self, frames, initial=None):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        c=ACPClient(['fixture'],Path(tmp.name),{},1)
        c.process=object();c.deadline=time.monotonic()+1
        c.session_id='sid'
        c.session=dict(models=dict(currentModelId='a',availableModels=[dict(modelId='a'),dict(modelId='b')]),
                       modes=dict(currentModeId='default'))
        if initial is not None:c.session['configOptions']=initial
        c.sent=[];c.closed=False
        def send(frame):
            c.sent.append(frame)
            for item in frames:
                c.frames.put(None if item is None else json.dumps(item).encode())
        c._send=send
        def close():
            c.closed=True
            return {}
        c.close=close
        return c

    def test_ack_then_async_upgrade(self):
        c=self.client([ack({'modelId':'a'}),event(config())])
        self.assertTrue(c.set_model('a')['readback_confirmed'])
        self.assertEqual(c._model_source,('config','selector'))
        self.assertEqual(len(c.sent),1)

    def test_before_ack_update_and_initial_old_then_new(self):
        for frames in ([event(config()),ack({'modelId':'a'})],
                       [event(config('b')),ack({'modelId':'a'}),event(config())]):
            c=self.client(frames);c.set_model('a')
            self.assertEqual(c._model_source,('config','selector'))

    def test_config_response(self):
        c=self.client([ack({'configOptions':config()})],initial=config('b'))
        c.set_model('a')
        self.assertEqual(c.sent[0]['method'],'session/set_config_option')

    def test_invalid_readbacks_no_prompt_or_retry(self):
        cases=[
            [ack({'modelId':'a'}),None],
            [ack({'modelId':'b'}),event(config())],
            [ack({'modelId':'a'}),event([])],
            [ack({'modelId':'a'}),event(config()*2)],
            [ack({'modelId':'a'}),event(config(type='boolean'))],
            [ack({'modelId':'a'}),event(config(currentValue=None))],
            [ack({'modelId':'a'}),event(config(options=[{'value':'b'}]))],
            [ack({'modelId':'a'}),event(config(options=[{'value':'a'},{'value':'a'}]))],
            [ack({'modelId':'a'}),event(config('b'))],
            [event(config()),ack({'modelId':'a'}),event([])],
            [event(config()),ack({'modelId':'a'}),event(config(id='changed'))],
            [event(config()),dict(jsonrpc='2.0',id=1,error={'code':-1})],
            [ack({'modelId':'a'}),dict(jsonrpc='2.0',id=2,result={})],
        ]
        for frames in cases:
            with self.subTest(frames=frames):
                c=self.client(frames)
                with self.assertRaises(ACPError):c.set_model('a')
                self.assertTrue(c.fault);self.assertTrue(c.closed)
                self.assertEqual(len(c.sent),1)
                with self.assertRaises(ACPError):c.set_model('a')
                self.assertEqual(len(c.sent),1)

    def test_wrong_session(self):
        e=event(config());e['params']['sessionId']='other'
        c=self.client([ack({'modelId':'a'}),e])
        with self.assertRaisesRegex(ACPError,'ACP_WRONG_SESSION'):c.set_model('a')

    def test_unsafe_mode_sticky_before_ack_and_after(self):
        for frames in ([mode('bypassPermissions'),mode('default'),ack({'modelId':'a'}),event(config())],
                       [ack({'modelId':'a'}),event(config()),mode('bypassPermissions'),mode('default')]):
            c=self.client(frames)
            with self.assertRaises(ACPError):c.set_model('a')
            with self.assertRaises(ACPError):c._event(mode('default'))
            self.assertEqual(len(c.sent),1)

    def test_config_source_never_downgrades_after_commit(self):
        c=self.client([ack({'modelId':'a'}),event(config())]);c.set_model('a')
        with self.assertRaises(ACPError):c._event(event([]))
        legacy=dict(jsonrpc='2.0',method='session/update',params=dict(sessionId='sid',
                    update=dict(sessionUpdate='current_model_update',currentModelId='a')))
        with self.assertRaises(ACPError):c._event(legacy)

    def test_no_new_deadline_after_ack(self):
        c=self.client([ack({'modelId':'a'})])
        original=c._next
        def expire_after_ack():
            value=original()
            c.deadline=0
            return value
        c._next=expire_after_ack
        with self.assertRaises(ACPError):c.set_model('a')
        self.assertEqual(c.deadline,0);self.assertEqual(len(c.sent),1)

    def test_preobserved_unsafe_control_prevents_setter(self):
        c=self.client([])
        c.frames.put(json.dumps(mode('bypassPermissions')).encode())
        c.frames.put(json.dumps(mode('default')).encode())
        with self.assertRaises(ACPError):c.set_model('a')
        self.assertEqual(c.sent,[])

    def current_model_update(self, value):
        return dict(jsonrpc='2.0',method='session/update',params=dict(sessionId='sid',
                    update=dict(sessionUpdate='current_model_update',currentModelId=value)))

    def test_typed_invalid_legacy_update_cannot_be_repaired(self):
        for bad in (None,True,1,'',[],{},'unadvertised'):
            for config_source in (False,True):
                frames=([event(config('b'))] if config_source else [])
                frames += [self.current_model_update(bad),ack({'modelId':'a'}),event(config())]
                c=self.client(frames)
                with self.assertRaises(ACPError):c.set_model('a')
                self.assertTrue(c.fault);self.assertFalse(c.prompt_attempted)

    def test_preselection_config_invalid_deletion_and_id_change_sticky(self):
        for bad in ([],None,config(currentValue=None),config()*2):
            c=self.client([ack({'configOptions':config()})])
            for frame in (event(bad),event(config())):
                c.frames.put(json.dumps(frame).encode())
            with self.assertRaises(ACPError):c.set_model('a')
            self.assertEqual(c.sent,[])
        for bad in ([],config(id='replacement')):
            c=self.client([])
            c._event(event(config()))
            with self.assertRaises(ACPError):c._event(event(bad))
            with self.assertRaises(ACPError):c._event(event(config()))
        c=self.client([],initial=config(currentValue=None))
        c.frames.put(json.dumps(event(config())).encode())
        with self.assertRaises(ACPError):c.set_model('a')
        self.assertEqual(c.sent,[])

    def test_wellformed_pre_ack_old_dual_sources_may_converge(self):
        for old in (self.native_model_update('b'),self.current_model_update('b')):
            c=self.client([event(config('b')),old,ack({'modelId':'a'}),event(config())])
            c.set_model('a');self.assertEqual(c._model_source,('config','selector'))
            c=self.client([event(config('b')),ack({'modelId':'a'}),old,event(config())])
            with self.assertRaises(ACPError):c.set_model('a')
            c=self.client([event(config()),old,ack({'modelId':'a'}),None])
            with self.assertRaises(ACPError):c.set_model('a')

    def test_initial_unsafe_snapshot_cannot_be_repaired_by_pending_safe(self):
        for initial in ({'modes':{'currentModeId':'fullAccess'}},
                        {'configOptions':[dict(id='mode',category='mode',type='select',
                         currentValue='fullAccess',options=[dict(value='fullAccess')])]}):
            c=self.client([]);c.identity={};c.session_id=None;c.session=None
            c._rpc=lambda *a:dict(sessionId='sid',**initial)
            c._pending_events=[mode('default')]
            with self.assertRaisesRegex(ACPError,'ACP_PERMISSION_MODE_UNVERIFIED'):c.new_session()
            with self.assertRaises(ACPError):c.set_model('a')
            self.assertEqual(c.sent,[])
        # No present initial mode: a safe queued mode can still establish it.
        c=self.client([]);c.identity={};c.session_id=None;c.session=None
        c._rpc=lambda *a:dict(sessionId='sid')
        c._pending_events=[mode('default')];c.new_session();c._safe_mode()

    def native_model_update(self, current='a', catalog=None):
        return dict(jsonrpc='2.0',method='session/update',params=dict(sessionId='sid',
            update=dict(sessionUpdate='model_update',currentModelId=current,
                        availableModels=catalog if catalog is not None else [dict(modelId='a'),dict(modelId='b')])))

    def test_native_full_model_snapshot_readback_and_drift(self):
        c=self.client([ack({'modelId':'a'}),self.native_model_update()]);c.set_model('a')
        self.assertEqual(c._model_source,('legacy',None))
        with self.assertRaises(ACPError):c._event(self.native_model_update('b'))
        c=self.client([ack({'modelId':'a'}),event(config())]);c.set_model('a')
        c._event(self.native_model_update());self.assertEqual(c._model_source,('config','selector'))
        with self.assertRaises(ACPError):c._event(self.native_model_update('b'))

    def test_native_model_snapshot_invalid_and_deleted(self):
        invalid=[]
        for key in ('currentModelId','availableModels'):
            frame=self.native_model_update();del frame['params']['update'][key];invalid.append(frame)
        for catalog in ([],{},[None],[dict(id='a')],[dict(modelId='')],
                        [dict(modelId='a'),dict(modelId='a')],[dict(modelId='b')]):
            invalid.append(self.native_model_update(catalog=catalog))
        for current in (None,True,1,'','missing'):
            invalid.append(self.native_model_update(current))
        frame=self.native_model_update();frame['params']['update']['availableModels']=None;invalid.append(frame)
        for frame in invalid:
            for config_source in (False,True):
                c=self.client([ack({'modelId':'a'}),event(config())] if config_source else
                              [ack({'modelId':'a'}),self.native_model_update()])
                c.set_model('a')
                with self.assertRaises(ACPError):c._event(frame)
                with self.assertRaises(ACPError):c._event(self.native_model_update())

    def test_native_legacy_snapshot_never_confirms_pending_config(self):
        c=self.client([event(config('b')),ack({'modelId':'a'}),self.native_model_update(),None])
        with self.assertRaises(ACPError):c.set_model('a')
        self.assertIsNone(c.model_id)
        c=self.client([event(config()),ack({'modelId':'a'}),self.native_model_update('b')])
        with self.assertRaises(ACPError):c.set_model('a')

    def test_closing_interrupts_wait_without_new_rpc(self):
        c=self.client([ack({'modelId':'a'})]);original=c._next
        def closing_after_ack():
            result=original();c._closing=True;return result
        c._next=closing_after_ack
        with self.assertRaisesRegex(ACPError,'ACP_CLOSED'):c.set_model('a')
        self.assertEqual(len(c.sent),1);self.assertFalse(c.prompt_attempted)
        self.assertTrue(c.closed)

    def test_grouped_options_validated_and_target_not_inferred(self):
        valid=[dict(group='g',options=[dict(value='a'),dict(value='b')])]
        c=self.client([ack({'configOptions':config(options=valid)})],initial=config(options=valid))
        c.set_model('a')
        for values in (None,{},[None],[dict(value='')],[dict(value=1)],
                       [dict(group='g',options=None)],
                       [dict(group='g',options=[dict(value='a')]),dict(value='a')]):
            c=self.client([],initial=config(options=values))
            with self.assertRaises(ACPError):c.set_model('a')
            self.assertEqual(c.sent,[])

    def test_concurrent_selection_has_only_one_setter(self):
        c=self.client([ack({'modelId':'a'}),event(config())])
        entered=threading.Event();release=threading.Event();original=c._send
        def held_send(frame):
            entered.set()
            if not release.wait(2):raise AssertionError('Fixture release missing')
            original(frame)
        c._send=held_send
        errors=[]
        def run():
            try:c.set_model('a')
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(ACPError,'ACP_INVALID_STATE'):c.set_model('a')
        finally:
            release.set();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertEqual(len(c.sent),1)

    def test_contradictory_typed_ack_is_never_ignored(self):
        for field in ('modelId', 'currentModelId'):
            for bad in ('b', None, 1, True, [], {}):
                with self.subTest(field=field,bad=bad):
                    c=self.client([event(config()),ack({field:bad,'configOptions':config()})])
                    with self.assertRaises(ACPError):c.set_model('a')
                    self.assertEqual(len(c.sent),1);self.assertFalse(c.prompt_attempted)

    def test_full_snapshot_missing_null_and_duplicate_ids(self):
        absent=event(config());del absent['params']['update']['configOptions']
        for frame in (absent,event(None),event({}),event(config()+[dict(id='selector',category='other')])):
            c=self.client([ack({'modelId':'a'}),frame])
            with self.assertRaises(ACPError):c.set_model('a')
        for response in ({},{'configOptions':None},{'configOptions':[]}):
            c=self.client([event(config()),ack(response)],initial=config())
            with self.assertRaises(ACPError):c.set_model('a')

    def test_pre_send_cached_update_cannot_confirm_selection(self):
        c=self.client([ack({'modelId':'a'}),None])
        c.frames.put(json.dumps(event(config())).encode())
        with self.assertRaises(ACPError):c.set_model('a')
        self.assertEqual(len(c.sent),1);self.assertFalse(c.prompt_attempted)

    def test_upgraded_source_ignores_legacy_ack_as_evidence(self):
        c=self.client([event(config('b')),ack({'currentModelId':'a'}),None])
        with self.assertRaises(ACPError):c.set_model('a')
        self.assertIsNone(c.model_id)

    def test_legacy_catalog_types_duplicates_membership(self):
        for catalog in (None,{},[None],[{'modelId':1}],[{'modelId':''}],
                        [{'modelId':'a'},{'modelId':'a'}],[{'modelId':'b'}]):
            c=self.client([]);c.session['models']['availableModels']=catalog
            with self.assertRaises(ACPError):c.set_model('a')
            self.assertEqual(c.sent,[])

    def test_unsafe_create_replay_and_idle_sticky(self):
        c=self.client([]);c.identity={};c.session_id=None;c.session=None
        c._rpc=lambda *a:dict(sessionId='sid',models=dict(currentModelId='a',availableModels=[dict(modelId='a')]),modes=dict(currentModeId='default'))
        c._pending_events=[mode('fullAccess'),mode('default')]
        with self.assertRaises(ACPError):c.new_session()
        with self.assertRaises(ACPError):c.set_model('a')
        self.assertEqual(c.sent,[])
        c=self.client([ack({'modelId':'a'}),event(config())]);c.set_model('a')
        with self.assertRaises(ACPError):c._event(mode('fullAccess'))
        with self.assertRaises(ACPError):c._event(mode('default'))
        with self.assertRaises(ACPError):c.prompt('never',lambda:None)
        self.assertEqual(len(c.sent),1)

    def test_unsafe_config_mode_cannot_be_repaired(self):
        unsafe=dict(id='mode',category='mode',type='select',currentValue='fullAccess',options=[dict(value='fullAccess')])
        c=self.client([event(config()+[unsafe]),event(config()),ack({'modelId':'a'})])
        with self.assertRaises(ACPError):c.set_model('a')
        self.assertTrue(c.fault);self.assertFalse(c.prompt_attempted)

if __name__=='__main__':unittest.main()
