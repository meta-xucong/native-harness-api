"""Only synthetic metadata and self-authored child processes. Real catalog NOT_RUN."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import public_harness_models as models
from native_harness_api.public_sfe_session import account_identity_digest
from native_harness_api.supervised_acp_transport import SupervisedACPTransport
from test_public_sfe_owned_session import SyntheticTransport

CHILD=Path(__file__).parent/'fakes/public_models_child.py'
DIGEST=account_identity_digest({'id':'synthetic-account','authMethodId':'workbuddy-desktop'})

def options():return [{'configId':'model','name':'Model','type':'select','currentValue':'a','options':[{'value':'a','name':'A'},{'value':'b','name':'B'}]}]

class Profile:
    def __init__(self,root):self.root=root;self.state='authenticated_confirmed';self.expected_account_digest=DIGEST;self.closed=False;self.records=[]
    def verify(self):return True
    def begin_execution(self):self.records.append('begin');self.state='execution_started'
    def environment(self,node):return dict(os.environ)
    def finish_execution(self,*,cleanup_confirmed):self.records.append(cleanup_confirmed);self.state='authenticated_confirmed'
    def quarantine(self):self.records.append('quarantine');self.state='execution_unknown';self.expected_account_digest=None
    def close(self):self.closed=True

class ModelTests(unittest.TestCase):
    def run_engine(self,mode='success',transport=None):
        with tempfile.TemporaryDirectory() as td:
            engine=models.ModelsOwnedSession(transport or SyntheticTransport());env=dict(os.environ,MODELS_FIXTURE=mode)
            result=engine.discover([sys.executable,'-u',str(CHILD.resolve())],Path(td),env,workspace=td,expected_account_digest=DIGEST,timeout=2,cleanup_timeout=.5)
            self.assertFalse(result['prompt_sent']);self.assertFalse(result['model_selected']);self.assertFalse(result['real_model_ready'])
            return result,engine

    def test_owned_discovery_has_real_catalog_and_closes_no_prompt(self):
        r,e=self.run_engine();self.assertTrue(r['success'],r);self.assertTrue(r['session_closed']);self.assertTrue(r['cleanup_confirmed'])
        self.assertEqual(r['catalog']['models'][1],{'id':'synthetic-b','name':'Synthetic B'})
        self.assertFalse(e.protocol.coordinator.prompt_intent_issued)
        self.assertIsNone(e.protocol.coordinator.model.requested)

    def test_malformed_interaction_late_nonzero_closehang_fail(self):
        for mode in ('malformed','interaction','late','nonzero','close_hang'):
            r,e=self.run_engine(mode);self.assertFalse(r['success'],mode);self.assertIsNone(r['catalog']);self.assertNotIn('PRIVATE_SECRET',json.dumps(r))

    def test_grouped_and_invalid_choices(self):
        x=options();x[0]['options']=[{'groupId':'g','name':'Group','options':x[0]['options']}]
        self.assertEqual(len(models.project_models(x)['models']),2)
        for value in ('https://example.invalid?token=x','a\x1b','a b',''):
            x=options();x[0]['options'][0]['value']=value
            with self.assertRaises(ValueError):models.project_models(x)
        x=options();x[0]['options'].append({'value':'a','name':'duplicate'})
        with self.assertRaises(ValueError):models.project_models(x)

    def test_profile_gate_precedes_launch_and_success_restores(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'workspace').mkdir();profile=Profile(root)
            class Transport(SyntheticTransport):
                def start(self,command,cwd,env):
                    self_test.assertEqual(profile.records,['begin'])
                    return super().start([sys.executable,'-u',str(CHILD.resolve())],cwd,env)
            self_test=self
            assets={k:root/k for k in ('executable','host_node','product_config')}
            r=models.discover_models(assets,profile,transport=Transport(),timeout=2,cleanup_timeout=.5)
            self.assertTrue(r['success']);self.assertEqual(profile.records,['begin',True]);self.assertTrue(profile.closed)

    def test_uncertain_cleanup_quarantines_and_retains_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'workspace').mkdir();profile=Profile(root)
            class Transport(SyntheticTransport):
                def start(self,command,cwd,env):return super().start([sys.executable,'-u',str(CHILD.resolve())],cwd,env)
                def close(self,timeout=1):super().close(timeout);return {'cleanup_complete':False}
            before=len(models.RETAINED)
            r=models.discover_models({k:root/k for k in ('executable','host_node','product_config')},profile,transport=Transport(),timeout=2,cleanup_timeout=.5)
            self.assertFalse(r['success']);self.assertEqual(profile.records,['begin','quarantine']);self.assertFalse(profile.closed)
            self.assertEqual(len(models.RETAINED),before+1);models.RETAINED.pop()

    def test_unconfirmed_profile_never_starts_and_releases_unstarted_lock(self):
        with tempfile.TemporaryDirectory() as td:
            profile=Profile(Path(td));profile.state='execution_unknown'
            transport=SyntheticTransport()
            r=models.discover_models({},profile,transport=transport)
            self.assertFalse(r['success']);self.assertEqual(transport.starts,0);self.assertTrue(profile.closed)
            self.assertEqual(profile.records,[])

    def test_wrong_live_account_never_creates_catalog(self):
        with tempfile.TemporaryDirectory() as td:
            engine=models.ModelsOwnedSession(SyntheticTransport())
            r=engine.discover([sys.executable,'-u',str(CHILD.resolve())],Path(td),dict(os.environ),workspace=td,expected_account_digest='0'*64,timeout=.1,cleanup_timeout=.2)
            self.assertFalse(r['success']);self.assertIsNone(r['catalog']);self.assertIsNone(engine.protocol.coordinator.session_id)

    def test_postfinish_verification_failure_quarantines(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'workspace').mkdir();profile=Profile(root)
            class Transport(SyntheticTransport):
                def start(self,command,cwd,env):return super().start([sys.executable,'-u',str(CHILD.resolve())],cwd,env)
            with patch.object(profile,'verify',side_effect=[True,True,True,ValueError('PRIVATE'),True]):
                r=models.discover_models({k:root/k for k in ('executable','host_node','product_config')},profile,transport=Transport(),timeout=2,cleanup_timeout=.5)
            self.assertFalse(r['success']);self.assertIsNone(r['catalog']);self.assertEqual(profile.records,['begin',True,'quarantine'])
            self.assertIsNone(profile.expected_account_digest);self.assertTrue(profile.closed)

    def test_explain_and_arg_errors_no_native(self):
        with patch('sys.stdout',new=io.StringIO()) as out,patch('sys.stderr',new=io.StringIO()) as err:
            self.assertEqual(models.main(['--explain']),0)
            self.assertEqual(models.main(['--unknown','PRIVATE_SECRET']),1)
            self.assertNotIn('PRIVATE_SECRET',out.getvalue()+err.getvalue())

    @unittest.skipUnless(os.name=='nt','Windows owned authored peer')
    def test_windows_owned_peer(self):
        r,e=self.run_engine(transport=SupervisedACPTransport());self.assertTrue(r['success'],r)


class TargetExportTests(unittest.TestCase):
    def result(self):
        return {'success':True,'cleanup_confirmed':True,'profile_restored':True,'profile_closed':True,
                'catalog':{'models':[{'id':'native-model','name':'Fixture model'}]}}

    def export(self,result=None,**changes):
        options=dict(profile_root=r'C:\Profiles\profile-1',profile_id='profile-1',account_fingerprint=DIGEST,
                     assets_root=r'C:\Native\assets',native_model='native-model',public_alias='workbuddy-test')
        options.update(changes)
        return models.export_target(self.result() if result is None else result,**options)

    def test_catalog_and_confirmed_metadata_generate_exact_recipe(self):
        from native_harness_api.harness_registry import ExecutionTarget, ADAPTERS
        exported=self.export();parsed=ExecutionTarget.from_value(exported)
        self.assertEqual(parsed.recipe['profile_id'],'profile-1')
        self.assertEqual(parsed.recipe['account_fingerprint'],DIGEST)
        self.assertEqual(parsed.recipe['asset_identity'],ADAPTERS['workbuddy.public-sfe.v1']['asset_identity'])
        self.assertEqual(parsed.binding.native_model,'native-model')

    def test_unknown_model_or_incomplete_discovery_cannot_export(self):
        with self.assertRaises(ValueError):self.export(native_model='unlisted')
        for key in ('success','cleanup_confirmed','profile_restored','profile_closed'):
            result=self.result();result[key]=False
            with self.subTest(key=key),self.assertRaises(ValueError):self.export(result)
        with self.assertRaises(ValueError):self.export(public_alias='codex-wrong')

    def test_flags_must_be_paired_before_native_entry(self):
        with patch('public_harness_runtime_assets.verify_runtime_assets',side_effect=AssertionError('No launch')) as verify:
            with patch('sys.stdout',new=io.StringIO()):
                self.assertEqual(models.main(['--target-model','native-model']),1)
                self.assertEqual(models.main(['--public-alias','workbuddy-test']),1)
            verify.assert_not_called()

    def test_cli_target_stdout_is_only_json_from_frozen_profile(self):
        from pathlib import PureWindowsPath
        from types import SimpleNamespace
        from contextlib import ExitStack
        class TTY(io.StringIO):
            def isatty(self):return True
        profile=Profile(PureWindowsPath(r'C:\Profiles\profile-1'))
        def discover(*args):
            profile.expected_account_digest='b'*64
            return self.result()
        out=TTY();err=TTY()
        with ExitStack() as stack:
            stack.enter_context(patch.object(models,'os',SimpleNamespace(name='nt')))
            stack.enter_context(patch.object(models,'Path',PureWindowsPath))
            stack.enter_context(patch('sys.stdin',TTY()))
            stack.enter_context(patch('sys.stdout',out));stack.enter_context(patch('sys.stderr',err))
            stack.enter_context(patch('public_harness_runtime_assets.verify_runtime_assets',return_value={}))
            stack.enter_context(patch('public_harness_login_profile.open_profile',return_value=profile))
            stack.enter_context(patch.object(models,'discover_models',side_effect=discover))
            code=models.main(['--assets-root',r'C:\Native\assets','--profile',str(profile.root),
                              '--target-model','native-model','--public-alias','workbuddy-test'])
        self.assertEqual(code,0)
        emitted=json.loads(out.getvalue())
        self.assertEqual(set(emitted),{'recipe','binding','assets_root'})
        self.assertEqual(emitted['recipe']['account_fingerprint'],DIGEST)
        self.assertIn('ONE empty native session',err.getvalue())

if __name__=='__main__':unittest.main()
