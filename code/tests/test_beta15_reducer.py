"""Self-authored protocol objects only, no real SDK/account/session/model calls."""
import copy
import unittest
from native_harness_api.beta15_reducer import SessionReducer, ModelReadback, ContractError, deny_permission, REQUEST_ID, ROOT_ID, SUBAGENT, ERROR_UPDATE


def event(kind='state_update', *, run='run',session='root',**fields):
    return {'sessionId':session,'update':{'sessionUpdate':kind,'_meta':{REQUEST_ID:run,ROOT_ID:run},**fields}}

def chunk(value='hello',**kw):return event('agent_message_chunk',messageId='answer',content={'type':'text','text':value},**kw)
def terminal(reason='end_turn',**kw):return event(state='idle',stopReason=reason,**kw)
def ack(run='run'):return {'messageId':'inserted-not-run','_meta':{REQUEST_ID:run}}
def catalog(current='synthetic-a',grouped=False):
    choices=[{'value':'synthetic-a','name':'A'},{'value':'synthetic-b','name':'B'}]
    return [{'configId':'model','name':'Model','type':'select','currentValue':current,
             'options':[{'groupId':'g','name':'Group','options':choices}] if grouped else choices}]

class ReducerTests(unittest.TestCase):
    def make(self,**kw):return SessionReducer('root',deadline=100,clock=kw.pop('clock',lambda:0),**kw)
    def test_ack_is_not_completion(self):
        r=self.make();r.acknowledge(ack());self.assertEqual(r.outcome,'pending')
        self.assertEqual(r.run_id,'run');self.assertNotEqual(r.run_id,r.message_id)
    def test_pre_response_buffer(self):
        r=self.make();e=chunk();r.update(e);e['update']['content']['text']='mutated'
        r.update(terminal());self.assertEqual(r.outcome,'pending')
        r.acknowledge(ack());self.assertEqual(r.outcome,'succeeded');self.assertEqual(r.evidence()['text'],'hello')
    def test_wrong_session_run_and_subagents(self):
        r=self.make();r.acknowledge(ack())
        for e in [chunk(session='child'),terminal(session='child'),terminal(run='other'),event('subagent_update',sessionId='child',state={'state':'idle','stopReason':'end_turn'})]:r.update(e)
        e=terminal();e['update']['_meta'][SUBAGENT]={'version':1,'sessionId':'child'};r.update(e)
        e=terminal();e['update']['_meta'][ROOT_ID]='other';r.update(e)
        self.assertEqual(r.outcome,'pending');self.assertEqual(r.evidence()['text'],'')
    def test_message_upsert_replaces_chunks_and_clear(self):
        r=self.make();r.acknowledge(ack());r.update(chunk('partial'))
        r.update(event('agent_message',messageId='answer',content=[{'type':'text','text':'replacement'}]))
        r.update(chunk(' tail'));self.assertEqual(r.evidence()['text'],'replacement tail')
        r.update(event('agent_message',messageId='answer'));self.assertEqual(r.evidence()['text'],'replacement tail')
        r.update(event('agent_message',messageId='answer',content=None));self.assertEqual(r.evidence()['text'],'')
        r.update(chunk('final'));r.update(terminal());self.assertEqual(r.evidence()['text'],'final')
        self.assertEqual(r.outcome,'succeeded')

    def test_thought_and_user_not_output(self):
        r=self.make();r.acknowledge(ack())
        for kind in ('agent_thought_chunk','user_message'):
            r.update(event(kind,messageId='other',content={'type':'text','text':'private'}))
        r.update(chunk());r.update(terminal());self.assertEqual(r.evidence()['text'],'hello')

    def test_missing_correlation_fails(self):
        r=self.make();r.acknowledge(ack());e=terminal();e['update'].pop('_meta');r.update(e)
        self.assertEqual(r.outcome,'indeterminate')
    def test_missing_or_duplicate_ack(self):
        for response in ({'messageId':'m'}, {'messageId':'m','_meta':{REQUEST_ID:''}}):
            r=self.make();r.acknowledge(response);self.assertEqual(r.outcome,'indeterminate')
        r=self.make();r.acknowledge(ack());r.acknowledge(ack());self.assertEqual(r.outcome,'indeterminate')
    def test_cancel_requires_terminal(self):
        r=self.make();r.acknowledge(ack());self.assertEqual(r.cancel_notification(),{'sessionId':'root'})
        self.assertEqual(r.outcome,'pending');r.update(terminal('cancelled'));self.assertEqual(r.outcome,'cancelled')
        self.assertEqual(r.evidence()['liveness'],'unknown')
    def test_cancel_can_race_completion(self):
        r=self.make();r.acknowledge(ack());r.cancel_notification();r.update(chunk());r.update(terminal())
        self.assertEqual(r.outcome,'succeeded')
    def test_timeout_disconnect_sticky(self):
        for failure in ('timeout','disconnect'):
            r=self.make();r.acknowledge(ack());r.update(chunk())
            r.tick(100) if failure=='timeout' else r.disconnect()
            r.update(terminal());self.assertEqual(r.outcome,'indeterminate');self.assertFalse(r.evidence()['retry_safe'])
    def test_late_terminal_cannot_bypass_deadline(self):
        now=[0];r=self.make(clock=lambda:now[0]);r.acknowledge(ack());r.update(chunk())
        now[0]=100;r.update(terminal());self.assertEqual(r.outcome,'indeterminate')
        r=self.make(clock=lambda:100);r.acknowledge(ack());self.assertFalse(r.insert_acknowledged)

    def test_overflow(self):
        r=self.make(max_events=1);r.update(chunk());r.update(terminal());r.acknowledge(ack())
        self.assertEqual(r.outcome,'indeterminate')
        r=self.make(max_bytes=32);r.update(chunk());self.assertEqual(r.outcome,'indeterminate')
    def test_terminal_reasons_and_empty(self):
        for reason in ('max_tokens','max_turn_requests','future',None):
            r=self.make();r.acknowledge(ack());r.update(chunk());r.update(terminal(reason));self.assertEqual(r.outcome,'indeterminate')
        r=self.make();r.acknowledge(ack());r.update(terminal());self.assertEqual(r.outcome,'indeterminate')
    def test_error_retry_and_terminal(self):
        r=self.make();r.acknowledge(ack());r.update(event(ERROR_UPDATE,error={'code':'execution_failed','message':'not exported'},willRetry=True))
        r.update(chunk());r.update(terminal());self.assertEqual(r.outcome,'succeeded')
        r=self.make();r.acknowledge(ack());r.update(event(ERROR_UPDATE,error={'code':'execution_failed','message':'not exported'},willRetry=False))
        self.assertEqual(r.outcome,'pending');r.update(terminal(ERROR_UPDATE));self.assertEqual(r.outcome,'failed')
        self.assertNotIn('not exported',str(r.evidence()))
    def test_terminal_duplicates_conflicts(self):
        r=self.make();r.acknowledge(ack());r.update(chunk());r.update(terminal());r.update(terminal())
        self.assertEqual(r.outcome,'succeeded');r.update(terminal('cancelled'));self.assertEqual(r.outcome,'indeterminate')
    def test_usage_unknown_and_permissions_denied(self):
        r=self.make();self.assertEqual(r.evidence()['usage'],{'state':'unknown'})
        self.assertEqual(deny_permission({'options':[{'kind':'allow_always','optionId':'yes'}]}),{'outcome':{'outcome':'cancelled'}})
    def test_nontext_not_faked_and_config_update_not_terminal(self):
        r=self.make();r.acknowledge(ack());r.update({'sessionId':'root','update':{'sessionUpdate':'config_option_update','configOptions':catalog()}})
        self.assertEqual(r.outcome,'pending')
        r.update(event('agent_message_chunk',messageId='m',content={'type':'image','data':'not text'}));self.assertEqual(r.outcome,'indeterminate')
    def test_bad_frame_nonfinite_and_clock(self):
        r=self.make();r.update(event(state=float('nan')));self.assertEqual(r.outcome,'indeterminate')
        r=self.make();r.tick(True);self.assertEqual(r.outcome,'indeterminate')

