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


if __name__ == "__main__":
    unittest.main()
