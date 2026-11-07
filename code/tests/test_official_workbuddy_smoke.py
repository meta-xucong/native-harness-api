"""Fixture-only smoke safety tests. Never downloads or runs WorkBuddy."""
import importlib.util
import io
import json
import hashlib
import struct
import zlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'tools/official_workbuddy_smoke.py'
spec = importlib.util.spec_from_file_location('official_smoke', PATH)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
SmokeError = smoke.SmokeError
archive_inventory = smoke.archive_inventory
INITIALIZE = smoke.INITIALIZE
validate_initialize = smoke.validate_initialize
isolated_env = smoke.isolated_env
observe = smoke.observe

class OfflineTests(unittest.TestCase):
    def test_inventory(self):
        self.assertEqual(archive_inventory('header\n----------\nPath = app/file\nSize = 10\n'), (1, 10))
        for name in ('../bad', 'C:\\bad', '/bad'):
            with self.assertRaises(SmokeError):
                archive_inventory('header\n----------\nPath = ' + name + '\nSize = 1\n')
    def test_no_rpc_other_than_initialize(self):
        self.assertEqual(INITIALIZE['method'], 'initialize')
        self.assertFalse(INITIALIZE['params']['clientCapabilities']['terminal'])
        for frame in ({'method': 'authenticate'}, {'method': 'session/request_permission'},
                      {'id': 1, 'error': {}}, {'jsonrpc': '2.0', 'id': True, 'result': {}}):
            with self.assertRaises(SmokeError):
                validate_initialize(frame)
        result = validate_initialize({'jsonrpc':'2.0', 'id':1, 'result':{
            'protocolVersion':1, 'agentCapabilities':{}, 'agentInfo':{'name':'DO_NOT_PRINT'}, 'authMethods':[{'id':'DO_NOT_PRINT'}]}})
        self.assertNotIn('DO_NOT_PRINT', json.dumps(result))
    def test_env_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = isolated_env(Path(tmp))
            self.assertNotIn('GITHUB_TOKEN', env)
            self.assertNotIn('HTTPS_PROXY', env)
            self.assertNotIn('NODE_OPTIONS', env)
            self.assertNotEqual(env['HOME'], os.environ.get('HOME'))
    def test_mock_initialize_once(self):
        class Owner:
            stdin = io.BytesIO()
            stdout = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1,"agentCapabilities":{}}}\n')
            stderr = io.BytesIO()
        owner = Owner()
        summary, _, threads, _ = observe(owner, 'initialize', 1)
        for thread in threads:
            thread.join(1)
        self.assertEqual(summary['protocol_version'], 1)
        self.assertEqual(len(owner.stdin.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(owner.stdin.getvalue())['method'], 'initialize')


    def test_output_limit(self):
        class Owner:
            stdin = io.BytesIO()
            stdout = io.BytesIO(b'x' * (smoke.MAX_OUTPUT + 8192))
            stderr = io.BytesIO()
        with self.assertRaises(SmokeError):
            observe(Owner(), 'initialize', 1)

    def test_deadline(self):
        class Owner:
            stdin = io.BytesIO()
            stdout = io.BytesIO()
            stderr = io.BytesIO()
            def host_exited(self):
                return False
        with self.assertRaisesRegex(SmokeError, 'NATIVE_DEADLINE'):
            observe(Owner(), 'help', .02)

    def test_permission_request_never_answered(self):
        class Owner:
            stdin = io.BytesIO()
            stdout = io.BytesIO(b'{"jsonrpc":"2.0","id":8,"method":"session/request_permission"}\n')
            stderr = io.BytesIO()
        owner = Owner()
        with self.assertRaisesRegex(SmokeError, 'UNSOLICITED_RPC'):
            observe(owner, 'initialize', 1)
        self.assertEqual(len(owner.stdin.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(owner.stdin.getvalue())['method'], 'initialize')

    def test_exact_hash_pins_and_stage_allowlist(self):
        self.assertEqual(smoke.INSTALLER_SIZE, 450174440)
        self.assertEqual(smoke.INSTALLER_SHA, 'b5edb52bd6c5b6c7296cf2fff697b019c86d11992a8a814c3010d611896852f9')
        self.assertEqual(smoke.ENTRY_SHA, '8df0f1c7bc6535e5df0b626450228241d874867023adb6c9c8f7b1a882590bf4')
        self.assertEqual(smoke.ENTRY, 'resources/app.asar.unpacked/cli/bin/codebuddy')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SmokeError, 'UNAUTHORIZED_STAGE'):
                smoke.execute_stage(Path('unused'), Path('unused'), Path(tmp), 'authenticate', None)

    def test_job_cleanup_on_protocol_failure(self):
        class Owner:
            stdin = io.BytesIO()
            stdout = io.BytesIO(b'not json\n')
            stderr = io.BytesIO()
            closed = False
            @classmethod
            def spawn(cls, argv, cwd, env):
                self.assertEqual(argv[-1], '--acp')
                self.assertNotIn('GITHUB_TOKEN', env)
                return owner
            def terminate(self, timeout):
                return {"owned_job_empty": True}
            def close(self, timeout):
                self.closed = True
                return {'owned_job_empty':True}
        owner = Owner()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SmokeError):
                smoke.execute_stage(Path('unused'), Path('unused'), Path(tmp), 'initialize', Owner)
        self.assertTrue(owner.closed)

    def test_failed_cleanup_cannot_pass(self):
        class Owner:
            stdin = io.BytesIO()
            stdout = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1,"agentCapabilities":{}}}\n')
            stderr = io.BytesIO()
            @classmethod
            def spawn(cls, argv, cwd, env):
                return owner
            def terminate(self, timeout):
                return {"owned_job_empty": True}
            def close(self, timeout):
                return {'owned_job_empty':False}
        owner = Owner()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(SmokeError, 'OWNED_JOB_STOP_UNCONFIRMED'):
                smoke.execute_stage(Path('unused'), Path('unused'), Path(tmp), 'initialize', Owner)

    def test_duplicate_and_nonfinite_json_rejected(self):
        for raw in (b'{"id":1,"id":1}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}'):
            with self.assertRaisesRegex(SmokeError, 'NON_PROTOCOL_STDOUT'):
                smoke.parse_frame(raw)

    def test_payload_exact_boundaries_crc_and_sha(self):
        body, next_header = b'fixture-body', b'fixture-header'
        fields = struct.pack('<QQI', len(body), len(next_header), zlib.crc32(next_header))
        payload = b'7z\xbc\xaf\x27\x1c\x00\x04' + struct.pack('<I', zlib.crc32(fields)) + fields + body + next_header
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'fixture.exe'
            source.write_bytes(b'prefix' + payload + b'suffix')
            smoke.extract_payload(source, root / 'good.7z', offset=6, size=len(payload), expected_sha=expected)
            self.assertEqual((root / 'good.7z').read_bytes(), payload)
            for offset, size in ((-1, len(payload)), (6, 5), (6, 9999)):
                with self.assertRaises(SmokeError):
                    smoke.extract_payload(source, root / 'bad.7z', offset=offset, size=size, expected_sha=expected)
            for index in (0, 7, 8, 12, len(payload)-1):
                modified = bytearray(payload)
                modified[index] ^= 1
                source.write_bytes(b'prefix' + modified)
                with self.assertRaises(SmokeError):
                    smoke.extract_payload(source, root / 'bad.7z', offset=6, size=len(payload), expected_sha=expected)
            source.write_bytes(b'prefix' + payload)
            with self.assertRaisesRegex(SmokeError, 'PAYLOAD_SHA_MISMATCH'):
                smoke.extract_payload(source, root / 'wrong-sha.7z', offset=6, size=len(payload), expected_sha='0'*64)

    def test_failure_diagnostics_no_native_text(self):
        class Owner:
            stdin = io.BytesIO()
            stdout = io.BytesIO(b'private-path-or-secret MODULE_NOT_FOUND\n')
            stderr = io.BytesIO(b'private stderr')
            @classmethod
            def spawn(cls, argv, cwd, env):
                return owner
            def exit_code(self):
                return 17
            def terminate(self, timeout):
                return {'owned_job_empty':True}
            def close(self, timeout):
                return {'owned_job_empty':True}
        owner, diagnostics = Owner(), {}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SmokeError):
                smoke.execute_stage(Path('unused'), Path('unused'), Path(tmp), 'initialize', Owner, diagnostics)
        self.assertTrue(diagnostics['module_not_found_observed'])
        self.assertEqual(diagnostics['host_exit_code_before_cleanup'], 17)
        self.assertEqual(diagnostics['stderr_bytes'], 14)
        self.assertNotIn('private', json.dumps(diagnostics))
        self.assertEqual(len(diagnostics['stdout_sha256']), 64)

    def test_complete_capture_and_natural_exit(self):
        valid = b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1,"agentCapabilities":{}}}\n'
        extra = b'{"jsonrpc":"2.0","id":8,"method":"authenticate"}\n'
        class Chunks:
            def __init__(self, chunks):
                self.chunks = list(chunks)
            def read(self, size):
                return self.chunks.pop(0) if self.chunks else b''
        for chunks, code, expected in (([valid, extra], None, 'EXTRA'),
                                       ([valid + b'{"method":'], None, 'FRAGMENT'),
                                       ([valid], 17, 'NATIVE_EXIT_NONZERO'),
                                       ([valid], None, None)):
            class Owner:
                stdin = io.BytesIO()
                stdout = Chunks(chunks)
                stderr = io.BytesIO()
                @classmethod
                def spawn(cls, *args):
                    return cls()
                def exit_code(self):
                    return code
                def terminate(self, timeout):
                    return {'owned_job_empty':True}
                def close(self, timeout):
                    return {'owned_job_empty':True}
            with tempfile.TemporaryDirectory() as tmp:
                if expected:
                    with self.assertRaisesRegex(SmokeError, expected):
                        smoke.execute_stage(Path('unused'), Path('unused'), Path(tmp), 'initialize', Owner)
                else:
                    result = smoke.execute_stage(Path('unused'), Path('unused'), Path(tmp), 'initialize', Owner)
                    self.assertTrue(result['owned_job_empty'])

    def test_required_capabilities_and_canonical_members(self):
        with self.assertRaisesRegex(SmokeError, 'INVALID_CAPABILITIES'):
            validate_initialize({'jsonrpc':'2.0', 'id':1, 'result':{'protocolVersion':1}})
        for alias in ('a\\b', 'a/./b', 'a//b'):
            listing = 'header\n----------\nPath = a/b\nSize = 1\n\nPath = ' + alias + '\nSize = 1\n'
            with self.assertRaisesRegex(SmokeError, 'ARCHIVE_MEMBER_UNSAFE'):
                archive_inventory(listing)

    def test_help_both_eof_before_host_exit_without_new_output(self):
        # Deterministic ordering: both EOFs first, host signal changes only when
        # the consumer next polls an empty queue. No sleep or native process.
        class Owner:
            stdin = io.BytesIO()
            exited = False
            def host_exited(self):
                return self.exited
            def exit_code(self):
                return 0
        owner = Owner()
        class Events:
            def __init__(self):
                self.items = [('stdout', None), ('stderr', None)]
            def get(self, timeout):
                if self.items:
                    return self.items.pop(0)
                owner.exited = True
                raise smoke.queue.Empty
        ticks = iter(i / 100 for i in range(100))
        capture = (Events(), smoke.threading.Event(),
                   {'stdout':bytearray(b'Usage --help --acp'), 'stderr':bytearray()}, [])
        with patch.object(smoke.time, 'monotonic', side_effect=lambda:next(ticks)):
            result, _, _, _ = observe(owner, 'help', .5, capture=capture)
        self.assertTrue(result['help_flag_observed'])


    def test_help_empty_queue_does_not_relax_completion(self):
        for end_count, exited, code, output, expected in (
                (1, True, 0, b'--help', 'NATIVE_DEADLINE'),
                (2, False, None, b'--help', 'NATIVE_DEADLINE'),
                (2, True, 17, b'--help', 'HELP_EXIT_NONZERO'),
                (2, True, 0, b'not help', 'HELP_NOT_OBSERVED')):
            class Owner:
                stdin = io.BytesIO()
                def host_exited(self):
                    return exited
                def exit_code(self):
                    return code
            class Events:
                def __init__(self):
                    self.items = [('stdout', None), ('stderr', None)][:end_count]
                def get(self, timeout):
                    if self.items:
                        return self.items.pop(0)
                    raise smoke.queue.Empty
            ticks = iter(i / 100 for i in range(100))
            capture = (Events(), smoke.threading.Event(),
                       {'stdout':bytearray(output), 'stderr':bytearray()}, [])
            with patch.object(smoke.time, 'monotonic', side_effect=lambda:next(ticks)):
                with self.assertRaisesRegex(SmokeError, expected):
                    observe(Owner(), 'help', .5, capture=capture)

    def log_fixture(self, root, date='2026-11-08'):
        private, cwd = root / 'initialize', root / 'initialize/workspace'
        cwd.mkdir(parents=True)
        directory = private / 'CODEBUDDY_CONFIG_DIR/logs' / date
        directory.mkdir(parents=True)
        suffix = hashlib.md5(str(cwd).encode(), usedforsecurity=False).hexdigest()
        path = directory / ('workspace__' + suffix + '.log')
        return private, cwd, path

    def test_log_scope_and_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            private, cwd, path = self.log_fixture(Path(tmp))
            path.write_bytes(b'TOKEN private-path event_loop_drain MODULE_NOT_FOUND')
            auth = private / 'CODEBUDDY_CONFIG_DIR/auth'
            auth.mkdir()
            (auth / 'account.info').write_text('MUST_NOT_READ')
            other_date = path.parent.parent / '2026-11-07'
            other_date.mkdir()
            (other_date / path.name).write_text('MUST_NOT_READ')
            original_open = smoke.os.open
            opened = []
            def guarded_open(p, *args):
                opened.append(Path(p))
                self.assertEqual(Path(p), path)
                return original_open(p, *args)
            with patch.object(smoke.os, 'open', side_effect=guarded_open):
                report = smoke.classify_logs(private, cwd, {'2026-11-08'})
            self.assertEqual(opened, [path])
            self.assertEqual(report['observed_categories']['event_loop_drain'], 1)
            self.assertEqual(len(report['files']), 1)
            for secret in ('TOKEN', 'private-path', 'workspace__', str(private)):
                self.assertNotIn(secret, json.dumps(report))

    def test_log_links_and_oversize_rejected(self):
        for kind in ('hardlink', 'oversize'):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                private, cwd, path = self.log_fixture(root)
                external = root / 'outside'
                external.write_text('DO_NOT_READ')
                if kind == 'hardlink':
                    os.link(external, path)
                else:
                    path.write_bytes(b'x' * (512 * 1024 + 1))
                with patch.object(smoke.os, 'open', side_effect=AssertionError('Must reject before read')):
                    report = smoke.classify_logs(private, cwd, {'2026-11-08'})
                self.assertEqual(len(report['files']), 0)
                self.assertIn(report['status'], ('unsafe_or_unreadable', 'read_limit'))

    def test_log_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            private, cwd, path = self.log_fixture(Path(tmp))
            external = Path(tmp) / 'outside'
            external.write_text('DO_NOT_READ')
            try:
                path.symlink_to(external)
            except OSError:
                self.skipTest('Symlink creation unavailable; no privilege escalation')
            with patch.object(smoke.os, 'open', side_effect=AssertionError('No read')):
                report = smoke.classify_logs(private, cwd, {'2026-11-08'})
            self.assertEqual(report['status'], 'unsafe_or_unreadable')

    def test_final_handle_target_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            private, cwd, path = self.log_fixture(Path(tmp))
            path.write_text('SECRET')
            # Simulate a directory substitution whose lstat/fstat identity checks
            # agree with the outside target. Final HANDLE path must still reject.
            with patch.object(smoke, 'opened_final_path', return_value=str(Path(tmp) / 'outside')):
                report = smoke.classify_logs(private, cwd, {'2026-11-08'}, private.resolve())
            self.assertEqual(report['status'], 'unsafe_or_unreadable')
            self.assertEqual(report['files'], [])
            self.assertNotIn('SECRET', json.dumps(report))

    def test_log_directory_replaced_after_stat(self):
        with tempfile.TemporaryDirectory() as tmp:
            private, cwd, path = self.log_fixture(Path(tmp))
            path.write_text('SECRET')
            original = smoke.os.open
            moved = Path(tmp) / 'moved-directory'
            def swap_directory(p, *args):
                path.parent.rename(moved)
                try:
                    path.parent.symlink_to(moved, target_is_directory=True)
                except OSError:
                    self.skipTest('Directory symlink creation unavailable')
                return original(p, *args)
            with patch.object(smoke.os, 'open', side_effect=swap_directory):
                report = smoke.classify_logs(private, cwd, {'2026-11-08'}, private.resolve())
            self.assertEqual(report['files'], [])
            self.assertEqual(report['status'], 'unsafe_or_unreadable')

    def test_log_exact_read_partial_and_changed_metadata(self):
        for mode in ('exact', 'partial', 'changed'):
            with tempfile.TemporaryDirectory() as tmp:
                private, cwd, path = self.log_fixture(Path(tmp))
                path.write_bytes(b'x' * (512 * 1024))
                original = smoke.os.fdopen
                reads = []
                class Wrapped:
                    def __init__(self, stream):
                        self.stream = stream
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        self.stream.close()
                    def fileno(self):
                        return self.stream.fileno()
                    def read(self, size):
                        reads.append(size)
                        data = self.stream.read(size - 1 if mode == 'partial' else size)
                        if mode == 'changed':
                            os.utime(path, ns=(1, 1))
                        return data
                with patch.object(smoke.os, 'fdopen', side_effect=lambda *a:Wrapped(original(*a))):
                    report = smoke.classify_logs(private, cwd, {'2026-11-08'})
                self.assertEqual(reads, [512 * 1024])
                if mode == 'exact':
                    self.assertEqual(report['status'], 'complete')
                else:
                    self.assertEqual(report['files'], [])
                    self.assertEqual(report['status'], 'truncated' if mode == 'partial' else 'unsafe_or_unreadable')
                    self.assertNotIn('sha256', json.dumps(report))

    def test_log_date_must_be_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            private, cwd, path = self.log_fixture(Path(tmp))
            with patch.object(smoke.os, 'open', side_effect=AssertionError('No read')):
                for date in ('20261108', '../2026-11-08'):
                    self.assertEqual(smoke.classify_logs(private, cwd, {date})['status'], 'invalid_date')

    def test_log_directory_reparse_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            private, cwd, path = self.log_fixture(Path(tmp))
            original = smoke.checked_stat
            def reject_reparse(p, directory=False):
                if Path(p) == path.parent:
                    raise SmokeError('LOG_UNSAFE_ENTRY')
                return original(p, directory)
            with patch.object(smoke, 'checked_stat', side_effect=reject_reparse), patch.object(smoke.os, 'open', side_effect=AssertionError('No read')):
                report = smoke.classify_logs(private, cwd, {'2026-11-08'})
            self.assertEqual(report['status'], 'unsafe_or_unreadable')

    def test_log_identity_change_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            private, cwd, path = self.log_fixture(Path(tmp))
            path.write_text('before')
            original = smoke.os.open
            def replace_before_open(p, *args):
                # Keep old inode allocated, so identity cannot be reused.
                path.rename(path.with_suffix('.backup'))
                path.write_text('secret after')
                return original(p, *args)
            with patch.object(smoke.os, 'open', side_effect=replace_before_open):
                report = smoke.classify_logs(private, cwd, {'2026-11-08'})
            self.assertEqual(report['status'], 'unsafe_or_unreadable')
            self.assertEqual(report['files'], [])

    def test_log_total_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            private, cwd, path = self.log_fixture(Path(tmp))
            for date in ('2026-11-08', '2026-11-09'):
                directory = path.parent.parent / date
                directory.mkdir(exist_ok=True)
                for workspace in (str(cwd), 'unknown-workspace'):
                    name = Path(workspace).name + '__' + hashlib.md5(workspace.encode(), usedforsecurity=False).hexdigest()
                    for suffix in ('.log', '.old.log'):
                        (directory / (name + suffix)).write_bytes(b'x' * 512 * 1024)
            report = smoke.classify_logs(private, cwd, {'2026-11-08', '2026-11-09'})
            self.assertEqual(report['status'], 'read_limit')
            self.assertEqual(report['total_bytes'], 2 * 1024 * 1024)
            self.assertEqual(len(report['files']), 4)

    def test_eof_grace_existing_deadline_and_natural_state(self):
        class Owner:
            def exit_code(self):
                return 17
            def active_processes(self):
                return 0
        report = {}
        with patch.object(smoke.time, 'monotonic', return_value=1):
            smoke.observe_eof_exit(Owner(), 1, report)
        self.assertEqual(report['eof_observation'], 'no_natural_exit_observed')
        self.assertNotIn('natural_exit_code', report)
        with patch.object(smoke.time, 'monotonic', return_value=1):
            smoke.observe_eof_exit(Owner(), 2, report)
        self.assertEqual(report['natural_exit_code'], 17)
        self.assertEqual(report['eof_job_counts'], [0])

    def test_no_log_read_when_cleanup_unconfirmed(self):
        class Owner:
            stdin = io.BytesIO()
            stdout = io.BytesIO()
            stderr = io.BytesIO()
            @classmethod
            def spawn(cls, *args):
                return cls()
            def exit_code(self):
                return 0
            def active_processes(self):
                return 1
            def terminate(self, timeout):
                return {'owned_job_empty':False}
            def close(self, timeout):
                return {'owned_job_empty':False}
        with tempfile.TemporaryDirectory() as tmp, patch.object(smoke, 'classify_logs', side_effect=AssertionError('No logs')):
            report = {}
            with self.assertRaisesRegex(SmokeError, 'INITIALIZE_EOF'):
                smoke.execute_stage(Path('unused'), Path('unused'), Path(tmp), 'initialize', Owner, report)
            self.assertEqual(report['cleanup_error'], 'OWNED_JOB_STOP_UNCONFIRMED')
            self.assertEqual(report['logs']['status'], 'not_read_cleanup_unconfirmed')
            (Path(tmp) / 'retry').mkdir()
            with patch.object(Owner, 'close', side_effect=OSError('SECRET')):
                with self.assertRaisesRegex(SmokeError, 'INITIALIZE_EOF'):
                    smoke.execute_stage(Path('unused'), Path('unused'), Path(tmp) / 'retry', 'initialize', Owner, report)
            self.assertEqual(report['cleanup_error'], 'OWNED_JOB_CLOSE_UNCONFIRMED')
            self.assertNotIn('SECRET', json.dumps(report))

    def test_log_failure_preserves_original_eof(self):
        class Owner:
            stdin = io.BytesIO()
            stdout = io.BytesIO()
            stderr = io.BytesIO()
            @classmethod
            def spawn(cls, *args):
                return cls()
            def exit_code(self):
                return 0
            def active_processes(self):
                return 0
            def terminate(self, timeout):
                return {'owned_job_empty':True}
            def close(self, timeout):
                return {'owned_job_empty':True}
        report = {}
        with tempfile.TemporaryDirectory() as tmp, patch.object(smoke, 'classify_logs', side_effect=OSError('PRIVATE')):
            with self.assertRaisesRegex(SmokeError, 'INITIALIZE_EOF'):
                smoke.execute_stage(Path('unused'), Path('unused'), Path(tmp), 'initialize', Owner, report)
        self.assertEqual(report['logs']['status'], 'unavailable')
        self.assertNotIn('PRIVATE', json.dumps(report))



