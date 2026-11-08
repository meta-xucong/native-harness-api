"""Explicit, new Windows profiles for user-operated managed authentication.

Only this module's bounded metadata is read, never native credential contents.
Restricted ACLs are not encryption, a browser sandbox or hostile-user isolation.
No profile deletion, token import, automatic login or retry exists here.
"""
from __future__ import annotations

import ctypes as C
from functools import wraps
import json
import os
from pathlib import Path
import re
import stat
import uuid

from public_harness_catalog_assets import EXE_SHA, NODE_EXE_SHA, OVERLAY_SHA, SFE_TREE, NODE_TREE, member_parts, AssetError, sha

DIRECTORIES = ('home', 'config', 'appdata', 'localappdata', 'temp', 'workspace', 'logs', 'control')
MANIFEST = 'managed-profile.json'
LOCK = 'profile.lock'
PREFIX = 'workbuddy-managed-'
PINS = dict(executable=EXE_SHA, host_node=NODE_EXE_SHA, product_config=OVERLAY_SHA,
            sfe_tree=SFE_TREE['sha256'], node_tree=NODE_TREE['sha256'])
STATES = frozenset(('fresh', 'login_started', 'authenticated_confirmed', 'unknown_retained',
                    'cancelled_unauthenticated', 'unauthenticated', 'identity_rejected',
                    'execution_started', 'execution_unknown'))


class ProfileError(RuntimeError):
    def __init__(self, code='PROFILE_REJECTED'):
        if code not in ('PROFILE_REJECTED', 'WINDOWS_REQUIRED', 'PROFILE_BUSY', 'PROFILE_IO_FAILED'):
            code = 'PROFILE_REJECTED'
        self.code = code
        super().__init__(code)


def require(value):
    if not value:
        raise ProfileError()


