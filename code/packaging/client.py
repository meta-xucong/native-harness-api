"""Windows client entry. Never logs in, starts inference or installs on import."""
import argparse
from pathlib import Path
import sys


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, "Invalid client arguments. Use --help; no input values were printed.\n")

def main(argv=None):
    root = Path(__file__).resolve().parent
    sys.path[:0] = [str(root / 'src'), str(root / 'tools')]
    parser = Parser(description='Native Harness local client. Explicit commands only.')
    parser.add_argument('command', choices=('login', 'models', 'node', 'facade', 'preflight', 'verify-assets'))
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv[:1])
    remaining = argv[1:]
    if args.command in ('login', 'models'):
        login_parser = Parser(prog='Client '+args.command, allow_abbrev=False)
        profile_flag = '--profile-parent' if args.command == 'login' else '--profile'
        login_parser.add_argument(profile_flag, dest='profile')
        login_parser.add_argument('--explain', action='store_true')
        login_args = login_parser.parse_args(remaining)
        remaining = (['--explain'] if login_args.explain else []) + ([profile_flag, login_args.profile] if login_args.profile is not None else [])
        if args.command == 'login':
            from public_harness_login import main as entry
        else:
            from public_harness_models import main as entry
        return entry(['--assets-root', str(root / 'assets'), *remaining])
    if args.command == 'preflight':
        from public_harness_preflight import main as entry
        return entry(remaining)
    if args.command == 'verify-assets':
        if remaining: parser.error('verify-assets takes no parameters')
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
