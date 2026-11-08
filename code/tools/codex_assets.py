"""Pinned official Windows package. Data-only preparation, never native execution."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request
from public_harness_catalog_assets import AssetError, directory, regular, sha

VERSION = '0.167.0'
URL = 'https://github.com/openai/codex/releases/download/rust-v0.167.0/codex-app-server-package-x86_64-pc-windows-msvc.tar.gz'
PACKAGE_SHA = '16f99769bd4accc519cf22079645db445907182c8b96bc626f496b7390f5ad46'
PACKAGE_SIZE = 164336310
TREE = {'sha256': '23877309c187c68ca94b286fb9d002201f13e806b089dcb5141ae643c3e9e4fe', 'files': 7, 'bytes': 473204491}
EXE_SHA = 'd781e2c96caeae19f5c8280814d778b92d4958bcca86064d6bec09d789ae7e22'
FILES = (
    'bin/codex-app-server.exe', 'bin/codex-code-mode-host.exe', 'bin/codex-exec-server.exe',
    'codex-package.json', 'codex-path/rg.exe', 'codex-resources/codex-command-runner.exe',
    'codex-resources/codex-windows-sandbox-setup.exe',
)


def verify_assets(root):
    """Check full pinned layout, including sidecars used by the official executable."""
    root = Path(root).absolute()
    directory(root)
    records = []
    for path in root.rglob('*'):
        if path.is_dir():
            directory(path)
        else:
            size = regular(path).st_size
            records.append([path.relative_to(root).as_posix(), size, sha(path)])
    records.sort()
    digest = hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()
    if digest != TREE['sha256']:
        raise AssetError('CODEX_TREE_MISMATCH')
    return root / 'bin' / 'codex-app-server.exe'


def prepare_assets(archive, target):
    archive, target = Path(archive), Path(target)
    if regular(archive).st_size != PACKAGE_SIZE or sha(archive) != PACKAGE_SHA:
        raise AssetError('CODEX_PACKAGE_MISMATCH')
    # Exact official inventory, not a generic archive unpacker. No link extraction.
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        files = [m for m in members if m.isfile()]
        if (set(m.name for m in files) != set(FILES) or len(files) != len(FILES)
                or any(not m.isfile() and not (m.isdir() and m.name.rstrip('/') in
                    ('bin', 'codex-path', 'codex-resources')) for m in members)):
            raise AssetError('CODEX_PACKAGE_LAYOUT')
        target.mkdir()
        for member in files:
            path = target / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, path.open('xb') as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
    return verify_assets(target)


def download_archive(path):
    # GitHub's official release endpoint redirects to its release asset storage.
    # Exact content SHA, not the redirect hostname, is the executable trust anchor.
    with urllib.request.urlopen(URL, timeout=60) as response, Path(path).open('xb') as output:
        remaining = PACKAGE_SIZE
        while chunk := response.read(min(1024 * 1024, remaining + 1)):
            remaining -= len(chunk)
            if remaining < 0:
                raise AssetError('CODEX_PACKAGE_SIZE')
            output.write(chunk)
    if remaining or sha(path) != PACKAGE_SHA:
        raise AssetError('CODEX_PACKAGE_MISMATCH')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--download', action='store_true', help='Download pinned official package first')
    args = parser.parse_args(argv)
    if args.download:
        download_archive(args.archive)
    print(prepare_assets(args.archive, args.target))


if __name__ == '__main__':
    main()