def _api(operation):
    @wraps(operation)
    def safe(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except ProfileError:
            raise
        except (OSError, ValueError, TypeError, RecursionError):
            raise ProfileError('PROFILE_IO_FAILED') from None
    return safe


def _json(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def _user_sid(value):
    # Current process TokenUser, including local/domain and AzureAD user SIDs.
    # Not an arbitrary SID supplied in a command or manifest.
    return (type(value) is str and len(value) <= 192
            and re.fullmatch(r'S-1-\d+(?:-\d+){1,15}', value) is not None
            and value not in ('S-1-5-18', 'S-1-5-19', 'S-1-5-20'))


class _SecurityAttributes(C.Structure):
    _fields_ = [('length', C.c_uint32), ('descriptor', C.c_void_p), ('inherit', C.c_int32)]


class WinSecurity:
    """Win32 ACL implementation. No powershell, subprocess or persistent OS setting."""
    def __init__(self):
        if os.name != 'nt':
            raise ProfileError('WINDOWS_REQUIRED')
        self.k = C.WinDLL('kernel32.dll', use_last_error=True)
        self.a = C.WinDLL('advapi32.dll', use_last_error=True)
        specs = {
            'GetCurrentProcess': (self.k, [], C.c_void_p),
            'CloseHandle': (self.k, [C.c_void_p], C.c_int32),
            'LocalFree': (self.k, [C.c_void_p], C.c_void_p),
            'GetDriveTypeW': (self.k, [C.c_wchar_p], C.c_uint32),
            'CreateDirectoryW': (self.k, [C.c_wchar_p, C.POINTER(_SecurityAttributes)], C.c_int32),
            'OpenProcessToken': (self.a, [C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)], C.c_int32),
            'GetTokenInformation': (self.a, [C.c_void_p, C.c_uint32, C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32)], C.c_int32),
            'ConvertSidToStringSidW': (self.a, [C.c_void_p, C.POINTER(C.c_void_p)], C.c_int32),
            'ConvertStringSecurityDescriptorToSecurityDescriptorW': (self.a, [C.c_wchar_p, C.c_uint32, C.POINTER(C.c_void_p), C.POINTER(C.c_uint32)], C.c_int32),
            'GetNamedSecurityInfoW': (self.a, [C.c_wchar_p, C.c_uint32, C.c_uint32, C.POINTER(C.c_void_p), C.c_void_p, C.POINTER(C.c_void_p), C.c_void_p, C.POINTER(C.c_void_p)], C.c_uint32),
            'GetSecurityDescriptorControl': (self.a, [C.c_void_p, C.POINTER(C.c_uint16), C.POINTER(C.c_uint32)], C.c_int32),
            'GetAce': (self.a, [C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)], C.c_int32),
        }
        for name, (lib, args, result) in specs.items():
            method = getattr(lib, name); method.argtypes = args; method.restype = result
        token = C.c_void_p()
        require(self.a.OpenProcessToken(self.k.GetCurrentProcess(), 8, C.byref(token)))
        try:
            size = C.c_uint32()
            self.a.GetTokenInformation(token, 1, None, 0, C.byref(size))
            require(0 < size.value <= 65536)
            buffer = C.create_string_buffer(size.value)
            require(self.a.GetTokenInformation(token, 1, buffer, size.value, C.byref(size)))
            self.sid = self._sid(C.c_void_p.from_buffer(buffer).value)
            require(_user_sid(self.sid))
            # An elevated Windows token can default new files to a group owner
            # (TokenOwner), rather than TokenUser. This adds no DACL principal.
            owner_size = C.c_uint32()
            self.a.GetTokenInformation(token, 4, None, 0, C.byref(owner_size))
            require(0 < owner_size.value <= 65536)
            owner_buffer = C.create_string_buffer(owner_size.value)
            require(self.a.GetTokenInformation(token, 4, owner_buffer, owner_size.value, C.byref(owner_size)))
            self.default_owner_sid = self._sid(C.c_void_p.from_buffer(owner_buffer).value)
        finally:
            self.k.CloseHandle(token)

    def _sid(self, pointer):
        output = C.c_void_p()
        require(pointer and self.a.ConvertSidToStringSidW(pointer, C.byref(output)))
        try:
            value = C.wstring_at(output)
            require(len(value) <= 192)
            return value
        finally:
            self.k.LocalFree(output)

    def path_shape(self, path):
        require(len(path.drive) == 2 and path.drive[0].isascii() and path.drive[0].isalpha()
                and path.drive[1] == ':' and path.root == '\\')
        require(self.k.GetDriveTypeW(path.anchor) == 3)

    def create(self, path):
        descriptor = C.c_void_p()
        sddl = f'O:{self.sid}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{self.sid})'
        require(self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), None))
        try:
            attributes = _SecurityAttributes(C.sizeof(_SecurityAttributes), descriptor, 0)
            require(self.k.CreateDirectoryW(str(path), C.byref(attributes)))
        finally:
            self.k.LocalFree(descriptor)
        self.check(path, True, protected=True)

    def _owner_allowed(self, owner, protected):
        return owner == self.sid or (not protected and owner == self.default_owner_sid)

    def check(self, path, directory, protected=False):
        owner, acl, descriptor = C.c_void_p(), C.c_void_p(), C.c_void_p()
        require(self.a.GetNamedSecurityInfoW(str(path), 1, 5, C.byref(owner), None,
                    C.byref(acl), None, C.byref(descriptor)) == 0)
        try:
            require(acl.value)
            require(self._owner_allowed(self._sid(owner), protected))
            control, revision = C.c_uint16(), C.c_uint32()
            require(self.a.GetSecurityDescriptorControl(descriptor, C.byref(control), C.byref(revision)))
            require(control.value & 4)  # SE_DACL_PRESENT; no NULL/unrestricted DACL.
            if protected:
                require(control.value & 0x1000)
            count = C.c_uint16.from_address(acl.value + 4).value
            require(count == 2)
            seen = set()
            for index in range(count):
                ace = C.c_void_p()
                require(self.a.GetAce(acl, index, C.byref(ace)))
                header = C.string_at(ace.value, 4)
                require(header[0] == 0 and (header[1] & ~0x13) == 0)
                require(int.from_bytes(header[2:4], 'little') >= 20)
                if directory:
                    require(header[1] & 3 == 3)
                require(C.c_uint32.from_address(ace.value + 4).value == 0x1f01ff)
                sid = self._sid(ace.value + 8)
                require(sid not in seen); seen.add(sid)
            require(seen == {self.sid, 'S-1-5-18'})
        finally:
            self.k.LocalFree(descriptor)


def _info(path, directory):
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400)
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    return info


def _identity(info):
    return info.st_dev, info.st_ino


def _physical(path, security):
    require(isinstance(path, Path) and path.is_absolute() and str(path) == os.path.abspath(path))
    require(len(str(path)) <= 220 and all(not part.endswith(('.', ' ')) and '~' not in part
            for part in path.parts[1:]))
    if path.parts[1:]:
        try:
            member_parts('/'.join(path.parts[1:]))
        except AssetError:
            raise ProfileError() from None
    security.path_shape(path)  # Reject remote/device path before metadata traversal.
    for ancestor in reversed((path, *path.parents)):
        _info(ancestor, True)
    require(os.path.normcase(str(path.resolve(strict=True))) == os.path.normcase(str(path)))
    return _identity(_info(path, True))


