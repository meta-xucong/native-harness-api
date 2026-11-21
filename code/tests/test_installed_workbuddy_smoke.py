"""Offline algorithm tests, not Windows/vendor execution evidence."""
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('installed_smoke', Path(__file__).parents[1] / 'tools/installed_workbuddy_smoke.py')
s = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s)
GOOD = json.dumps({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':1,'agentCapabilities':{}}}).encode()+b'\n'


class Owner:
    def __init__(self, out=GOOD, code=0):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(out)
        self.stderr = io.BytesIO()
        self.code = code
        self.killed = False
        self.closed = False
    def host_exited(self): return True
    def exit_code(self): return self.code
    def terminate(self, timeout):
        self.killed = True
        return {'owned_job_empty':True}
    def close(self, timeout):
        self.closed = True
        return {'owned_job_empty':True}


class TrialTests(unittest.TestCase):
    def tearDown(self): s.RETAINED.clear()
    def phase(self, owner, init=True):
        with patch.object(s.OwnedWindowsJob, 'spawn', return_value=owner) as spawn:
            result = s.phase(['fixture'], Path('.'), {}, init, timeout=.2, cleanup_timeout=.2)
        self.assertEqual(spawn.call_count, 1)
        return result
    def test_failure_code_is_fixed_and_no_exception_text(self):
        self.assertEqual(s.failure_code(s.TrialError('DEADLINE')), 'DEADLINE')
        for exc in (OSError('secret path'), s.TrialError('secret token'),
                    s.TrialError('DEADLINE', 'secret'), RuntimeError('IO_FAILED')):
            self.assertEqual(s.failure_code(exc), 'INTERNAL_FAILURE')
        self.assertEqual(self.phase(Owner(code=8))['failure_code'], 'NONZERO_EXIT')
        self.assertEqual(self.phase(Owner(b'login needed'),False)['failure_code'], 'HELP_NOT_OBSERVED')
    def test_valid_and_exact_one_request(self):
        owner = Owner()
        result = self.phase(owner)
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertTrue(result['cleanup_complete'])
        self.assertEqual(json.loads(owner.stdin.getvalue()), s.INITIALIZE)
        self.assertEqual(owner.stdin.getvalue().count(b'\n'), 1)
    def test_invalid_extra_notification_and_residual(self):
        for output in (b'', GOOD+b'{}\n', GOOD+b'x', GOOD+b'\n',
                       b'{"jsonrpc":"2.0","method":"permission"}\n',
                       GOOD.replace(b'"id": 1', b'"id": true'),
                       GOOD.replace(b'"id": 1', b'"id": 1,"id":1')):
            with self.subTest(output=output):
                self.assertEqual(self.phase(Owner(output))['status'], 'FAILED')
    def test_nonzero_and_empty_help(self):
        self.assertEqual(self.phase(Owner(code=3))['status'], 'FAILED')
        self.assertEqual(self.phase(Owner(b''), False)['status'], 'FAILED')
        self.assertEqual(self.phase(Owner(b'--help --acp'), False)['status'], 'OBSERVED')
    def test_help_markers_required(self):
        for output in (b'login required', b'error', b'--help only', b'--acp only'):
            self.assertEqual(self.phase(Owner(output), False)['status'], 'FAILED')
        owner = Owner(b'--help')
        owner.stderr = io.BytesIO(b'--acp')
        self.assertEqual(self.phase(owner,False)['status'], 'OBSERVED')
    def test_windows_path_aliases(self):
        for raw in ('relative', 'C:relative', r'\\server\share\dir',
                    r'\\?\C:\dir', r'\\.\C:\dir', r'C:\dir:stream',
                    r'C:\dir\..\other', 'C:\\trailing.', r'C:\NUL', '/tmp/x'):
            with self.subTest(raw=raw), self.assertRaises(s.TrialError): s.windows_path(raw)
        self.assertEqual(str(s.windows_path(r'C:\Safe Space\试验')), r'C:\Safe Space\试验')
    def test_overlap_and_nonchild_rejected_before_stat(self):
        for install, work, root in (
            (r'C:\Vendor',r'c:\vendor',r'C:\vendor\trial'),
            (r'C:\Vendor',r'C:\Vendor\Work',r'C:\Vendor\Work\trial'),
            (r'C:\Work\Vendor',r'C:\Work',r'C:\Work\trial'),
            (r'C:\Vendor',r'C:\Work',r'C:\Elsewhere\trial')):
            with self.assertRaises(s.TrialError): s.validate_paths(install,work,root)
    def test_canonical_aliases_rejected(self):
        class AliasPath(s.PureWindowsPath):
            mapping = {}
            def resolve(self, strict=False):
                self.assert_strict = strict
                if not strict: raise AssertionError('strict required')
                return s.PureWindowsPath(self.mapping.get(str(self), str(self)))
            def is_dir(self): return True
            def exists(self): return False
        for mapped_install, mapped_parent in (
                (r'C:\Physical\Work', r'C:\Physical\Work'),
                (r'C:\Physical', r'C:\Physical\Work'),
                (r'C:\Physical\Work\Vendor', r'C:\Physical\Work'),
                (r'C:\Elsewhere', r'C:\WrongParent')):
            AliasPath.mapping = {r'C:\VendorAlias':mapped_install,
                r'D:\Work':r'C:\Physical\Work'}
            class RunPath(AliasPath):
                @property
                def parent(self):
                    class Parent(AliasPath):
                        def resolve(self, strict=False):
                            return s.PureWindowsPath(mapped_parent)
                    return Parent(r'D:\Work')
            with patch.object(s,'checked_ancestors'), self.assertRaises(s.TrialError):
                s.validate_paths(AliasPath(r'C:\VendorAlias'), AliasPath(r'D:\Work'), RunPath(r'D:\Work\trial'))
    def test_reparse_parent_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            (parent/'real').mkdir()
            (parent/'link').symlink_to(parent/'real',target_is_directory=True)
            with self.assertRaises(s.TrialError): s.prepare(parent/'link'/'trial')
            self.assertFalse((parent/'real'/'trial').exists())
            with self.assertRaises(s.TrialError): s.checked_ancestors(parent/'link')
    def test_cleanup_failure_retains_owner(self):
        owner = Owner()
        owner.close = lambda _: (_ for _ in ()).throw(OSError('secret'))
        result = self.phase(owner)
        self.assertEqual(result['status'], 'FAILED')
        self.assertFalse(result['cleanup_complete'])
        self.assertIn(owner, s.RETAINED)
        self.assertNotIn('secret', json.dumps(result))
    def test_partial_spawn_retains_owner(self):
        owner = Owner()
        class Failed(Exception): pass
        exc = Failed('secret')
        exc.owner = owner
        with patch.object(s.OwnedWindowsJob, 'spawn', side_effect=exc):
            result = s.phase([], Path('.'), {}, True, timeout=.05)
        self.assertEqual(result['status'], 'FAILED')
        self.assertTrue(owner.closed)
    def test_root_and_stage_are_one_shot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'trial'
            env = s.prepare(root)
            self.assertTrue((root/'intent.json').exists())
            with self.assertRaises(FileExistsError): s.prepare(root)
            s.stage_intent(root,'help')
            with self.assertRaises(FileExistsError): s.stage_intent(root,'help')
            self.assertEqual(env['ELECTRON_RUN_AS_NODE'], '1')
            self.assertNotIn('NODE_OPTIONS',env)
    def test_help_failure_no_second_spawn_or_initialize_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'trial'
            with patch.object(s, 'PLATFORM', 'nt'), patch.object(s,'validate_paths'), patch.object(s,'validate_install'), patch.object(s,'phase',return_value={'status':'FAILED'}) as phase:
                result = s.run(Path(directory), root, Path(directory))
            self.assertEqual(phase.call_count,1)
            self.assertFalse((root/'initialize.intent').exists())
            self.assertEqual(result['status'],'FAILED')
    def test_commands_and_no_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'trial'
            with patch.object(s,'PLATFORM','nt'), patch.object(s,'validate_paths'), patch.object(s,'validate_install'), patch.object(s,'phase',side_effect=[{'status':'OBSERVED'},{'status':'FAILED'}]) as phase:
                result = s.run(Path(directory),root,Path(directory))
            self.assertEqual(phase.call_count,2)
            self.assertEqual(phase.call_args_list[0].args[0][-1], '--help')
            self.assertEqual(phase.call_args_list[1].args[0][-1], '--acp')
            self.assertEqual(len(phase.call_args_list[1].args[0]),3)
            self.assertTrue((root/'initialize.intent').exists())
            self.assertEqual(result['status'],'FAILED')
    def test_install_hash_mismatch_no_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'test').write_bytes(b'bad')
            with patch.object(s,'PINNED', {'test':'0'*64}):
                with self.assertRaises(s.TrialError): s.validate_install(root)
    def test_output_bound(self):
        self.assertEqual(self.phase(Owner(b'a'*(s.LIMIT+1)))['status'],'FAILED')
    def test_thread_start_failure_cleanup(self):
        owner = Owner()
        with patch.object(s.threading.Thread,'start', side_effect=RuntimeError('secret')):
            result = self.phase(owner)
        self.assertEqual(result['status'],'FAILED')
        self.assertTrue(owner.killed)
        self.assertFalse(result['io_threads_stopped'])
        self.assertIn(owner, s.RETAINED)
    def test_deadline_blocked_writer(self):
        owner = Owner()
        gate = threading.Event()
        class Writer:
            def write(self, raw): gate.wait(1); return len(raw)
            def flush(self): pass
        owner.stdin = Writer()
        owner.host_exited = lambda: owner.killed
        original = owner.terminate
        def terminate(timeout):
            gate.set()
            return original(timeout)
        owner.terminate = terminate
        result = self.phase(owner)
        self.assertEqual(result['status'], 'FAILED')
        self.assertTrue(owner.killed)
    def test_initialize_no_response_deadline(self):
        owner = Owner(b'')
        owner.host_exited = lambda: owner.killed
        self.assertEqual(self.phase(owner)['status'], 'FAILED')
    def test_late_cleanup_bytes_rejected(self):
        owner = Owner()
        gate = threading.Event()
        class Stream:
            count = 0
            def read(self, size):
                self.count += 1
                if self.count == 1: return GOOD
                if self.count == 2:
                    gate.wait(1)
                    return b'{"method":"late"}\n'
                return b''
        owner.stdout=Stream()
        owner.host_exited=lambda:owner.killed
        original=owner.terminate
        def terminate(timeout):
            gate.set()
            return original(timeout)
        owner.terminate=terminate
        # Keep stderr open until cleanup, matching a live native process.
        class Err:
            def read(self,size): gate.wait(1); return b''
        owner.stderr=Err()
        self.assertEqual(self.phase(owner)['status'],'FAILED')


@unittest.skipUnless(os.name == 'nt', 'Real Windows owned Job / synthetic Python only')
class WindowsPhaseTests(unittest.TestCase):
    def run_phase(self, program, initialize):
        with tempfile.TemporaryDirectory() as directory:
            env = {'SystemRoot': os.environ.get('SystemRoot', r'C:\Windows'),
                   'TEMP': directory, 'TMP': directory}
            result = s.phase([sys.executable, '-c', program], Path(directory), env,
                             initialize, timeout=5, cleanup_timeout=5)
        self.assertTrue(result['cleanup_complete'])
        self.assertTrue(result['io_threads_stopped'])
        return result

    def test_real_short_path_overlap_rejected(self):
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True).GetShortPathNameW
        api.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        api.restype = wintypes.DWORD
        with tempfile.TemporaryDirectory() as directory:
            installation = Path(directory) / 'Long Installation Directory For Alias Test'
            installation.mkdir()
            work = installation / 'Work Directory'
            work.mkdir()
            buf = ctypes.create_unicode_buffer(32768)
            size = api(str(installation), buf, len(buf))
            if not size or size >= len(buf):
                self.skipTest('Filesystem short-path lookup unavailable')
            alias = Path(buf.value)
            if str(alias).casefold() == str(installation).casefold():
                self.skipTest('Filesystem did not create an 8.3 alias')
            # Different lexical names still refer to a physically containing install.
            with self.assertRaises(s.TrialError):
                s.validate_paths(alias, work, work / 'trial')

    def test_real_python_help(self):
        result = self.run_phase("print('--help --acp')", False)
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertTrue(result['natural_exit_zero'])

    def test_real_python_unique_initialize(self):
        program = ("import sys,json; request=json.loads(sys.stdin.readline()); "
                   "assert request['method']=='initialize' and request['id']==1; "
                   "print(" + repr(GOOD.decode().strip()) + ",flush=True)")
        result = self.run_phase(program, True)
        self.assertEqual(result['status'], 'OBSERVED')
        self.assertTrue(result['natural_exit_zero'])

    def test_real_python_extra_frame_rejected(self):
        program = ("import sys; sys.stdin.readline(); print(" + repr(GOOD.decode().strip()) +
                   ",flush=True); print('{\"jsonrpc\":\"2.0\",\"method\":\"late\"}',flush=True)")
        result = self.run_phase(program, True)
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(result['failure_code'], 'PROTOCOL_REJECTED')


if __name__ == '__main__': unittest.main()
