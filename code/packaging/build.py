"""Offline, allowlisted Windows probe bundle builder. Never executes bundled code."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

RUNTIME_URL = 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip'
RUNTIME_SHA256 = '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'
SOURCE_FILES = ('__init__.py', 'workbuddy_probe.py', 'workbuddy_acp.py')
ASSETS = ('launcher.py', 'Probe.cmd', 'config.example.json', 'README.zh-CN.md')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and '..' not in p.parts and '\\' not in name and ':' not in name and str(p) == name


def read_regular(path):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents) or not path.is_file():
        raise ValueError(f'Expected regular file: {path}')
    return path.read_bytes()


def text_bytes(data):
    """Canonical UTF-8/LF for app text only; runtime remains byte-identical."""
    return data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')


def verify_source_revision(code_dir, revision):
    """CLI gate: exact Git HEAD and raw committed bytes, including builder itself."""
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Full audited source commit required')
    code_dir = Path(code_dir).resolve()
    root = code_dir.parent

    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    if Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve() != root:
        raise ValueError('Expected repository root above code directory')
    if git('rev-parse', 'HEAD').decode().strip() != revision:
        raise ValueError('HEAD does not equal audited revision')
    paths = [code_dir / 'src/native_harness_api' / n for n in SOURCE_FILES]
    paths += [code_dir / 'packaging' / n for n in (*ASSETS, 'build.py')]
    for path in paths:
        relative = path.relative_to(root).as_posix()
        committed = git('show', revision + ':' + relative)
        if read_regular(path) != committed:
            raise ValueError('Working tree differs from audited Git blob: ' + relative)


def runtime_files(data):
    if digest(data) != RUNTIME_SHA256:
        raise ValueError('Official runtime SHA256 mismatch')
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for item in z.infolist():
            if not safe_name(item.filename) or item.is_dir() or item.filename in result:
                raise ValueError('Unsafe runtime member')
            result[item.filename] = z.read(item)
    for required in ('python.exe', 'python313.zip', 'python313._pth', 'LICENSE.txt'):
        if required not in result:
            raise ValueError('Incomplete runtime')
    return {'runtime/' + k: v for k, v in result.items()}


def build(code_dir, runtime_zip, output, revision):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Full audited source commit required')
    code_dir, output = Path(code_dir), Path(output)
    files = runtime_files(read_regular(Path(runtime_zip)))
    for name in SOURCE_FILES:
        files['src/native_harness_api/' + name] = text_bytes(read_regular(code_dir / 'src/native_harness_api' / name))
    for name in ASSETS:
        files[name] = text_bytes(read_regular(code_dir / 'packaging' / name))
    manifest = {'format': 1, 'kind': 'standalone-workbuddy-probe', 'source_revision': revision,
                'runtime': {'url': RUNTIME_URL, 'sha256': RUNTIME_SHA256, 'version': '3.13.17', 'platform': 'windows-x64'},
                'files': {k: digest(v) for k, v in sorted(files.items())}}
    files['RELEASE-MANIFEST.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    files['SHA256SUMS'] = ''.join(f'{digest(v)}  {k}\n' for k, v in sorted(files.items())).encode()
    # Exclusive creation prevents overwriting any previous release or local state.
    with output.open('xb') as stream:
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as z:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                z.writestr(info, data)
    verify(output)
    return digest(output.read_bytes())


def verify(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or any(not safe_name(n) for n in names):
            raise ValueError('Unsafe or duplicate bundle member')
        manifest = json.loads(z.read('RELEASE-MANIFEST.json'))
        expected = manifest['files']
        if set(names) != set(expected) | {'RELEASE-MANIFEST.json', 'SHA256SUMS'}:
            raise ValueError('Unexpected/missing bundle member')
        for name, sha in expected.items():
            if digest(z.read(name)) != sha:
                raise ValueError('Bundle hash mismatch: ' + name)
        sums = ''.join(f'{digest(z.read(n))}  {n}\n' for n in sorted(set(names) - {'SHA256SUMS'}))
        if z.read('SHA256SUMS') != sums.encode():
            raise ValueError('Checksum manifest mismatch')
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime-zip', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--source-revision', required=True)
    a = p.parse_args()
    code_dir = Path(__file__).resolve().parents[1]
    verify_source_revision(code_dir, a.source_revision)
    print(build(code_dir, a.runtime_zip, a.output, a.source_revision))