class KeepaliveCommandTests(unittest.TestCase):
    def owner_class(self, stage):
        class Input(io.BytesIO):
            def __init__(self):
                super().__init__()
                self.written = bytearray()
            def write(self, data):
                self.written.extend(data)
                return super().write(data)
        class Owner:
            calls = []
            instance = None
            @classmethod
            def spawn(cls, command, cwd, env):
                cls.calls.append((command, cwd, env))
                owner = cls()
                owner.stdin = Input()
                owner.stdout = io.BytesIO(b'--help --acp\n' if stage == 'help' else b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":1,"agentCapabilities":{}}}\n')
                owner.stderr = io.BytesIO()
                cls.instance = owner
                return owner
            def exit_code(self): return 0
            def host_exited(self): return True
            def terminate(self, timeout): return {'owned_job_empty': True}
            def close(self, timeout): return {'owned_job_empty': True}
        return Owner

    def test_help_command_unchanged_no_preloader(self):
        owner = self.owner_class('help')
        with tempfile.TemporaryDirectory() as tmp:
            result = smoke.execute_stage(Path('fixture.exe'), Path('fixture.entry'), Path(tmp), 'help', owner)
            self.assertEqual(owner.calls[0][0], ['fixture.exe','fixture.entry','--help'])
            self.assertFalse((Path(tmp)/'help/acp-keepalive.cjs').exists())
            self.assertEqual(result['entry_profile'], 'direct_help')
            self.assertNotIn('keepalive_ms', result)
            self.assertEqual(bytes(owner.instance.stdin.written), b'')

    def test_initialize_preload_only_and_single_rpc(self):
        owner = self.owner_class('initialize')
        with tempfile.TemporaryDirectory() as tmp:
            result = smoke.execute_stage(Path('fixture.exe'), Path('fixture.entry'), Path(tmp), 'initialize', owner)
            command, _, env = owner.calls[0]
            path = (Path(tmp)/'initialize/acp-keepalive.cjs').resolve()
            self.assertEqual(command, ['fixture.exe','--require',str(path),'fixture.entry','--acp'])
            self.assertEqual(path.read_bytes(), smoke.KEEPALIVE_BYTES)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), smoke.KEEPALIVE_SHA)
            self.assertEqual(len(owner.calls), 1)
            self.assertEqual([json.loads(line) for line in owner.instance.stdin.getvalue().splitlines()], [INITIALIZE])
            self.assertNotIn('NODE_OPTIONS', env)
            self.assertEqual(result['entry_profile'], 'node_preload_initialize')
            self.assertEqual(result['keepalive_ms'], 45000)
            self.assertEqual(result['keepalive_sha256'], smoke.KEEPALIVE_SHA)

    def test_missing_and_hash_mismatch_reject_before_spawn(self):
        original = smoke.create_keepalive
        for mutation in ('missing','corrupt'):
            owner = self.owner_class('initialize')
            def tamper(private):
                path = original(private)
                if mutation == 'missing': path.unlink()
                else: path.write_bytes(b'x'*len(smoke.KEEPALIVE_BYTES))
                return smoke.verify_keepalive(path, private)
            with tempfile.TemporaryDirectory() as tmp, patch.object(smoke, 'create_keepalive', side_effect=tamper):
                with self.assertRaisesRegex(SmokeError, '^KEEPALIVE_INTEGRITY_FAILED$'):
                    smoke.execute_stage(Path('fixture.exe'), Path('entry'), Path(tmp), 'initialize', owner)
                self.assertEqual(owner.calls, [])

    def test_empty_or_error_exit_never_retries_direct_entry(self):
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                owner = self.owner_class('initialize')
                original_spawn = owner.spawn
                def empty_spawn(command, cwd, env):
                    instance = original_spawn(command, cwd, env)
                    instance.stdout = io.BytesIO(b'')
                    instance.exit_code = lambda: exit_code
                    return instance
                with tempfile.TemporaryDirectory() as tmp, patch.object(owner, 'spawn', side_effect=empty_spawn):
                    with self.assertRaises(SmokeError):
                        smoke.execute_stage(Path('fixture.exe'), Path('entry'), Path(tmp), 'initialize', owner)
                    self.assertEqual(len(owner.calls), 1)
                    self.assertEqual(owner.calls[0][0][1], '--require')
                    self.assertEqual([json.loads(line) for line in bytes(owner.instance.stdin.written).splitlines()], [INITIALIZE])

    def test_preloader_exclusive_creation_and_fixed_deadline(self):
        import inspect
        with tempfile.TemporaryDirectory() as tmp:
            smoke.create_keepalive(Path(tmp))
            with self.assertRaisesRegex(SmokeError, '^KEEPALIVE_CREATE_FAILED$'):
                smoke.create_keepalive(Path(tmp))
        self.assertEqual(inspect.signature(observe).parameters['timeout'].default, 45)
        self.assertEqual(smoke.KEEPALIVE_BYTES, b"'use strict';\nsetTimeout(() => {}, 45000);\n")