def _check_manifest(data, name, sid):
    require(type(data) is dict and set(data) == {'schema_version', 'kind', 'profile_id', 'owner_sid',
            'pins', 'state', 'confirmed_account_digest', 'native_encryption_verified', 'browser_isolation_verified'})
    require(type(data['schema_version']) is int and data['schema_version'] == 1
            and data['kind'] == 'public-beta16-cn-managed' and data['profile_id'] == name
            and re.fullmatch(PREFIX + r'[0-9a-f]{32}', name) and data['owner_sid'] == sid
            and data['pins'] == PINS and type(data['state']) is str and data['state'] in STATES)
    require(data['native_encryption_verified'] is False and data['browser_isolation_verified'] is False)
    digest = data['confirmed_account_digest']
    require((data['state'] in ('authenticated_confirmed', 'execution_started') and type(digest) is str
             and re.fullmatch(r'[0-9a-f]{64}', digest)) or
            (data['state'] not in ('authenticated_confirmed', 'execution_started') and digest is None))
    return data


def _write_new(path, contents, security):
    require(type(contents) is bytes and len(contents) <= 4096)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode))
        security.check(path, False)
        stream.write(contents); stream.flush(); os.fsync(stream.fileno())
    _info(path, False)


class ManagedProfile:
    def __init__(self, root, security, data, lock):
        self.root, self._security, self._data, self._lock = root, security, data, lock
        self._identity = _physical(root, security)
        self._closed = False
        self._execution_owner = False

    @property
    def state(self):
        return self._data['state']

    @property
    def expected_account_digest(self):
        return self._data['confirmed_account_digest']

    def _control(self):
        """Validate only the owned control capability; never native file contents."""
        require(not self._closed and not self._lock.closed
                and _physical(self.root, self._security) == self._identity)
        self._security.check(self.root, True, protected=True)
        lock_path = self.root / LOCK
        require(_identity(_info(lock_path, False)) == _identity(os.fstat(self._lock.fileno())))
        self._security.check(lock_path, False)
        current = _read_manifest(self.root, self._security)
        require(current == self._data)
        return current

    @_api
    def verify(self):
        """Metadata/ACL walk only. Never read any native credential, backup or log."""
        self._control()
        names = set()
        with os.scandir(self.root) as entries:
            for index, entry in enumerate(entries):
                require(index < len(DIRECTORIES) + 2)
                names.add(entry.name)
        require(names == set(DIRECTORIES) | {MANIFEST, LOCK})
        total = 0
        stack = [(self.root, 0)]
        while stack:
            parent, depth = stack.pop(); require(depth <= 32)
            with os.scandir(parent) as entries:
                for entry in entries:
                    total += 1; require(total <= 50000)
                    path = Path(entry.path)
                    info = path.lstat()
                    is_dir = stat.S_ISDIR(info.st_mode)
                    _info(path, is_dir)
                    self._security.check(path, is_dir)
                    if is_dir:
                        stack.append((path, depth + 1))
        for name in DIRECTORIES:
            _info(self.root / name, True)
        data = _read_manifest(self.root, self._security)
        require(data == self._data)
        return True

    @_api
    def record(self, state, confirmed_account_digest=None):
        require(type(state) is str and state in STATES
                and state not in ('fresh', 'execution_started', 'execution_unknown'))
        require((self.state == 'fresh' and state == 'login_started') or
                (self.state == 'login_started' and state not in ('fresh', 'login_started')))
        self._replace(state, confirmed_account_digest)

    def _replace(self, state, digest):
        self.verify()
        data = dict(self._data, state=state, confirmed_account_digest=digest)
        _check_manifest(data, self.root.name, self._security.sid)
        temporary = self.root / ('metadata-' + uuid.uuid4().hex + '.tmp')
        _write_new(temporary, _json(data), self._security)
        os.replace(temporary, self.root / MANIFEST)
        self._data = data
        self.verify()

    @_api
    def begin_execution(self):
        """Persist before spawn. A process crash must never silently release this gate."""
        require(self.state == 'authenticated_confirmed' and not self._execution_owner)
        self._replace('execution_started', self.expected_account_digest)
        self._execution_owner = True

    @_api
    def finish_execution(self, *, cleanup_confirmed):
        """Only this handle's execution may settle; does not settle the Bridge task."""
        require(type(cleanup_confirmed) is bool and self._execution_owner and self.state == 'execution_started')
        self._replace('authenticated_confirmed' if cleanup_confirmed else 'execution_unknown',
                      self.expected_account_digest if cleanup_confirmed else None)
        self._execution_owner = False

    @_api
    def quarantine(self):
        """Reduce authority even when an unrelated native file fails its ACL check.

        Root, lock and own metadata must still be valid. This does NOT verify the
        whole profile or cleanup, and does not permit the caller to release an
        uncertain owned process/lock. Never logout, delete or repair permissions.
        """
        current = self._control()
        state = 'execution_unknown' if current['state'] in ('execution_started', 'execution_unknown') else 'unknown_retained'
        data = dict(current, state=state, confirmed_account_digest=None)
        _check_manifest(data, self.root.name, self._security.sid)
        if data != current:
            temporary = self.root / ('metadata-' + uuid.uuid4().hex + '.tmp')
            _write_new(temporary, _json(data), self._security)
            os.replace(temporary, self.root / MANIFEST)
        self._data = data
        self._control()
        self._execution_owner = False

    @_api
    def environment(self, host_node):
        require(self.state in ('fresh', 'login_started', 'execution_started'))
        require(self.state != 'execution_started' or self._execution_owner)
        self.verify()
        host_node = Path(host_node)
        require(host_node.is_absolute() and host_node.name.lower() == 'node.exe')
        _physical(host_node.parent, self._security)
        require(0 < _info(host_node, False).st_size <= 200 * 1024 * 1024)
        require(sha(host_node) == NODE_EXE_SHA)
        system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        _physical(system, self._security)
        result = {'SystemRoot': str(system), 'WINDIR': str(system),
                  'PATH': str(system / 'System32'),
                  'AGENT_HARNESS_HOST_NODE_EXECUTABLE': str(host_node), 'DISABLE_TELEMETRY': '1'}
        for folder, keys in [('home', ('HOME', 'USERPROFILE')),
                ('config', ('CODEBUDDY_CONFIG_DIR', 'WORKBUDDY_CONFIG_DIR')),
                ('appdata', ('APPDATA',)), ('localappdata', ('LOCALAPPDATA',)), ('temp', ('TEMP', 'TMP'))]:
            for key in keys:
                result[key] = str(self.root / folder)
        return result

    def close(self):
        if not self._closed:
            self._closed = True
            self._lock.close()  # Windows releases this process's byte-range lock.

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _read_manifest(root, security):
    path = root / MANIFEST
    before = _info(path, False); require(0 < before.st_size <= 4096)
    security.check(path, False)
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno()); require(_identity(before) == _identity(opened))
        contents = stream.read(4097)
        after = os.fstat(stream.fileno())
    require(len(contents) <= 4096 and (opened.st_size, opened.st_mtime_ns) == (after.st_size, after.st_mtime_ns))
    require(_identity(_info(path, False)) == _identity(before))
    return _check_manifest(json.loads(contents, object_pairs_hook=_pairs), root.name, security.sid)


