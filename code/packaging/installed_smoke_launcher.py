"""Explicit invocation only; no downloads, discovery or automatic runs."""
from pathlib import Path
import sys


def main():
    if not sys.flags.isolated or not sys.dont_write_bytecode:
        raise SystemExit('Use the bundled runtime with -I -B as documented.')
    root = Path(__file__).resolve().parent
    sys.path[:0] = [str(root/'src'),str(root/'tools')]
    from installed_workbuddy_smoke import main as run
    return run()

if __name__ == '__main__':
    raise SystemExit(main())
