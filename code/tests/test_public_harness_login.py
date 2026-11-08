"""Authored protocol peer only. Native login and real account end-to-end NOT_RUN."""
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import public_harness_login as login
from public_harness_catalog_protocol import CATALOG
from test_public_harness_login_protocol import product, account


def response(request, scenario='success'):
    n=request.get('id');method=request.get('method')
    if method=='initialize':
        value=dict(protocolVersion=1,serverInfo=dict(name='@genie/agent-harness',version='synthetic'),capabilities={},authMethods=CATALOG)
    elif method=='product/read':value=product()
    elif method=='account/read':value=account(n==6 and scenario!='anonymous')
    elif method=='auth/status':value={'authenticated':n==7 and scenario!='anonymous'}
    elif method=='auth/login':
        if scenario=='timeout':return None
        if scenario=='url':return {'jsonrpc':'2.0','id':80,'method':'elicitation/create','params':{'mode':'url','message':'synthetic','requestId':5,'elicitationId':'synthetic','url':'https://example.invalid/auth?state=PRIVATE_SECRET'}}
        value={}
    elif method=='$/cancel_request':return {'jsonrpc':'2.0','id':5,'error':{'code':-32800,'message':'synthetic'}}
    elif n==80 and 'result' in request:return {'jsonrpc':'2.0','id':5,'result':{}}
    else:return None
    return {'jsonrpc':'2.0','id':n,'result':value}


class Pipe:
    def __init__(self):self.q=queue.Queue()
    def read(self,_):return self.q.get()
    def emit(self,data):self.q.put(data)


class Writer:
    def __init__(self,owner):self.owner=owner;self.pending=b'';self.saved=b''
    def write(self,data):
        data=bytes(data);self.saved+=data;self.pending+=data
        while b'\n' in self.pending:
            line,self.pending=self.pending.split(b'\n',1)
            reply=response(json.loads(line),self.owner.scenario)
            if reply:self.owner.stdout.emit(json.dumps(reply).encode()+b'\n')
        return len(data)
    def flush(self):pass
    def close(self):self.owner.finish()


class Owner:
    def __init__(self,scenario='success',exitcode=0,descendants=0):
        self.scenario=scenario;self.code=exitcode;self.descendants=descendants
        self.stdout=Pipe();self.stderr=Pipe();self.stdin=Writer(self);self.exited=False;self.killed=False
    def finish(self):
        if not self.exited:
            self.exited=True;self.stdout.emit(b'');self.stderr.emit(b'')
    def host_exited(self):return self.exited
    def active_processes(self):return 0 if self.killed else self.descendants if self.exited else 1
    def exit_code(self):return self.code
    def terminate(self,_):self.killed=True;self.finish();return {'owned_job_empty':True}
    def close(self,_):return {'owned_job_empty':True}


class Profile:
    def __init__(self,root):self.root=root;self.state='fresh';self.records=[];self.closed=False;self.bad=False
    def verify(self):
        if self.bad:raise ValueError('PRIVATE_PROFILE_ERROR')
        return True
    def environment(self,node):return {}
    def record(self,state,digest=None):self.records.append((state,digest));self.state=state
    @property
    def expected_account_digest(self):return self.records[-1][1] if self.records else None
    def quarantine(self):self.records.append(('unknown_retained',None));self.state='unknown_retained'
    def close(self):self.closed=True


class UI:
    def __init__(self,answers=None):self.actions=[];self.answers=answers or {};self.action=None
    def begin(self,action,event=None):self.actions.append(action);self.action=action
    def poll(self):return self.answers.get(self.action,True)
    def abandon(self):self.action=None


