"""Profile schema/filesystem fixtures, plus actual Win32 ACL/lock acceptance.

The POSIX fake does not establish Windows ACL behavior or real auth protection.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import public_harness_login_profile as storage
RealSecurity = storage.WinSecurity


class FakeSecurity:
    sid = 'S-1-5-21-100-200-300-400'

    def path_shape(self, path):
        storage.require(path.is_absolute())

    def create(self, path):
        path.mkdir(mode=0o700)

    def check(self, path, directory, protected=False):
        storage.require(path.exists())


def fake_lock(root, security):
    return (root / storage.LOCK).open('r+b')


class ProfileFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.parent = Path(self.temp.name).resolve()
        self.patches = [patch.object(storage, 'WinSecurity', FakeSecurity),
                        patch.object(storage, '_lock', fake_lock)]
        for item in self.patches:
            item.start()
        self.addCleanup(self.temp.cleanup)
        for item in self.patches:
            self.addCleanup(item.stop)

    def test_new_and_explicit_reopen_never_treat_manifest_as_login(self):
        with storage.create_profile(self.parent) as profile:
            root = profile.root
            self.assertEqual(profile.state, 'fresh')
            self.assertIsNone(profile.expected_account_digest)
            self.assertTrue(profile.verify())
            data = json.loads((root / storage.MANIFEST).read_text())
            self.assertFalse(data['native_encryption_verified'])
            self.assertFalse(data['browser_isolation_verified'])
        with storage.open_profile(root) as reopened:
            self.assertEqual(reopened.state, 'fresh')
            self.assertIsNone(reopened.expected_account_digest)
        self.assertTrue(root.is_dir())

    def test_record_requires_started_then_single_terminal_digest(self):
        with storage.create_profile(self.parent) as profile:
            with self.assertRaises(storage.ProfileError):
                profile.record('authenticated_confirmed', 'a' * 64)
            profile.record('login_started')
            profile.record('authenticated_confirmed', 'a' * 64)
            self.assertEqual(profile.expected_account_digest, 'a' * 64)
            with self.assertRaises(storage.ProfileError):
                profile.record('login_started')
            root = profile.root
        with storage.open_profile(root) as reopened:
            self.assertEqual(reopened.expected_account_digest, 'a' * 64)

    def test_unknown_is_retained_without_digest_or_retry(self):
        with storage.create_profile(self.parent) as profile:
            profile.record('login_started')
            profile.record('unknown_retained')
            root = profile.root
            with self.assertRaises(storage.ProfileError):
                profile.record('authenticated_confirmed', 'b' * 64)
        self.assertTrue(root.exists())

    def test_recovery_preserves_origin_pins_and_cannot_be_resumed_after_owner_loss(self):
        with storage.create_profile(self.parent) as profile:
            root = profile.root
            profile.record('login_started')
            profile.record('unknown_retained')
            original = json.loads((root / storage.MANIFEST).read_text())
            profile.begin_recovery()
            profile.finish_recovery('a' * 64)
            expected = dict(original, state='authenticated_confirmed', confirmed_account_digest='a' * 64)
            self.assertEqual(json.loads((root / storage.MANIFEST).read_text()), expected)
        with storage.create_profile(self.parent) as profile:
            root = profile.root
            profile.record('login_started')
            profile.record('unknown_retained')
            profile.begin_recovery()
        with storage.open_profile(root) as reopened:
            self.assertEqual(reopened.state, 'recovery_started')
            with self.assertRaises(storage.ProfileError):
                reopened.begin_recovery()
            with self.assertRaises(storage.ProfileError):
                reopened.finish_recovery('a' * 64)

    def test_bad_digest_and_manifest_claims_are_rejected(self):
        with storage.create_profile(self.parent) as profile:
            profile.record('login_started')
            for digest in (None, '', 'A' * 64, 'a' * 65, True):
                with self.assertRaises(storage.ProfileError):
                    profile.record('authenticated_confirmed', digest)
            path = profile.root / storage.MANIFEST
            data = json.loads(path.read_text()); data['native_encryption_verified'] = True
            path.write_text(json.dumps(data))
            with self.assertRaises(storage.ProfileError):
                profile.verify()

    def test_credentials_are_not_read_by_metadata_verification(self):
        with storage.create_profile(self.parent) as profile:
            opaque = profile.root / 'config' / 'auth.info'
            opaque.write_bytes(b'opaque-synthetic-fixture-not-an-account')
            original = Path.open
            opened = []
            def spy(path, *args, **kwargs):
                opened.append(path)
                if path == opaque:
                    raise AssertionError('native credential contents must not be opened')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'open', spy):
                self.assertTrue(profile.verify())
            self.assertNotIn(opaque, opened)

    def test_workbuddy_app_data_accepts_only_scoped_appcontainer_and_leveldb_acls(self):
        sid = FakeSecurity.sid
        capability = storage.WORKBUDDY_APP_CAPABILITY_SID
        root_entries = [
            (0, 0, 0x1301bf, capability), (0, 0x0b, 0xe0010000, capability),
            (0, 0x13, 0x1f01ff, 'S-1-5-18'), (0, 0x13, 0x1f01ff, sid),
        ]
        child_directory = [
            (0, 0x10, 0x1301bf, capability), (0, 0x1b, 0xe0010000, capability),
            (0, 0x13, 0x1f01ff, 'S-1-5-18'), (0, 0x13, 0x1f01ff, sid),
        ]
        child_file = [
            (0, 0x10, 0x1301bf, capability),
            (0, 0x10, 0x1f01ff, 'S-1-5-18'), (0, 0x10, 0x1f01ff, sid),
        ]
        self.assertTrue(storage._check_workbuddy_appcontainer_aces(sid, sid, True, True, root_entries))
        self.assertTrue(storage._check_workbuddy_appcontainer_aces(sid, sid, True, False, child_directory))
        self.assertTrue(storage._check_workbuddy_appcontainer_aces(sid, sid, False, False, child_file))
        bad_type = list(root_entries); bad_type[0] = (1, *bad_type[0][1:])
        for changed in (root_entries + [(0, 0, 0x1f01ff, 'S-1-1-0')],
                        [*root_entries[:1], (0, 0x0b, 0x1f01ff, capability), *root_entries[2:]],
                        bad_type):
            with self.assertRaises(storage.ProfileError):
                storage._check_workbuddy_appcontainer_aces(sid, sid, True, True, changed)
        with self.assertRaises(storage.ProfileError):
            storage._check_workbuddy_appcontainer_aces('S-1-5-21-999', sid, True, True, root_entries)
        with self.assertRaises(storage.ProfileError):
            storage._check_workbuddy_appcontainer_aces(sid, sid, True, True,
                    [(kind, flags, mask, 'S-1-15-3-1024-123-456' if trustee == capability else trustee)
                     for kind, flags, mask, trustee in root_entries])
        leveldb = [(1, 0, 0x20, 'S-1-1-0'), (0, 0x10, 0x1f01ff, 'S-1-5-18'),
                   (0, 0x10, 0x1f01ff, sid)]
        self.assertTrue(storage._check_workbuddy_leveldb_aces(sid, sid, leveldb))
        with self.assertRaises(storage.ProfileError):
            storage._check_workbuddy_leveldb_aces(sid, sid,
                    [*leveldb[:1], (1, 0, 0x1f01ff, 'S-1-1-0'), *leveldb[2:]])
        with self.assertRaises(storage.ProfileError):
            storage._check_workbuddy_leveldb_aces(sid, sid, leveldb[1:] + leveldb[:1])
        junction = [(1, 0, 0x1, 'S-1-1-0'), (0, 3, 0x1f01ff, 'S-1-5-18'),
                    (0, 3, 0x1f01ff, sid)]
        self.assertTrue(storage._check_inetcache_compat_aces(sid, sid, junction))
        with self.assertRaises(storage.ProfileError):
            storage._check_inetcache_compat_aces(sid, sid, junction[1:] + junction[:1])

    def test_workbuddy_acl_exception_stays_inside_exact_app_data_subtrees(self):
        root = self.parent / ('workbuddy-managed-' + '0' * 32)
        data_root = storage._workbuddy_app_data_root(root)
        self.assertTrue(storage._is_workbuddy_app_data_path(data_root, root))
        app_cache = data_root / 'Default' / 'Cache'
        self.assertTrue(storage._is_workbuddy_app_data_path(app_cache / 'entry', root))
        self.assertFalse(storage._is_workbuddy_app_data_path(data_root.with_name('User Data Copy'), root))
        self.assertFalse(storage._is_workbuddy_app_data_path(root / 'config' / 'entry', root))
        self.assertTrue(storage._is_workbuddy_appcontainer_root(app_cache, data_root))
        self.assertFalse(storage._is_workbuddy_appcontainer_root(app_cache / 'child', data_root))
        self.assertTrue(storage._is_workbuddy_appcontainer_path(app_cache / 'child', data_root))
        self.assertFalse(storage._is_workbuddy_appcontainer_path(data_root / 'Default' / 'Cookies', data_root))
        self.assertFalse(storage._is_workbuddy_appcontainer_path(
                data_root / 'Default' / 'Local Storage' / 'leveldb' / 'LOG', data_root))
        self.assertTrue(storage._is_workbuddy_leveldb_file(
                data_root / 'Default' / 'Local Storage' / 'leveldb' / 'LOG', data_root, False))
        self.assertFalse(storage._is_workbuddy_leveldb_file(
                data_root / 'Default' / 'Local Storage' / 'leveldb', data_root, True))
        self.assertFalse(storage._is_workbuddy_leveldb_file(
                data_root / 'Default' / 'Local Storage Copy' / 'leveldb' / 'LOG', data_root, False))
        session_storage = data_root / 'Default' / 'Session Storage'
        session_file = session_storage / 'LOG'
        self.assertTrue(storage._is_workbuddy_session_storage_file(session_file, data_root, False))
        self.assertFalse(storage._is_workbuddy_session_storage_file(session_storage, data_root, True))
        self.assertFalse(storage._is_workbuddy_session_storage_file(
                session_storage / 'nested' / 'LOG', data_root, False))
        self.assertFalse(storage._is_workbuddy_session_storage_file(
                session_storage.with_name('Session Storage Copy') / 'LOG', data_root, False))
        self.assertTrue(storage._is_workbuddy_restricted_acl_path(app_cache / 'entry', data_root, False))
        self.assertTrue(storage._is_workbuddy_restricted_acl_path(
                data_root / 'Default' / 'Local Storage' / 'leveldb' / 'LOG', data_root, False))
        self.assertTrue(storage._is_workbuddy_restricted_acl_path(session_file, data_root, False))
        self.assertFalse(storage._is_workbuddy_restricted_acl_path(session_storage, data_root, True))
        self.assertFalse(storage._is_workbuddy_restricted_acl_path(
                session_storage / 'nested' / 'LOG', data_root, False))
        self.assertFalse(storage._is_workbuddy_restricted_acl_path(
                session_storage.with_name('Session Storage Copy') / 'LOG', data_root, False))
        cache_dir = root / 'localappdata' / 'Microsoft' / 'Windows' / 'Caches'
        cache_file = cache_dir / 'cversions.3.db'
        self.assertTrue(storage._is_workbuddy_windows_cache_file(cache_file, root, False))
        self.assertTrue(storage._is_workbuddy_windows_cache_file(cache_dir / 'another.db', root, False))
        self.assertFalse(storage._is_workbuddy_windows_cache_file(cache_dir, root, True))
        self.assertFalse(storage._is_workbuddy_windows_cache_file(cache_dir / 'nested' / 'entry.db', root, False))
        self.assertFalse(storage._is_workbuddy_windows_cache_file(
                cache_dir.with_name('Caches Copy') / 'entry.db', root, False))
        sid = FakeSecurity.sid
        capability = storage.WORKBUDDY_APP_CAPABILITY_SID
        appcontainer = [(0, 0, 0x1301bf, capability), (0, 0x0b, 0xe0010000, capability),
                        (0, 0x13, 0x1f01ff, 'S-1-5-18'), (0, 0x13, 0x1f01ff, sid)]
        leveldb = [(1, 0, 0x20, 'S-1-1-0'), (0, 0x10, 0x1f01ff, 'S-1-5-18'),
                   (0, 0x10, 0x1f01ff, sid)]
        leveldb_file = data_root / 'Default' / 'Local Storage' / 'leveldb' / 'LOG'
        self.assertTrue(storage._check_workbuddy_app_data_aces(app_cache, data_root,
                sid, sid, True, appcontainer))
        self.assertTrue(storage._check_workbuddy_app_data_aces(leveldb_file, data_root,
                sid, sid, False, leveldb))
        self.assertTrue(storage._check_workbuddy_app_data_aces(session_file, data_root,
                sid, sid, False, leveldb))
        with self.assertRaises(storage.ProfileError):
            storage._check_workbuddy_app_data_aces(leveldb_file, data_root,
                    sid, sid, False, appcontainer)
        with self.assertRaises(storage.ProfileError):
            storage._check_workbuddy_app_data_aces(app_cache, data_root,
                    sid, sid, True, leveldb)
        with self.assertRaises(storage.ProfileError):
            storage._check_workbuddy_app_data_aces(session_storage / 'nested' / 'LOG', data_root,
                    sid, sid, False, leveldb)
        with self.assertRaises(storage.ProfileError):
            storage._check_workbuddy_app_data_aces(
                    session_storage.with_name('Session Storage Copy') / 'LOG', data_root,
                    sid, sid, False, leveldb)
        package_sid, capability_sid = 'S-1-15-2-123', 'S-1-15-3-456'
        windows_cache = [
            (0, 0, 0x1f01ff, sid), (0, 0, 0x120089, package_sid),
            (0, 0, 0x120089, capability_sid), (0, 0x10, 0x1f01ff, 'S-1-5-18'),
            (0, 0x10, 0x1f01ff, sid),
        ]
        self.assertTrue(storage._check_workbuddy_windows_cache_aces(sid, sid, windows_cache))
        for changed in (
                [windows_cache[0], (0, 0, 0x1f01ff, package_sid), *windows_cache[2:]],
                [windows_cache[0], windows_cache[2], windows_cache[1], *windows_cache[3:]],
                [*windows_cache, (0, 0, 0x120089, package_sid)],
                [windows_cache[0], windows_cache[1], (0, 0, 0x120089, 'S-1-15-2-789'), *windows_cache[3:]]):
            with self.assertRaises(storage.ProfileError):
                storage._check_workbuddy_windows_cache_aces(sid, sid, changed)
        with self.assertRaises(storage.ProfileError):
            storage._check_workbuddy_windows_cache_aces('S-1-5-21-999', sid, windows_cache)

    def test_profile_verify_uses_windows_cache_fallback_only_for_direct_files(self):
        class CacheSecurity(FakeSecurity):
            def __init__(self):
                self.strict_failures = []
                self.accepted_cache_files = []

            def check(self, path, directory, protected=False):
                path = Path(path)
                if (not directory and path.parent.name == 'Caches'
                        and path.parent.parent.name == 'Windows'
                        and path.parent.parent.parent.name == 'Microsoft'
                        and path.parent.parent.parent.parent.name == 'localappdata'):
                    self.strict_failures.append(path)
                    raise storage.ProfileError('synthetic standard ACL mismatch')
                return super().check(path, directory, protected)

            def check_workbuddy_windows_cache_file(self, path, profile_root):
                storage.require(storage._is_workbuddy_windows_cache_file(path, profile_root, False))
                self.accepted_cache_files.append(Path(path))

        with patch.object(storage, 'WinSecurity', CacheSecurity):
            with storage.create_profile(self.parent) as profile:
                cache_dir = profile.root / 'localappdata' / 'Microsoft' / 'Windows' / 'Caches'
                nested_dir = cache_dir / 'nested'
                nested_dir.mkdir(parents=True)
                direct_files = [cache_dir / 'cversions.3.db', cache_dir / 'another.db']
                for path in direct_files + [nested_dir / 'nested.db']:
                    path.write_bytes(b'synthetic-cache-fixture')
                self.assertTrue(profile.verify())
                self.assertCountEqual(profile._security.strict_failures, direct_files)
                self.assertCountEqual(profile._security.accepted_cache_files, direct_files)

    def test_extra_top_level_state_blocks_reuse(self):
        with storage.create_profile(self.parent) as profile:
            (profile.root / 'unexpected').write_text('synthetic')
            with self.assertRaises(storage.ProfileError):
                profile.verify()

    def test_hardlinked_native_file_is_rejected_without_contents(self):
        with storage.create_profile(self.parent) as profile:
            a = profile.root / 'config' / 'a'; a.write_bytes(b'synthetic')
            try:
                os.link(a, profile.root / 'config' / 'b')
            except OSError:
                self.skipTest('hardlinks unavailable')
            with self.assertRaises(storage.ProfileError):
                profile.verify()

    @unittest.skipIf(os.name == 'nt', 'Windows symlinks may need privilege; actual ACL tests separate')
    def test_link_and_untrusted_ancestor_fail_closed(self):
        with storage.create_profile(self.parent) as profile:
            (profile.root / 'config' / 'link').symlink_to(self.parent, target_is_directory=True)
            with self.assertRaises(storage.ProfileError):
                profile.verify()
        link = self.parent / 'parent-link'; link.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(storage.ProfileError):
            storage.create_profile(link)

    def test_old_or_simulated_profile_not_accepted(self):
        existing = self.parent / 'old-account'; existing.mkdir()
        (existing / 'auth.info').write_bytes(b'synthetic-no-real-account')
        with self.assertRaises(storage.ProfileError):
            storage.open_profile(existing)
        self.assertEqual((existing / 'auth.info').read_bytes(), b'synthetic-no-real-account')

    def test_duplicate_manifest_keys_and_tampering_rejected(self):
        with storage.create_profile(self.parent) as profile:
            path = profile.root / storage.MANIFEST
            path.write_text('{"schema_version":1,"schema_version":1}')
            with self.assertRaises(storage.ProfileError):
                profile.verify()

    def test_closed_capability_cannot_write(self):
        profile = storage.create_profile(self.parent); profile.close()
        with self.assertRaises(storage.ProfileError):
            profile.record('login_started')

    def test_failed_capability_construction_closes_lock_and_retains_directory(self):
        handles = []
        def capture(root, security):
            stream = fake_lock(root, security); handles.append(stream); return stream
        with patch.object(storage, '_lock', capture), \
             patch.object(storage, 'ManagedProfile', side_effect=storage.ProfileError()):
            with self.assertRaises(storage.ProfileError):
                storage.create_profile(self.parent)
        self.assertEqual(len(handles), 1)
        self.assertTrue(handles[0].closed)
        self.assertEqual(len(list(self.parent.iterdir())), 1)

    def confirmed_profile(self):
        profile = storage.create_profile(self.parent)
        profile.record('login_started')
        profile.record('authenticated_confirmed', 'd' * 64)
        self.addCleanup(profile.close)
        return profile

    def test_execution_marker_persists_across_crash_and_reopen_cannot_settle_it(self):
        profile = self.confirmed_profile()
        profile.begin_execution()
        self.assertEqual(profile.state, 'execution_started')
        self.assertEqual(profile.expected_account_digest, 'd' * 64)
        profile.close()
        with storage.open_profile(profile.root) as reopened:
            self.assertEqual(reopened.state, 'execution_started')
            with self.assertRaises(storage.ProfileError):
                reopened.begin_execution()
            with self.assertRaises(storage.ProfileError):
                reopened.finish_execution(cleanup_confirmed=True)
            with self.assertRaises(storage.ProfileError):
                reopened.environment(self.parent / 'node.exe')
            reopened.quarantine()
            self.assertEqual(reopened.state, 'execution_unknown')
            self.assertIsNone(reopened.expected_account_digest)

    def test_execution_cleanup_requires_boolean_and_preserves_digest_only_on_true(self):
        profile = self.confirmed_profile()
        profile.begin_execution()
        with self.assertRaises(storage.ProfileError):
            profile.finish_execution(cleanup_confirmed=1)
        profile.finish_execution(cleanup_confirmed=True)
        self.assertEqual(profile.state, 'authenticated_confirmed')
        self.assertEqual(profile.expected_account_digest, 'd' * 64)
        profile.begin_execution()
        profile.finish_execution(cleanup_confirmed=False)
        self.assertEqual(profile.state, 'execution_unknown')
        self.assertIsNone(profile.expected_account_digest)
        with self.assertRaises(storage.ProfileError):
            profile.begin_execution()

    def test_terminal_profiles_cannot_build_environment(self):
        profile = self.confirmed_profile()
        with self.assertRaises(storage.ProfileError):
            profile.environment(self.parent / 'node.exe')
        profile.quarantine()
        with self.assertRaises(storage.ProfileError):
            profile.environment(self.parent / 'node.exe')
        self.assertTrue((profile.root / 'control').is_dir())

    def test_metadata_errors_do_not_echo_path_or_contents(self):
        with storage.create_profile(self.parent) as profile:
            with patch.object(Path, 'lstat', side_effect=OSError('PRIVATE_PATH_OR_TOKEN')):
                with self.assertRaises(storage.ProfileError) as caught:
                    profile.verify()
            self.assertNotIn('PRIVATE', str(caught.exception))

    def test_environment_rejects_unpinned_node(self):
        with storage.create_profile(self.parent) as profile:
            node = self.parent / 'node.exe'; node.write_bytes(b'synthetic-not-node')
            with self.assertRaises(storage.ProfileError):
                profile.environment(node)

    def test_environment_preserves_parent_proxy_without_importing_auth_or_node_options(self):
        with storage.create_profile(self.parent) as profile:
            node = self.parent / 'node.exe'; node.write_bytes(b'synthetic-node')
            for uppercase in (False, True):
                proxies = {'http_proxy': 'http://127.0.0.1:18080',
                           'https_proxy': 'http://127.0.0.1:18081',
                           'all_proxy': 'http://127.0.0.1:18082',
                           'no_proxy': 'localhost,127.0.0.1'}
                if uppercase:
                    proxies = {key.upper(): value for key, value in proxies.items()}
                parent = dict(proxies, SystemRoot=str(self.parent),
                              HOME='synthetic-original-home', CODEBUDDY_CONFIG_DIR='synthetic-original-config',
                              NODE_OPTIONS='synthetic-node-option', NODE_TLS_REJECT_UNAUTHORIZED='0',
                              CODEBUDDY_API_KEY='synthetic-not-a-key')
                with self.subTest(uppercase=uppercase), patch.object(storage.os, 'environ', parent), \
                        patch.object(storage, 'sha', return_value=storage.NODE_EXE_SHA):
                    result = profile.environment(node)
                self.assertEqual({key: value for key, value in result.items()
                                  if key.lower().endswith('_proxy')}, proxies)
                self.assertEqual(len({key.lower() for key in result}), len(result))
                self.assertEqual(result['HOME'], str(profile.root / 'home'))
                self.assertEqual(result['CODEBUDDY_CONFIG_DIR'], str(profile.root / 'config'))
                for key in ('NODE_OPTIONS', 'NODE_TLS_REJECT_UNAUTHORIZED', 'CODEBUDDY_API_KEY'):
                    self.assertNotIn(key, result)

    def test_sid_shape_allows_current_azuread_identity_but_not_sddl_injection(self):
        self.assertTrue(storage._user_sid('S-1-12-1-111-222-333-444'))
        self.assertTrue(storage._user_sid(FakeSecurity.sid))
        for sid in ('S-1-5-18', 'S-1-5-19', 'S-1-5-20', 'S-1-5-21)D:', None, True):
            self.assertFalse(storage._user_sid(sid))

    def test_owner_fallback_is_exact_os_token_owner_and_never_protected_root(self):
        security = object.__new__(RealSecurity)
        security.sid = FakeSecurity.sid
        security.default_owner_sid = 'S-1-5-32-544'
        self.assertTrue(security._owner_allowed(security.sid, True))
        self.assertTrue(security._owner_allowed(security.default_owner_sid, False))
        self.assertFalse(security._owner_allowed(security.default_owner_sid, True))
        self.assertFalse(security._owner_allowed('S-1-5-21-999', False))
        security.default_owner_sid = security.sid
        self.assertFalse(security._owner_allowed('S-1-5-32-544', False))

    def test_quarantine_clears_confirmed_digest_despite_unrelated_native_acl_failure(self):
        profile = self.confirmed_profile()
        native = profile.root / 'config' / 'opaque.info'
        native.write_bytes(b'synthetic-no-credential')
        original = profile._security.check
        def check(path, *args, **kwargs):
            if path == native:
                raise storage.ProfileError()
            return original(path, *args, **kwargs)
        with patch.object(profile._security, 'check', check):
            with self.assertRaises(storage.ProfileError):
                profile.verify()
            profile.quarantine()
            self.assertEqual(profile.state, 'unknown_retained')
            self.assertIsNone(profile.expected_account_digest)
            with self.assertRaises(storage.ProfileError):
                profile.verify()
            self.assertFalse(profile._lock.closed)
        data = json.loads((profile.root / storage.MANIFEST).read_text())
        self.assertIsNone(data['confirmed_account_digest'])
        self.assertEqual(data['state'], 'unknown_retained')
        self.assertTrue(native.exists())

    def test_quarantine_requires_unchanged_root_and_current_lock(self):
        profile = self.confirmed_profile()
        with patch.object(profile._security, 'check', side_effect=storage.ProfileError()):
            with self.assertRaises(storage.ProfileError):
                profile.quarantine()
        self.assertFalse(profile._lock.closed)
        self.assertEqual(profile.state, 'authenticated_confirmed')


@unittest.skipUnless(os.name == 'nt', 'Actual Windows ACL and byte lock')
class ActualWindowsProfileTests(unittest.TestCase):
    def test_session_history_long_path_keeps_original_acl_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            with storage.create_profile(Path(temporary).resolve()) as profile:
                project = profile.root / 'config' / 'projects' / ('encoded-workspace-' + 'x' * 180)
                project.parent.mkdir()
                history = project / 'synthetic-session.jsonl'
                native_project = Path(storage._win32_name(project))
                native_history = Path(storage._win32_name(history))
                native_project.mkdir()
                try:
                    native_history.write_text('{}\n', encoding='utf-8')
                    self.assertGreater(len(str(history)), 260)
                    self.assertTrue(profile.verify())
                finally:
                    native_history.unlink(missing_ok=True)
                    native_project.rmdir()

    def test_real_windows_private_acl_inheritance_lock_and_reopen(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            with storage.create_profile(parent) as profile:
                root = profile.root
                opaque = root / 'config' / 'synthetic.info'
                opaque.write_bytes(b'not-credentials')
                self.assertTrue(profile.verify())
                with self.assertRaises(storage.ProfileError) as caught:
                    storage.open_profile(root)
                self.assertEqual(caught.exception.code, 'PROFILE_BUSY')
                profile.record('login_started')
                profile.record('authenticated_confirmed', 'c' * 64)
            with storage.open_profile(root) as reopened:
                self.assertTrue(reopened.verify())
                self.assertEqual(reopened.expected_account_digest, 'c' * 64)


@unittest.skipIf(os.name == 'nt', 'Non-Windows refusal')
class UnsupportedTests(unittest.TestCase):
    def test_production_refuses_nonwindows_before_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(storage.ProfileError) as caught:
                storage.create_profile(Path(temporary))
            self.assertEqual(caught.exception.code, 'WINDOWS_REQUIRED')
            self.assertEqual(list(Path(temporary).iterdir()), [])
