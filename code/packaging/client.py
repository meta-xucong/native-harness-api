"""Windows client entry. Never logs in, starts inference or installs on import."""
import argparse
import json
from pathlib import Path
import sys


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, "Invalid client arguments. Use --help; no input values were printed.\n")

def main(argv=None):
    root = Path(__file__).resolve().parent
    sys.path[:0] = [str(root / 'src'), str(root / 'tools')]
    parser = Parser(description='Native Harness local client. Explicit commands only. Optional prefix: --harness workbuddy|codex.')
    parser.add_argument('command', choices=('login', 'models', 'node', 'facade', 'preflight', 'verify-assets'))
    argv = list(sys.argv[1:] if argv is None else argv)
    manifest = root / 'CLIENT-MANIFEST.json'
    kind = json.loads(manifest.read_text(encoding='utf-8'))['kind'] if manifest.exists() else 'native-harness-windows-client'
    if kind not in ('native-harness-windows-client', 'native-harness-windows-codex-client'):
        parser.error('Unknown package kind')
    harness = 'codex' if kind == 'native-harness-windows-codex-client' else 'workbuddy'
    if argv[:1] == ['--harness']:
        if len(argv) < 2 or argv[1] != harness:
            parser.error('Harness not included in this package')
        argv = argv[2:]
    args = parser.parse_args(argv[:1])
    remaining = argv[1:]
    if args.command in ('login', 'models'):
        login_parser = Parser(prog='Client '+args.command, allow_abbrev=False)
        profile_flag = '--profile-parent' if args.command == 'login' else ('--profile-root' if harness == 'codex' else '--profile')
        login_parser.add_argument(profile_flag, dest='profile')
        login_parser.add_argument('--explain', action='store_true')
        if args.command == 'models':
            login_parser.add_argument('--target-model')
            login_parser.add_argument('--public-alias')
        login_args = login_parser.parse_args(remaining)
        remaining = (['--explain'] if login_args.explain else []) + ([profile_flag, login_args.profile] if login_args.profile is not None else [])
        if args.command == 'models':
            if bool(login_args.target_model) != bool(login_args.public_alias):
                login_parser.error('Target model and alias must be paired')
            if login_args.target_model:
                remaining += ['--target-model', login_args.target_model, '--public-alias', login_args.public_alias]
        if harness == 'codex':
            from codex_login import main as entry
            return entry([args.command, '--assets-root', str(root / 'assets'), *remaining])
        if args.command == 'login':
            from public_harness_login import main as entry
        else:
            from public_harness_models import main as entry
        return entry(['--assets-root', str(root / 'assets'), *remaining])
    if args.command == 'preflight':
        if harness == 'codex': parser.error('WorkBuddy preflight is not a Codex acceptance check')
        from public_harness_preflight import main as entry
        return entry(remaining)
    if args.command == 'verify-assets':
        if remaining: parser.error('verify-assets takes no parameters')
        if harness == 'codex':
            from codex_assets import verify_assets
            verify_assets(root / 'assets')
        else:
            from public_harness_runtime_assets import verify_runtime_assets
            verify_runtime_assets(root / 'assets')
        print('Pinned runtime assets verified. No native process or account was accessed.')
        return 0
    from native_harness_api.runtime import main as runtime
    return runtime([args.command, *remaining])


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('Client stopped. Check configuration and retained state. No automatic retry.', file=sys.stderr)
        raise SystemExit(1)