import shutil
import subprocess
import time
NODE = shutil.which('node')

@unittest.skipUnless(NODE, 'Node needed for self-written fixtures')
class KeepaliveTests(unittest.TestCase):
    def run_fixture(self, source, *, preload=True, lifetime=None):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            entry = folder / 'fixture main.cjs'
            entry.write_text(source)
            wrapper = folder / 'acp-keepalive.cjs'
            wrapper.write_bytes(smoke.KEEPALIVE_BYTES)
            if lifetime is not None:
                # Short-lived fixture copy, not a production CLI/env override.
                wrapper = folder / 'fixture preload.cjs'
                wrapper.write_text(smoke.KEEPALIVE_BYTES.decode().replace('45000', str(lifetime)))
            command = [NODE] + (['--require', str(wrapper)] if preload else []) + [str(entry), '--acp']
            result = subprocess.run(command, input=b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n', stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            return result, str(entry)

    def test_preserves_vendor_style_argv_and_main(self):
        result, entry = self.run_fixture('console.log(JSON.stringify({argv:process.argv.slice(1),main:require.main===module,file:__filename}));process.exit(0);')
        data = json.loads(result.stdout)
        self.assertEqual(data, {'argv':[entry,'--acp'],'main':True,'file':entry})
        self.assertEqual(result.returncode, 0)

    def test_unref_async_fixture_requires_keepalive(self):
        source = 'setTimeout(()=>{console.log(JSON.stringify({id:1,result:{fixture:true}}));process.exit(0)},40).unref();'
        direct, _ = self.run_fixture(source, preload=False)
        kept, _ = self.run_fixture(source)
        self.assertEqual(direct.returncode, 0)
        self.assertEqual(direct.stdout, b'')
        self.assertEqual(json.loads(kept.stdout), {'id':1,'result':{'fixture':True}})

    def test_explicit_exit_not_intercepted(self):
        result, _ = self.run_fixture('process.exit(7);')
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b'')

    def test_finite_handle_expires(self):
        started = time.monotonic()
        result, _ = self.run_fixture('', lifetime=70)
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(time.monotonic()-started, .06)
        self.assertEqual(result.stdout, b'')



if __name__ == "__main__":
    unittest.main()
