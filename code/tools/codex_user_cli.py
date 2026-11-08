"""Existing Codex CLI connection. Only our sidecar is read/written, never credentials."""
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import public_harness_login_profile as storage
from codex_control_protocol import CodexControlProtocol, stages
from native_harness_api.codex_session import account_identity_digest

VERSION = '0.167.0'
PREFIX = 'codex-user-cli-'
POLICY = storage.ProfilePolicy('codex-user-cli-0.167.0', PREFIX, {'protocol': 'app-server-v2', 'version': VERSION})
CONTEXT_FIELDS = frozenset(('cli_path', 'cli_sha256', 'codex_home'))
CONTEXT_FILE = 'control/cli-context.json'
RETAINED = []


def executable_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_cli(executable=None, environ=None):
    """Locate only; caller confirms the displayed result before any execution."""
    env = os.environ if environ is None else environ
    selected = executable or shutil.which('codex', path=env.get('PATH', ''))
    if not selected:
        raise ValueError('CODEX_CLI_NOT_FOUND')
    path = Path(selected).resolve()
    if path.suffix.lower() == '.cmd':
        packages = path.parent / 'node_modules' / '@openai'
        roots = (packages / 'codex' / 'node_modules' / '@openai' / 'codex-win32-x64', packages / 'codex-win32-x64', packages / 'codex')
        candidates = [r / 'vendor' / 'x86_64-pc-windows-msvc' / 'bin' / 'codex.exe' for r in roots]
        path = next((p.resolve() for p in candidates if p.is_file()), None)
    if path is None or path.name.lower() != 'codex.exe' or not path.is_file():
        raise ValueError('SELECT_OFFICIAL_CODEX_EXE')
    home = env.get('CODEX_HOME')
    if not home:
        user_home = env.get('USERPROFILE') or env.get('HOME')
        if not user_home:
            raise ValueError('CODEX_HOME_NOT_FOUND')
        home = str(Path(user_home) / '.codex')
    home = Path(home).resolve()
    if not home.is_dir():
        raise ValueError('EXISTING_CODEX_HOME_REQUIRED')
    return dict(cli_path=str(path), cli_sha256=executable_digest(path), codex_home=str(home))


def environment(context, environ=None):
    result = dict(os.environ if environ is None else environ)
    result['CODEX_HOME'] = context['codex_home']
    return result


def verify_context(context):
    if type(context) is not dict or set(context) != CONTEXT_FIELDS or any(not isinstance(v, str) or not v for v in context.values()):
        raise ValueError('CODEX_CONTEXT_INVALID')
    exe, home = Path(context['cli_path']), Path(context['codex_home'])
    if (not exe.is_absolute() or exe.name.lower() != 'codex.exe' or not home.is_absolute()
            or not home.is_dir() or executable_digest(exe) != context['cli_sha256']):
        raise ValueError('CODEX_CONTEXT_CHANGED')
    return context


def verify_version(context):
    verify_context(context)
    result = subprocess.run([context['cli_path'], '--version'], env=environment(context),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False)
    if result.returncode != 0 or result.stdout.strip() != ('codex-cli ' + VERSION).encode():
        raise ValueError('CODEX_VERSION_UNSUPPORTED')


def create_profile(parent, context):
    verify_context(context)
    parent, home = Path(parent).resolve(), Path(context['codex_home']).resolve()
    if parent == home or home in parent.parents:
        raise ValueError('SIDECAR_MUST_BE_OUTSIDE_CODEX_HOME')
    profile = storage.create_profile(parent, policy=POLICY)
    try:
        storage._write_new(profile.root / CONTEXT_FILE, (json.dumps(context, sort_keys=True) + '\n').encode(), profile._security)
        return profile
    except BaseException:
        profile.close()
        raise


def open_profile(root):
    return storage.open_profile(root, policy=POLICY)


def read_context(profile):
    profile.verify()
    path = profile.root / CONTEXT_FILE
    if path.stat().st_size > 16384:
        raise ValueError('CODEX_CONTEXT_INVALID')
    profile._security.check(path, False)
    context = json.loads(path.read_text(encoding='utf-8'))
    verify_context(context)
    home, root = Path(context['codex_home']).resolve(), profile.root.resolve()
    if root == home or home in root.parents:
        raise ValueError('SIDECAR_MUST_BE_OUTSIDE_CODEX_HOME')
    return context


def discover(profile, context, *, owner_factory=None):
    """One owned account/catalog request; no login/start or thread/turn request."""
    if read_context(profile) != context:
        raise ValueError('CODEX_CONTEXT_CHANGED')
    fresh = profile.state == 'fresh'
    if not fresh and profile.state != 'authenticated_confirmed':
        raise ValueError('SIDECAR_NOT_CONFIRMED')
    if owner_factory is None:
        from native_harness_api.codex_owned_session import CodexOwnedSession
        from native_harness_api.supervised_acp_transport import SupervisedACPTransport
        owner_factory = lambda: CodexOwnedSession(SupervisedACPTransport(),
            protocol_factory=partial(CodexControlProtocol, mode='connect' if fresh else 'models', expected_codex_home=context['codex_home']), stages_factory=stages)
    owner = owner_factory()
    if fresh:
        profile.record('login_started')  # Existing storage setup intent, not login RPC.
    else:
        profile.begin_execution()
    clean = False
    try:
        result = owner.run([context['cli_path'], 'app-server', '--listen', 'stdio://'],
            str(profile.root / 'workspace'), environment(context),
            workspace=str(profile.root / 'workspace'), model_id='', prompt='',
            expected_account_digest=profile.expected_account_digest, timeout=60, cleanup_timeout=15)
        clean = result.get('cleanup_confirmed') is True
        session = result.get('session', {})
        if clean:
            if fresh:
                profile._replace('fresh', None)  # Observation only; no native task was submitted.
            else:
                profile.finish_execution(cleanup_confirmed=True)
        if (not clean or result.get('fault') is not None or session.get('fault') is not None
                or session.get('terminal_observed') is not True or session.get('outcome') != 'succeeded'):
            raise ValueError('CODEX_CONNECTION_NOT_VERIFIED')
        return session
    except BaseException:
        if not clean:
            RETAINED.append((owner, profile))
            profile.quarantine()
        raise


def confirm_profile(profile, account):
    if profile.state == 'fresh':
        digest = account_identity_digest(account)
        profile.record('login_started')
        profile.record('authenticated_confirmed', digest)
    elif profile.state != 'authenticated_confirmed' or profile.expected_account_digest != account_identity_digest(account):
        raise ValueError('CODEX_ACCOUNT_MISMATCH')


def export_target(profile, context, models, native_model, public_alias):
    from native_harness_api.harness_registry import make_recipe, binding_for
    from dataclasses import asdict
    if profile.state != 'authenticated_confirmed' or read_context(profile) != context:
        raise ValueError('SIDECAR_NOT_CONFIRMED')
    if native_model not in {m['model'] for m in models}:
        raise ValueError('MODEL_NOT_IN_CURRENT_DIRECTORY')
    recipe = make_recipe(adapter_id='codex.user-cli.v1', profile_path=str(profile.root),
        profile_id=profile.root.name, account_fingerprint=profile.expected_account_digest,
        native_model=native_model, public_alias=public_alias, **context)
    return {'recipe': recipe, 'binding': asdict(binding_for(recipe)), 'assets_root': str(Path(context['cli_path']).parent)}


def close_profile(profile):
    if not any(p is profile for _, p in RETAINED):
        profile.close()


def hold_retained():
    while RETAINED:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            continue
