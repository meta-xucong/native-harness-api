"""Private-runtime entry point; no network, installs, discovery or auto-run."""
from pathlib import Path
import sys


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
    from native_harness_api.workbuddy_probe import main as probe_main
    return probe_main()


if __name__ == '__main__':
    raise SystemExit(main())
