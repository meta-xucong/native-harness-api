"""User-run Codex device-code login or model directory. No native launch on import."""
from functools import partial
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_assets import verify_assets
from codex_profile import create_profile, open_profile, environment
from codex_control_protocol import CodexControlProtocol, stages
from native_harness_api.codex_session import account_identity_digest
from public_harness_login import SafeParser, safe_label, WindowsTTY

RETAINED = []
EXPLANATION = '''Codex 0.167.0 / Windows x64 / ChatGPT device-code only.
Login creates a NEW dedicated retained profile. Existing credentials are not imported.
Complete authentication only on the official website. Never paste credentials here.
Cancellation is not logout or rollback. Native credentials remain in the local profile.
Restricted ACLs are not credential encryption, network isolation or a no-tools guarantee.
Model discovery does not perform inference or prove subscription resale permission.
Real authenticated execution has not been verified by development fixtures.'''


def run_control(profile, executable, *, mode, on_device=None, owner_factory=None):
    """Caller holds profile lock until all native ownership/IO cleanup is confirmed."""
    if owner_factory is None:
        from native_harness_api.codex_owned_session import CodexOwnedSession
        from native_harness_api.supervised_acp_transport import SupervisedACPTransport
        owner_factory = lambda: CodexOwnedSession(SupervisedACPTransport(),
            protocol_factory=partial(CodexControlProtocol, mode=mode, on_device=on_device), stages_factory=stages)
    if mode == 'login':
        profile.record('login_started')
    else:
        profile.begin_execution()
    owner = owner_factory()
    try:
        result = owner.run([str(executable), '--listen', 'stdio://'], str(executable.parent), environment(profile),
            workspace=str(profile.root / 'workspace'), model_id='', prompt='',
            expected_account_digest=profile.expected_account_digest,
            timeout=900 if mode == 'login' else 60, cleanup_timeout=15)
    except BaseException:
        RETAINED.append((owner, profile))
        profile.quarantine()
        raise
    if not result.get('cleanup_confirmed'):
        RETAINED.append((owner, profile))
        profile.quarantine()
        raise ValueError('CLEANUP_UNCERTAIN_PROFILE_RETAINED')
    session = result.get('session', {})
    if (result.get('fault') is not None or session.get('fault') is not None
            or session.get('terminal_observed') is not True or session.get('outcome') != 'succeeded'):
        if mode == 'login':
            profile.record('unknown_retained')
        else:
            profile.quarantine()
        raise ValueError('CONTROL_NOT_VERIFIED')
    if mode == 'models':
        profile.finish_execution(cleanup_confirmed=True)
    return session


def confirm(ui, text):
    ui.begin_prompt(text)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        answer = ui.poll()
        if answer is not None:
            return answer
        time.sleep(.02)
    ui.abandon()
    return False


def export_target(profile, assets_root, models, native_model, public_alias):
    from native_harness_api.harness_registry import make_recipe, binding_for
    from dataclasses import asdict
    if native_model not in {m['model'] for m in models}:
        raise ValueError('MODEL_NOT_IN_CURRENT_DIRECTORY')
    recipe = make_recipe(adapter_id='codex.app-server.v1', profile_path=str(profile.root),
        profile_id=profile.root.name, account_fingerprint=profile.expected_account_digest,
        native_model=native_model, public_alias=public_alias)
    return {'recipe': recipe, 'binding': asdict(binding_for(recipe)), 'assets_root': str(assets_root)}


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument('action', choices=('login', 'models'), nargs='?')
    parser.add_argument('--explain', action='store_true')
    parser.add_argument('--assets-root', type=Path)
    parser.add_argument('--profile-parent', type=Path)
    parser.add_argument('--profile-root', type=Path)
    parser.add_argument('--target-model')
    parser.add_argument('--public-alias')
    profile = None
    try:
        args = parser.parse_args(argv)
        if args.explain:
            print(EXPLANATION)
            return 0
        if os.name != 'nt' or not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError('WINDOWS_TTY_REQUIRED')
        if bool(args.target_model) != bool(args.public_alias) or (args.action != 'models' and args.target_model):
            raise ValueError('TARGET_MODEL_AND_ALIAS_REQUIRE_MODELS')
        executable = verify_assets(args.assets_root)
        if args.action == 'login':
            if args.profile_parent is None or args.profile_root is not None:
                raise ValueError('LOGIN_REQUIRES_NEW_PROFILE_PARENT')
            print(EXPLANATION)
            ui = WindowsTTY()
            if not confirm(ui, 'Create a new profile and begin one ChatGPT device-code login? Type yes:'):
                return 1
            profile = create_profile(args.profile_parent)
            print('Retained profile: ' + str(profile.root))
            def display(url, code):
                print('Verify this is the official OpenAI website before entering the code:')
                print(safe_label(url))
                print('Device code: ' + safe_label(code), flush=True)
            result = run_control(profile, executable, mode='login', on_device=display)
            account = result['account']
            print('Email: ' + safe_label(account.get('email')))
            print('Workspace: ' + safe_label(account.get('workspaceName')))
            if not confirm(ui, 'Confirm this is your intended account? Type yes:'):
                profile.record('identity_rejected')
                return 1
            profile.record('authenticated_confirmed', account_identity_digest(account))
            print('Local profile confirmed. Use models --profile-root with this profile path.')
        elif args.action == 'models':
            if args.profile_root is None or args.profile_parent is not None:
                raise ValueError('MODELS_REQUIRES_CONFIRMED_PROFILE_ROOT')
            profile = open_profile(args.profile_root)
            result = run_control(profile, executable, mode='models')
            output = (export_target(profile, executable.parent.parent, result['models'], args.target_model, args.public_alias)
                      if args.target_model else {'models': result['models']})
            print(json.dumps(output, ensure_ascii=True, indent=2))
        else:
            raise ValueError('ACTION_REQUIRED')
        return 0
    except (Exception, KeyboardInterrupt):
        # Never print native protocol/error payloads or arbitrary exception strings.
        print('CODEX_CONTROL_NOT_COMPLETED; profile retained. No automatic retry.', file=sys.stderr)
        return 1
    finally:
        if profile is not None and not any(p is profile for _, p in RETAINED):
            profile.close()
        while RETAINED:
            try:
                time.sleep(1)
            except KeyboardInterrupt:
                continue


if __name__ == '__main__':
    raise SystemExit(main())
