import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from beta15_login_profile import (create_simulation_profile, ProfileError, DIRECTORIES,
    MANIFEST, RESULT, _physical_directory, _require_local_path_shape)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.parent = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_profile_and_fixed_result(self):
        p = create_simulation_profile(self.parent)
        self.assertTrue(p.verify_fresh())
        self.assertEqual(set(x.name for x in p.root.iterdir()), set(DIRECTORIES) | {MANIFEST})
        value = p.record_outcome('SIMULATED_AUTHENTICATED')
        self.assertFalse(value['native_login_enabled'])
        self.assertFalse(value['real_account_verified'])
        self.assertFalse(value['credential_encryption_verified'])
        self.assertFalse(value['browser_isolation_verified'])
        self.assertTrue(value['retained'])
        self.assertEqual(json.loads((p.root / 'output' / RESULT).read_text()), value)
        with self.assertRaises(ProfileError): p.record_outcome('SIMULATED_AUTHENTICATED')
        with self.assertRaises(ProfileError): p.verify_fresh()
        self.assertFalse(hasattr(p, 'delete'))
        self.assertFalse(hasattr(p, 'native_environment'))

    def test_posix_new_permissions_not_existing_permissions(self):
        p = create_simulation_profile(self.parent)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(p.root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((p.root / MANIFEST).stat().st_mode), 0o600)

    def test_unexpected_content_refuses_without_read_or_delete(self):
        p = create_simulation_profile(self.parent)
        unexpected = p.root / 'home' / 'unexpected-account-store'
        unexpected.write_text('DO_NOT_READ')
        with self.assertRaises(ProfileError): p.record_outcome('SIMULATED_FAILED')
        self.assertEqual(unexpected.read_text(), 'DO_NOT_READ')
        self.assertFalse((p.root / 'output' / RESULT).exists())

    def test_manifest_tamper_refused(self):
        p = create_simulation_profile(self.parent)
        (p.root / MANIFEST).write_text('{}')
        with self.assertRaises(ProfileError): p.verify_fresh()

    def test_root_replacement_refused(self):
        p = create_simulation_profile(self.parent)
        p.root.rename(self.parent / 'original')
        p.root.mkdir()
        with self.assertRaises(ProfileError): p.verify_fresh()

    def test_parent_and_leaf_symlink_refused(self):
        alias = self.parent / 'alias'
        target = self.parent / 'target'; target.mkdir()
        try: alias.symlink_to(target, target_is_directory=True)
        except OSError: self.skipTest('symlinks unavailable')
        with self.assertRaises(ProfileError): create_simulation_profile(alias)
        p = create_simulation_profile(self.parent)
        manifest = p.root / MANIFEST
        manifest.rename(p.root / 'original-manifest')
        manifest.symlink_to(p.root / 'original-manifest')
        with self.assertRaises(ProfileError): p.verify_fresh()

    def test_no_reuse_and_no_existing_file_overwrite(self):
        fixed = type('UUID', (), {'hex': '0' * 32})()
        with patch('beta15_login_profile.uuid.uuid4', return_value=fixed):
            first = create_simulation_profile(self.parent)
            original = (first.root / MANIFEST).read_bytes()
            with self.assertRaises(ProfileError): create_simulation_profile(self.parent)
            self.assertEqual((first.root / MANIFEST).read_bytes(), original)

    def test_unknown_outcome_and_noncanonical_parent_refused(self):
        p = create_simulation_profile(self.parent)
        for value in ['TOKEN=DO_NOT_WRITE', None, 1, {}, ['SIMULATED_FAILED']]:
            with self.assertRaises(ProfileError): p.record_outcome(value)
        with self.assertRaises(ProfileError): create_simulation_profile(Path('.'))

    def test_hardlink_manifest_refused(self):
        p = create_simulation_profile(self.parent)
        try: os.link(p.root / MANIFEST, self.parent / 'linked')
        except OSError: self.skipTest('hardlinks unavailable')
        with self.assertRaises(ProfileError): p.verify_fresh()

    def test_partial_create_is_retained_without_cleanup(self):
        with patch('beta15_login_profile._new_file', side_effect=OSError('PRIVATE')):
            with self.assertRaises(ProfileError) as found: create_simulation_profile(self.parent)
        self.assertEqual(str(found.exception), 'profile_create_failed')
        children = list(self.parent.iterdir())
        self.assertEqual(len(children), 1)
        self.assertTrue(children[0].is_dir())

    def test_remote_device_and_relative_path_shapes_rejected_before_drive_query(self):
        paths = [PureWindowsPath(r'\\server\share\parent'),
                 PureWindowsPath(r'\\?\C:\parent'),
                 PureWindowsPath(r'\\.\C:\parent'),
                 PureWindowsPath(r'C:parent'), PureWindowsPath(r'\parent'),
                 PurePosixPath('//server/parent'), PurePosixPath('parent')]
        with patch('beta15_login_profile._windows_drive_type') as drive:
            for path in paths:
                with self.subTest(path=path), self.assertRaises(ProfileError):
                    _require_local_path_shape(path)
            drive.assert_not_called()

    def test_windows_fixed_drive_only_and_standard_posix_anchor(self):
        path = PureWindowsPath(r'C:\trusted\parent')
        for drive_type in (0, 1, 2, 4, 5, 6):
            with patch('beta15_login_profile._windows_drive_type', return_value=drive_type):
                with self.assertRaises(ProfileError): _require_local_path_shape(path)
        with patch('beta15_login_profile._windows_drive_type', return_value=3) as drive:
            _require_local_path_shape(path)
            drive.assert_called_once_with('C:\\')
        with patch('beta15_login_profile._windows_drive_type') as drive:
            _require_local_path_shape(PurePosixPath('/trusted/parent'))
            drive.assert_not_called()

    def test_observed_reparse_ancestor_rejected_before_child_metadata(self):
        leaf = self.parent / 'must-not-be-inspected'
        original = Path.lstat
        visited = []
        def metadata(path, *args, **kwargs):
            visited.append(path)
            if path == self.parent:
                return type('Info', (), {'st_mode': stat.S_IFDIR,
                                        'st_file_attributes': 0x400})()
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', metadata):
            with self.assertRaises(ProfileError): _physical_directory(leaf)
        self.assertIn(self.parent, visited)
        self.assertNotIn(leaf, visited)


if __name__ == '__main__': unittest.main()