class LoginSupervisorTests(unittest.TestCase):
    def run_peer(self,owner=None,ui=None,**options):
        owner=owner or Owner();ui=ui or UI()
        with tempfile.TemporaryDirectory() as td:
            profile=Profile(Path(td));assets={k:Path(td)/k for k in ('executable','host_node','product_config')}
            class Job:
                @staticmethod
                def spawn(command,cwd,env):
                    self.assertEqual(profile.records,[('login_started',None)])
                    self.assertIn('--strict-mcp-config',command)
                    return owner
            result=login.supervise(assets,profile,ui,job_class=Job,timeout=options.pop('timeout',1),cancel_grace=.1,cleanup_timeout=.1,**options)
            self.assertNotIn('SYNTHETIC_ACCOUNT',json.dumps(result));self.assertFalse(result['real_model_ready'])
            return result,owner,profile,ui

    def test_success_requires_natural_close_and_profile_record(self):
        r,o,p,u=self.run_peer();self.assertTrue(r['success']);self.assertTrue(r['cleanup_complete']);self.assertTrue(r['natural_exit_zero']);self.assertTrue(r['natural_job_empty'])
        self.assertEqual(p.records[-1][0],'authenticated_confirmed');self.assertEqual(len(p.records[-1][1]),64);self.assertTrue(p.closed)
        methods=[json.loads(x).get('method') for x in o.stdin.saved.splitlines()]
        self.assertEqual(methods,['initialize','initialized','product/read','account/read','auth/status','auth/login','account/read','auth/status'])
        self.assertEqual(u.actions,['confirm_login_intent','confirm_account_identity'])

    def test_identity_rejected_no_digest(self):
        r,o,p,u=self.run_peer(ui=UI({'confirm_account_identity':False}));self.assertFalse(r['success']);self.assertEqual(p.records[-1],('identity_rejected',None))

    def test_intent_refused_never_login(self):
        r,o,p,u=self.run_peer(ui=UI({'confirm_login_intent':False}));self.assertFalse(r['success']);self.assertNotIn(b'auth/login',o.stdin.saved)

    def test_timeout_cancels_then_retains_no_digest(self):
        r,o,p,u=self.run_peer(Owner('timeout'),timeout=.03);self.assertFalse(r['success']);self.assertIn(b'$/cancel_request',o.stdin.saved);self.assertIsNone(p.records[-1][1]);self.assertTrue(r['cleanup_complete'])

    def test_url_private_handoff_does_not_print_or_reopen(self):
        with patch('sys.stdout',new=io.StringIO()) as terminal:
            r,o,p,u=self.run_peer(Owner('url'))
            self.assertTrue(r['success']);self.assertIn('confirm_url_handoff',u.actions)
            self.assertNotIn('PRIVATE_SECRET',terminal.getvalue());self.assertNotIn('PRIVATE_SECRET',json.dumps(r))

    def test_keyboard_interrupt_sends_cancellation_before_cleanup(self):
        class InterruptUI(UI):
            def poll(self):
                if self.action=='confirm_url_handoff':raise KeyboardInterrupt()
                return True
        r,o,p,u=self.run_peer(Owner('url'),ui=InterruptUI())
        self.assertFalse(r['success']);self.assertIn(b'$/cancel_request',o.stdin.saved);self.assertIsNone(p.records[-1][1]);self.assertTrue(r['cleanup_complete'])

    def test_blocked_writer_released_by_owned_cleanup(self):
        release=threading.Event();o=Owner('timeout');original=o.stdin.write
        def blocked(value):release.wait(1);return original(value)
        o.stdin.write=blocked;terminate=o.terminate
        def finish(timeout):release.set();return terminate(timeout)
        o.terminate=finish
        r,o,p,u=self.run_peer(o,timeout=.02)
        self.assertFalse(r['success']);self.assertTrue(r['io_threads_stopped']);self.assertTrue(p.closed)

    def test_acl_verification_failure_never_stores_digest_or_releases_lock(self):
        o=Owner();close=o.close
        with patch.object(Profile,'verify',side_effect=[True,ValueError('PRIVATE_SECRET')]):
            before=len(login.RETAINED);r,o,p,u=self.run_peer(o)
            self.assertFalse(r['success']);self.assertFalse(p.closed);self.assertEqual(p.records,[('login_started',None),('unknown_retained',None)])
            self.assertEqual(len(login.RETAINED),before+1);login.RETAINED.pop()

    def test_nonblocking_tty_yes_no_only(self):
        class Console:
            def __init__(self,chars):self.chars=list(chars)
            def kbhit(self):return bool(self.chars)
            def getwch(self):return self.chars.pop(0)
        for chars,expected in [('yes\r',True),('no\r',False),('password\r',False),('yes\x1b\r',False)]:
            ui=object.__new__(login.WindowsTTY);ui.console=Console(chars);ui.pending=True;ui.buffer=''
            self.assertEqual(ui.poll(),expected)
        ui=object.__new__(login.WindowsTTY);ui.console=Console('');ui.pending=True;ui.buffer=''
        self.assertIsNone(ui.poll())

    def test_nonzero_and_descendants_never_confirm(self):
        for owner in (Owner(exitcode=1),Owner(descendants=1)):
            r,o,p,u=self.run_peer(owner);self.assertFalse(r['success']);self.assertEqual(p.records[-1],('unknown_retained',None))

    def test_no_account_ack_is_not_success(self):
        r,o,p,u=self.run_peer(Owner('anonymous'));self.assertFalse(r['success']);self.assertEqual(p.records[-1],('unauthenticated',None))

    def test_cleanup_uncertain_retains_profile_and_owner(self):
        o=Owner();o.close=lambda _: {'owned_job_empty':False}
        before=len(login.RETAINED);r,o,p,u=self.run_peer(o)
        self.assertFalse(r['success']);self.assertFalse(p.closed);self.assertEqual(p.state,'login_started');self.assertEqual(len(login.RETAINED),before+1)
        login.RETAINED.pop()

    def test_stream_bound(self):
        o=Owner();o.stderr.emit(b'PRIVATE_SECRET'*(login.STREAM_LIMIT//10))
        r,o,p,u=self.run_peer(o);self.assertFalse(r['success']);self.assertTrue(r['cleanup_complete']);self.assertNotIn('PRIVATE_SECRET',json.dumps(r))

    def test_prompt_discards_stale_yes_and_accepts_only_new_input(self):
        class Console:
            def __init__(self,chars):self.chars=list(chars)
            def kbhit(self):return bool(self.chars)
            def getwch(self):return self.chars.pop(0)
        ui=object.__new__(login.WindowsTTY);ui.console=Console('yes\ryes\r');ui.pending=False;ui.buffer=''
        with patch('sys.stdout',new=io.StringIO()) as out:
            ui.begin('confirm_account_identity',{'account':{'id':'synthetic'}})
            self.assertIsNone(ui.poll());ui.console.chars.extend('yes\r');self.assertTrue(ui.poll())
            self.assertNotIn('yes\ryes',out.getvalue())
        ui.console=Console('x'*257)
        with patch('sys.stdout',new=io.StringIO()) as out,self.assertRaises(ValueError):ui.begin('confirm_login_intent')
        self.assertEqual(out.getvalue(),'')

    def test_post_record_failure_revokes_digest_and_retains_owner(self):
        original=Profile.record
        def record(p,state,digest=None):
            original(p,state,digest)
            if state=='authenticated_confirmed':raise ValueError('PRIVATE_POST_RECORD')
        before=len(login.RETAINED)
        with patch.object(Profile,'record',record):r,o,p,u=self.run_peer()
        self.assertFalse(r['success']);self.assertTrue(r['quarantine_attempted']);self.assertTrue(r['quarantine_verified'])
        self.assertEqual(p.records[-1],('unknown_retained',None));self.assertFalse(p.closed)
        self.assertEqual(len(login.RETAINED),before+1);login.RETAINED.pop()

    def test_post_record_explicit_verification_failure_quarantines(self):
        before=len(login.RETAINED)
        with patch.object(Profile,'verify',side_effect=[True,True,ValueError('PRIVATE_VERIFY'),True]):
            r,o,p,u=self.run_peer()
        self.assertFalse(r['success']);self.assertTrue(r['quarantine_verified'])
        self.assertEqual(p.records[-1],('unknown_retained',None));self.assertFalse(p.closed)
        self.assertEqual(len(login.RETAINED),before+1);login.RETAINED.pop()

    def test_failed_quarantine_never_unlocks(self):
        original=Profile.record
        def record(p,state,digest=None):
            original(p,state,digest)
            if state=='authenticated_confirmed':raise ValueError()
        before=len(login.RETAINED)
        with patch.object(Profile,'record',record),patch.object(Profile,'quarantine',side_effect=ValueError()):r,o,p,u=self.run_peer()
        self.assertFalse(r['success']);self.assertFalse(r['quarantine_verified']);self.assertFalse(p.closed)
        self.assertEqual(len(login.RETAINED),before+1);login.RETAINED.pop()

    def test_labels_no_terminal_controls(self):
        value=login.safe_label('name\x1b[31m\u202eX\n')
        self.assertNotIn('\x1b',value);self.assertNotIn('\n',value);self.assertNotIn('\u202e',value)

    def test_explain_never_verify_spawn_or_profile(self):
        with patch('sys.stdout',new=io.StringIO()),patch.object(login.OwnedWindowsJob,'spawn',side_effect=AssertionError('must not spawn')):
            self.assertEqual(login.main(['--explain']),0)

    def test_cli_no_bypass_and_no_non_tty(self):
        with patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(login.main(['--assets-root','x','--profile-parent','y']),1)
        with patch('sys.stderr',new=io.StringIO()) as err,patch('sys.stdout',new=io.StringIO()) as out:
            self.assertEqual(login.main(['--scripted','PRIVATE_ARG_TOKEN']),1)
            self.assertNotIn('PRIVATE_ARG_TOKEN',err.getvalue()+out.getvalue())
            self.assertNotIn('--scripted',err.getvalue()+out.getvalue())

    def test_cli_broken_output_still_holds_retained(self):
        marker=object()
        with patch.object(login,'RETAINED',[marker]),patch.object(login,'_hold_retained') as hold,patch('builtins.print',side_effect=BrokenPipeError('PRIVATE_OUTPUT')):
            self.assertEqual(login.main(['--explain']),1)
            hold.assert_called_once_with()
            self.assertEqual(login.RETAINED,[marker])

    def test_cli_parse_error_holds_without_echo_even_if_output_broken(self):
        with patch.object(login,'RETAINED',[]),patch.object(login,'_hold_retained') as hold,patch('builtins.print',side_effect=BrokenPipeError()):
            self.assertEqual(login.main(['--unknown','PRIVATE_TOKEN']),1)
            hold.assert_called_once_with()
            self.assertEqual(login.RETAINED,[])

    def test_cli_normal_return_runs_common_hold(self):
        with patch.object(login,'RETAINED',[]),patch.object(login,'_hold_retained') as hold,patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(login.main(['--explain']),0)
            hold.assert_called_once_with()
        with patch.object(login,'RETAINED',[]),patch.object(login.time,'sleep',side_effect=AssertionError('must not wait')):
            login._hold_retained()

    def test_hold_retained_ignores_keyboard_interrupt(self):
        retained=[object()]
        def tick(_):
            if tick.first:
                tick.first=False
                raise KeyboardInterrupt()
            retained.clear()
        tick.first=True
        with patch.object(login,'RETAINED',retained),patch.object(login.time,'sleep',side_effect=tick) as sleep:
            login._hold_retained()
            self.assertEqual(sleep.call_count,2)

    @unittest.skipUnless(os.name=='nt','Windows owned authored process only')
    def test_windows_authored_peer(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'workspace').mkdir();p=Profile(root)
            assets={k:root/k for k in ('executable','host_node','product_config')}
            class Job:
                @staticmethod
                def spawn(command,cwd,env):
                    return login.OwnedWindowsJob.spawn([sys.executable,str(Path(__file__).resolve()),'--authored-peer'],cwd,dict(os.environ))
            r=login.supervise(assets,p,UI(),job_class=Job,timeout=10,cancel_grace=2,cleanup_timeout=2)
            self.assertTrue(r['success'],r)


def authored_peer():
    for line in sys.stdin.buffer:
        out=response(json.loads(line))
        if out:
            sys.stdout.buffer.write(json.dumps(out).encode()+b'\n');sys.stdout.buffer.flush()

if __name__=='__main__':
    if sys.argv[1:]==['--authored-peer']:authored_peer()
    else:unittest.main()
