from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from native_harness_api.contracts import Binding, digest
from native_harness_api.public_sfe_adapter import PENDING, PublicSfeAdapter, project_result, project_workbuddy_node_result
B=Binding('wb',1,'wb-model','native-model');G={'start_grant_id':'g1'}
def task():
 r={'model':'wb-model','messages':[{'role':'user','content':'hello'}],'stream':False,'n':1}
 return {'remote_task_id':'t1','identity':{'source':'s','tenant':'u','principal':'k','operation':'o'},'ledger_instance':'l','request_digest':digest(r),'request':{'binding':B.wire(),'client_request':r,'messages':r['messages']}}
def success():
 return {'outcome':'succeeded','fault':None,'session':{'outcome':'succeeded','fault':None,'model_confirmed':True,'protocol_ready':True,'account_verified':True,'turn':{'outcome':'succeeded','fault':None,'insert_acknowledged':True,'terminal_observed':True,'terminal_reason':'end_turn','text':'answer'}},'cleanup_confirmed':True,'natural_exit_zero':True,'cleanup':{k:True for k in ('host_exited','owned_job_empty','handles_closed','cleanup_complete','io_threads_stopped')}}


def pre_prompt_timeout():
    value = success()
    value.update(outcome='indeterminate', fault='TURN_NOT_COMPLETED', launch_attempted=True,
                 session={'prompt_intent_issued': False, 'turn': None,
                          'session_new_intent_issued': False, 'session_close_acknowledged': False})
    return value


class AdapterTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.calls=0
 def tearDown(self):self.tmp.cleanup()
 def invoke(self,**kw):
  self.calls+=1;self.assertEqual(kw,{'model_id':'native-model','messages':[{'role':'user','content':'hello'}]});return success()
 def adapter(self,invoke=None):return PublicSfeAdapter(self.root,binding=B,invoke=invoke or self.invoke)
 def test_replay(self):
  a=self.adapter();r=a.start(task(),G);self.assertEqual(r['outcome'],'succeeded');self.assertEqual(self.adapter().start(task(),G),r);self.assertEqual(self.adapter().reconcile(task(),G),r);self.assertEqual(self.calls,1)
 def test_concurrent_once(self):
  a=self.adapter()
  with ThreadPoolExecutor(max_workers=8) as ex:values=list(ex.map(lambda _:a.start(task(),G),range(16)))
  self.assertEqual(self.calls,1);self.assertTrue(all(v['outcome'] in ('pending','succeeded') for v in values))
 def test_unknown_no_retry(self):
  def fail(**kw):self.calls+=1;raise RuntimeError('secret')
  a=self.adapter(fail);self.assertEqual(a.start(task(),G)['outcome'],'pending');self.assertEqual(self.adapter().start(task(),G)['outcome'],'pending');self.assertEqual(self.calls,1);self.assertNotIn(b'secret',a.db.read_bytes())
 def test_crash_window(self):
  a=self.adapter();tid,gid,fp,_=a._identity(task(),G)
  with closing(sqlite3.connect(a.db)) as db:
   db.execute('INSERT INTO evidence VALUES(?,?,?,NULL)',(gid,tid,fp));db.commit()
  self.assertEqual(a.start(task(),G)['outcome'],'pending');self.assertEqual(self.calls,0)
 def test_changed_grant(self):
  a=self.adapter();a.start(task(),G)
  with self.assertRaises(sqlite3.IntegrityError):a.start(task(),{'start_grant_id':'g2'})
  self.assertEqual(self.calls,1)
 def test_identity_conflict(self):
  a=self.adapter();a.start(task(),G);t=task();t['identity']['principal']='other'
  with self.assertRaises(ValueError):a.start(t,G)
  with self.assertRaises(ValueError):a.reconcile(t,G)
 def test_binding_mismatch(self):
  a=self.adapter();t=task();t['request']['binding']['native_model']='other'
  with self.assertRaises(ValueError):a.start(t,G)
  self.assertEqual(self.calls,0)
 def test_reconcile_no_start(self):self.assertIsNone(self.adapter().reconcile(task(),G));self.assertEqual(self.calls,0)
 def test_raw_evidence_not_saved(self):
  def invoke(**kw):v=success();v['private']='credential';return v
  a=self.adapter(invoke);a.start(task(),G);self.assertNotIn(b'credential',a.db.read_bytes())
 def test_failure_diagnostic_is_persisted_separately_and_recover_does_not_retry(self):
  def invoke(**kw):
   self.calls += 1
   return {'fault': 'TURN_NOT_COMPLETED', 'session': {'first_failed_frame': {
       'kind': 'rpc_error', 'method': 'session/prompt', 'rpc_error_code': -32603,
       'message': 'private credential', 'data': {'id': 'private native id'}}}}
  a = self.adapter(invoke)
  with closing(sqlite3.connect(a.db)) as db:
   db.execute('INSERT INTO evidence VALUES(?,?,?,?)', ('oldg', 'oldt', 'oldhash', '{"old":"result"}'))
   db.commit()
  self.assertEqual(a.start(task(), G), PENDING)
  self.assertEqual(self.adapter().reconcile(task(), G), PENDING)
  self.assertEqual(self.adapter().start(task(), G), PENDING)
  self.assertEqual(self.calls, 1)
  with closing(sqlite3.connect(a.db)) as db:
   grant, first = db.execute('SELECT grant_id,first_failed_frame FROM protocol_diagnostics').fetchone()
   self.assertEqual(grant, G['start_grant_id'])
   self.assertEqual(json.loads(first), {'kind': 'rpc_error', 'method': 'session/prompt', 'rpc_error_code': -32603})
   self.assertEqual(db.execute('SELECT result FROM evidence WHERE grant_id=?', ('oldg',)).fetchone(),
                    ('{"old":"result"}',))
  self.assertNotIn(b'private', a.db.read_bytes())
 def test_outer_failure_is_saved_without_payload(self):
  def invoke(**kw):
   return {'fault': 'TURN_NOT_COMPLETED', 'session': {'first_failed_frame': {
       'kind': 'invalid_jsonrpc_version', 'method': 'PRIVATE', 'version': 'PRIVATE', 'raw': 'PRIVATE'}}}
  a = self.adapter(invoke)
  self.assertEqual(a.start(task(), G), PENDING)
  with closing(sqlite3.connect(a.db)) as db:
   grant, first = db.execute('SELECT grant_id,first_failed_frame FROM protocol_diagnostics').fetchone()
   self.assertEqual(grant, G['start_grant_id'])
   self.assertEqual(json.loads(first), {'kind': 'invalid_jsonrpc_version'})
  self.assertNotIn(b'PRIVATE', a.db.read_bytes())
 def test_pending_matrix(self):
  for path in [('cleanup_confirmed',),('natural_exit_zero',),('session','model_confirmed'),('session','protocol_ready'),('session','account_verified'),('session','turn','insert_acknowledged'),('session','turn','terminal_observed'),('cleanup','handles_closed'),('cleanup','io_threads_stopped')]:
   for bad in (False,1,'true',None):
    v=success();cursor=v
    for key in path[:-1]:cursor=cursor[key]
    cursor[path[-1]]=bad
    with self.subTest(path=path,bad=bad):self.assertEqual(project_result(v,'m')['outcome'],'pending')
 def test_fault_empty(self):
  for content in ('',' ','\ud800'):
   v=success();v['session']['turn']['text']=content;self.assertEqual(project_result(v,'m')['outcome'],'pending')
  v=success();v['session']['turn']['fault']='x';self.assertEqual(project_result(v,'m')['outcome'],'pending')
 def test_refusal_cancel_failure(self):
  for outcome,reason in [('succeeded','refusal'),('cancelled','cancelled'),('failed','_codebuddy.ai/error')]:
   v=success();v['outcome']=v['session']['outcome']=v['session']['turn']['outcome']=outcome;v['session']['turn']['terminal_reason']=reason;r=project_result(v,'m');self.assertEqual(r['outcome'],outcome)
   if reason=='refusal':self.assertEqual(r['output_blocks'][0]['type'],'refusal')
 def test_missing_root(self):
  with self.assertRaises((ValueError,FileNotFoundError)):PublicSfeAdapter(self.root/'missing',binding=B,invoke=self.invoke)
 def test_result_copy(self):
  a=self.adapter();r=a.start(task(),G);r['outcome']='failed';self.assertEqual(a.reconcile(task(),G)['outcome'],'succeeded')

 def test_hardlinked_db(self):
  import os
  a=self.adapter()
  os.link(a.db,self.root/'alias')
  with self.assertRaises(ValueError):a.reconcile(task(),G)
 def test_symlink_sidecar(self):
  a=self.adapter();other=self.root/'other';other.write_text('x');link=Path(str(a.db)+'-journal')
  try:link.symlink_to(other)
  except OSError:self.skipTest('symlink unavailable')
  with self.assertRaises(ValueError):a.start(task(),G)
 def test_multiturn_rejected(self):
  t=task();r=t['request']['client_request'];r['messages'].insert(0,{'role':'assistant','content':'old'});t['request_digest']=digest(r)
  with self.assertRaises(ValueError):self.adapter().start(t,G)
  self.assertEqual(self.calls,0)

 def test_preexisting_hardlink_not_modified(self):
  import os
  outside=self.root/'other.sqlite'
  with closing(sqlite3.connect(outside)) as db:
   db.execute('CREATE TABLE sentinel(n INTEGER)');db.commit()
  os.link(outside,self.root/'sfe-evidence.sqlite')
  with self.assertRaises(ValueError):self.adapter()
  with closing(sqlite3.connect(outside)) as db:self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[('sentinel',)])
 def test_failure_code(self):
  v=success();v['session']['failure_code']='protocol';self.assertEqual(project_result(v,'m')['outcome'],'pending')

 def test_pre_prompt_abort_requires_full_owned_evidence(self):
  proof = pre_prompt_timeout()
  self.assertEqual(project_workbuddy_node_result(proof, 'native-model')['code'], 'PRE_PROMPT_ABORTED')
  self.assertEqual(project_result(proof, 'native-model'), PENDING)
  for container, field, value in (
      ('session', 'prompt_intent_issued', True), ('session', 'prompt_intent_issued', 0),
      ('session', 'turn', {}), ('session', 'session_new_intent_issued', True),
      ('session', 'session_new_intent_issued', None), ('cleanup', 'owned_job_empty', False),
      (None, 'launch_attempted', False), (None, 'natural_exit_zero', False),
      (None, 'cleanup', None)):
   with self.subTest(field=field, value=value):
    malformed = pre_prompt_timeout()
    target = malformed if container is None else malformed[container]
    target[field] = value
    self.assertEqual(project_workbuddy_node_result(malformed, 'native-model'), PENDING)