class ModelTests(unittest.TestCase):
    def test_flat_and_grouped_selection_readback(self):
        for grouped in (False,True):
            model=ModelReadback(catalog(grouped=grouped));self.assertFalse(model.confirmed)
            self.assertEqual(model.select_intent('synthetic-b'),{'configId':'model','type':'id','value':'synthetic-b'})
            self.assertFalse(model.confirmed)
            model.selection_response({'configOptions':catalog('synthetic-b',grouped)})
            self.assertTrue(model.confirmed)
    def test_no_invented_selection_or_optimistic_readback(self):
        model=ModelReadback(catalog())
        with self.assertRaises(ContractError):model.select_intent('invented')
        self.assertTrue(model.invalidated)
        model=ModelReadback(catalog())
        model.select_intent('synthetic-b')
        with self.assertRaises(ContractError):model.selection_response({'configOptions':catalog()})
        self.assertFalse(model.confirmed)
    def test_selection_response_is_single_attempt(self):
        model=ModelReadback(catalog())
        with self.assertRaises(ContractError):model.select_intent([])
        self.assertTrue(model.invalidated)
        model=ModelReadback(catalog())
        model.select_intent('synthetic-b')
        with self.assertRaises(ContractError):model.selection_response({'configOptions':catalog()})
        with self.assertRaises(ContractError):model.selection_response({'configOptions':catalog('synthetic-b')})
        self.assertFalse(model.confirmed)

    def test_updates_do_not_replace_response(self):
        model=ModelReadback(catalog());model.select_intent('synthetic-b')
        update={'sessionUpdate':'config_option_update','configOptions':catalog('synthetic-b')}
        model.config_update(update);self.assertFalse(model.confirmed)
        model.selection_response({'configOptions':catalog('synthetic-b')});model.config_update(update);self.assertTrue(model.confirmed)
        update['configOptions']=catalog();model.config_update(update);self.assertFalse(model.confirmed)
    def confirmed_model(self):
        model=ModelReadback(catalog());model.select_intent('synthetic-b')
        model.selection_response({'configOptions':catalog('synthetic-b')})
        return model

    def test_changed_model_never_revives_old_response(self):
        model=self.confirmed_model()
        model.config_update({'sessionUpdate':'config_option_update','configOptions':catalog()})
        self.assertTrue(model.invalidated);self.assertFalse(model.confirmed)
        model.config_update({'sessionUpdate':'config_option_update','configOptions':catalog('synthetic-b')})
        self.assertTrue(model.invalidated);self.assertFalse(model.confirmed)

    def test_invalid_update_never_revives(self):
        for invalid in (None,{}, {'sessionUpdate':'wrong','configOptions':catalog('synthetic-b')},
                        {'sessionUpdate':'config_option_update','configOptions':[]}):
            model=self.confirmed_model()
            with self.assertRaises(ContractError):model.config_update(invalid)
            self.assertFalse(model.confirmed);self.assertTrue(model.invalidated)
            model.config_update({'sessionUpdate':'config_option_update','configOptions':catalog('synthetic-b')})
            self.assertFalse(model.confirmed)

    def test_duplicate_or_invalid_response_clears_confirmation(self):
        for response in (None,{}, {'configOptions':catalog('synthetic-b')}):
            model=self.confirmed_model()
            with self.assertRaises(ContractError):model.selection_response(response)
            self.assertFalse(model.confirmed);self.assertTrue(model.invalidated)
            model.config_update({'sessionUpdate':'config_option_update','configOptions':catalog('synthetic-b')})
            self.assertFalse(model.confirmed)

    def test_duplicate_mixed_and_current_unadmitted(self):
        cases=[];c=catalog();c[0]['options']*=2;cases.append(c)
        c=catalog(grouped=True);c[0]['options'].append({'value':'x','name':'X'});cases.append(c)
        cases.append(catalog('unknown'));cases.append(catalog()*2)
        for c in cases:
            with self.assertRaises(ContractError):ModelReadback(c)

if __name__=='__main__':unittest.main()
