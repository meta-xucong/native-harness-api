"""New, non-reusable simulation profiles. Never reads vendor/account files.

Directory separation is not a Windows ACL, browser, or encryption guarantee.
There is deliberately no reopen, resume, native-environment, logout, or delete API.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
import uuid

DIRECTORIES = ('home', 'config', 'appdata', 'localappdata', 'temp', 'workspace', 'output')
OUTCOMES = frozenset({
    'SIMULATED_AUTHENTICATED', 'SIMULATED_CANCELLED_UNAUTHENTICATED',
    'SIMULATED_CANCELLED_ACCOUNT_PRESENT', 'SIMULATED_UNKNOWN_RETAINED',
    'SIMULATED_IDENTITY_REJECTED', 'SIMULATED_REFUSED', 'SIMULATED_FAILED',
    'SIMULATED_CANCELLED_BEFORE_START', 'SIMULATED_UNAUTHENTICATED',
    'SIMULATED_ACCOUNT_PRESENT_UNCONFIRMED',
})
MANIFEST = 'simulation-profile.json'
RESULT = 'simulation-result.json'


class ProfileError(Exception):
    """Only a fixed, non-sensitive error code leaves the storage layer."""

    def __init__(self, code='profile_rejected', profile_id=None):
        if code not in ('profile_rejected', 'profile_io_failed', 'profile_create_failed'):
            code = 'profile_rejected'
        super().__init__(code)
        self.code = code
        prefix = 'native-login-sim-'
        self.profile_id = profile_id if (type(profile_id) is str and profile_id.startswith(prefix)
            and len(profile_id) == len(prefix) + 32
            and all(character in '0123456789abcdef' for character in profile_id[len(prefix):])) else None


def _require(condition):
    if not condition:
        raise ProfileError()


def _windows_drive_type(anchor):
    # OS metadata query only; do not resolve/stat a supplied UNC/device path.
    import ctypes
    kernel32 = ctypes.WinDLL('kernel32.dll', use_last_error=True)
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = [ctypes.c_wchar_p]
    get_drive_type.restype = ctypes.c_uint
    return get_drive_type(anchor)


def _require_local_path_shape(path):
    """Reject obvious remote/device paths before filesystem metadata access.

    This is not a network sandbox or a proof that a POSIX mount is local.
    The caller must still choose a trusted local filesystem.
    """
    _require(path.is_absolute())
    if isinstance(path, PureWindowsPath):
        _require(len(path.drive) == 2 and path.drive[0] in
                 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
                 and path.drive[1] == ':' and path.root == '\\')
        _require(_windows_drive_type(path.anchor) == 3)  # DRIVE_FIXED only
    else:
        _require(isinstance(path, PurePosixPath) and path.anchor == '/')


def _physical_directory(path):
    _require(isinstance(path, Path))
    _require_local_path_shape(path)
    _require(str(path) == os.path.abspath(path))
    # Check ancestors first: never knowingly traverse an observed link/reparse
    # ancestor to inspect its child. Concurrent replacement is not sandboxed.
    for current in reversed((path, *path.parents)):
        info = current.lstat()
        _require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode))
        _require(not getattr(info, 'st_file_attributes', 0) & 0x400)
    _require(os.path.normcase(str(path.resolve(strict=True))) == os.path.normcase(str(path)))
    info = path.stat()
    return (info.st_dev, info.st_ino)


def _file_identity(info):
    _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    _require(not getattr(info, 'st_file_attributes', 0) & 0x400)
    return (info.st_dev, info.st_ino)


def _new_file(path, contents):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        identity = _file_identity(os.fstat(stream.fileno()))
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())
    return identity


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('utf8')


class SimulationProfile:
    """An in-process capability for only the newly created simulation directory."""

    def __init__(self, parent, root, profile_id, parent_identity, identities, manifest_bytes, manifest_identity):
        self.root = root
        self.profile_id = profile_id
        self._parent = parent
        self._parent_identity = parent_identity
        self._identities = identities
        self._manifest_bytes = manifest_bytes
        self._manifest_identity = manifest_identity
        self._finalized = False

    def _check(self):
        _require(not self._finalized)
        _require(_physical_directory(self._parent) == self._parent_identity)
        for directory, expected in self._identities.items():
            _require(_physical_directory(directory) == expected)
        expected_names = set(DIRECTORIES) | {MANIFEST}
        with os.scandir(self.root) as entries:
            names = set()
            for index, entry in enumerate(entries):
                _require(index < len(expected_names) and entry.name in expected_names)
                names.add(entry.name)
        _require(names == expected_names)
        for name in DIRECTORIES:
            with os.scandir(self.root / name) as entries:
                _require(next(entries, None) is None)
        path = self.root / MANIFEST
        before = path.lstat()
        _require(_file_identity(before) == self._manifest_identity)
        _require(before.st_size == len(self._manifest_bytes) and before.st_size <= 4096)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            _require(_file_identity(opened) == self._manifest_identity)
            _require(opened.st_size == before.st_size)
            contents = stream.read(4097)
            after = os.fstat(stream.fileno())
        _require((opened.st_size, opened.st_mtime_ns) == (after.st_size, after.st_mtime_ns))
        _require(contents == self._manifest_bytes)
        _require(_physical_directory(self.root) == self._identities[self.root])

    def verify_fresh(self):
        try:
            self._check()
            return True
        except ProfileError as error:
            raise ProfileError(error.code, self.profile_id) from None
        except (OSError, ValueError):
            raise ProfileError('profile_io_failed', self.profile_id) from None

    def record_outcome(self, outcome):
        """Write only a finite synthetic outcome. Existing/unknown data is never read."""
        _require(type(outcome) is str and outcome in OUTCOMES)
        try:
            self._check()
            value = {
                'schema_version': 1, 'profile_id': self.profile_id, 'outcome': outcome,
                'simulation_only': True, 'retained': True, 'real_account_verified': False,
                'native_login_enabled': False, 'credential_encryption_verified': False,
                'browser_isolation_verified': False, 'security_boundary_verified': False,
                'logout_performed': False, 'deletion_performed': False,
            }
            contents = _json_bytes(value)
            _require(len(contents) <= 4096)
            _new_file(self.root / 'output' / RESULT, contents)
            self._finalized = True
            return value
        except ProfileError as error:
            raise ProfileError(error.code, self.profile_id) from None
        except (OSError, ValueError):
            raise ProfileError('profile_io_failed', self.profile_id) from None


def create_simulation_profile(parent):
    """Create an exclusive fresh child. A failure retains any partial child."""
    _require(type(parent) is Path or isinstance(parent, Path))
    created_id = None
    try:
        parent_identity = _physical_directory(parent)
        profile_id = 'native-login-sim-' + uuid.uuid4().hex
        root = parent / profile_id
        root.mkdir(mode=0o700, exist_ok=False)
        created_id = profile_id
        _require(_physical_directory(parent) == parent_identity)
        identities = {root: _physical_directory(root)}
        for name in DIRECTORIES:
            directory = root / name
            directory.mkdir(mode=0o700, exist_ok=False)
            identities[directory] = _physical_directory(directory)
        manifest = {
            'schema_version': 1, 'profile_id': profile_id, 'simulation_only': True,
            'native_login_enabled': False, 'reuse_allowed': False,
            'credential_encryption_verified': False, 'browser_isolation_verified': False,
            'security_boundary_verified': False,
        }
        contents = _json_bytes(manifest)
        manifest_identity = _new_file(root / MANIFEST, contents)
        profile = SimulationProfile(parent, root, profile_id, parent_identity, identities, contents, manifest_identity)
        profile.verify_fresh()
        return profile
    except ProfileError as error:
        raise ProfileError(error.code, created_id) from None
    except (OSError, ValueError):
        raise ProfileError('profile_create_failed', created_id) from None