def _lock(root, security):
    import msvcrt
    path = root / LOCK
    info = _info(path, False); require(info.st_size == 1)
    security.check(path, False)
    stream = path.open('r+b', buffering=0)
    try:
        require(_identity(info) == _identity(os.fstat(stream.fileno())))
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return stream
    except OSError:
        stream.close(); raise ProfileError('PROFILE_BUSY') from None
    except BaseException:
        stream.close(); raise


@_api
def create_profile(parent):
    """New random profile only. Failed/partial directories remain for user review."""
    security = WinSecurity()
    parent = Path(parent); _physical(parent, security)
    root = parent / (PREFIX + uuid.uuid4().hex)
    security.create(root)
    for name in DIRECTORIES:
        security.create(root / name)
    data = dict(schema_version=1, kind='public-beta16-cn-managed', profile_id=root.name,
                owner_sid=security.sid, pins=dict(PINS), state='fresh', confirmed_account_digest=None,
                native_encryption_verified=False, browser_isolation_verified=False)
    _write_new(root / MANIFEST, _json(data), security)
    _write_new(root / LOCK, b'0', security)
    lock = _lock(root, security)
    try:
        profile = ManagedProfile(root, security, data, lock)
        profile.verify(); return profile
    except BaseException:
        lock.close(); raise


@_api
def open_profile(root):
    """Explicit tool-created profile only; lock held through its native process lifetime.

    Does not authenticate or prove readiness. Callers still need live Product,
    account identity digest and auth/status verification. No native data is read.
    """
    security = WinSecurity()
    root = Path(root); _physical(root, security)
    require(re.fullmatch(PREFIX + r'[0-9a-f]{32}', root.name))
    security.check(root, True, protected=True)
    lock = _lock(root, security)
    try:
        profile = ManagedProfile(root, security, _read_manifest(root, security), lock)
        profile.verify(); return profile
    except BaseException:
        lock.close(); raise
