"""Codex policy over the existing managed profile's ACL, lock and durable states."""
import os
from pathlib import Path
import public_harness_login_profile as storage
from codex_assets import PACKAGE_SHA, TREE

POLICY = storage.ProfilePolicy('codex-app-server-0.167.0-managed', 'codex-managed-',
                               {'package': PACKAGE_SHA, 'tree': TREE['sha256']})


def create_profile(parent):
    return storage.create_profile(parent, policy=POLICY)


def open_profile(root):
    return storage.open_profile(root, policy=POLICY)


def environment(profile):
    storage.require(profile.policy == POLICY and profile.state in ('fresh', 'login_started', 'execution_started'))
    storage.require(profile.state != 'execution_started' or profile._execution_owner)
    profile.verify()
    system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    storage._physical(system, profile._security)
    result = {'SystemRoot': str(system), 'WINDIR': str(system), 'PATH': str(system / 'System32')}
    for folder, keys in [('home', ('HOME', 'USERPROFILE')), ('config', ('CODEX_HOME',)),
                         ('appdata', ('APPDATA',)), ('localappdata', ('LOCALAPPDATA',)),
                         ('temp', ('TEMP', 'TMP'))]:
        for key in keys:
            result[key] = str(profile.root / folder)
    return result
