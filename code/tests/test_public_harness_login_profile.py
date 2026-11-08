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

    def test_sid_shape_allows_current_azuread_identity_but_not_sddl_injection(self):
        self.assertTrue(storage._user_sid('S-1-12-1-111-222-333-444'))
        self.assertTrue(storage._user_sid(FakeSecurity.sid))
        for sid in ('S-1-5-18', 'S-1-5-19', 'S-1-5-20', 'S-1-5-21)D:', None, True):
            self.assertFalse(storage._user_sid(sid))


@unittest.skipUnless(os.name == 'nt', 'Actual Windows ACL and byte lock')
class ActualWindowsProfileTests(unittest.TestCase):
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
