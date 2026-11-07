"""Offline allowlisted installed-entry diagnostic kit builder; never executes vendor code."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile

RUNTIME_URL = 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip'
RUNTIME_SHA256 = '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'
RUNTIME_NAMES = ('python.exe','pythonw.exe','python313.dll','python3.dll','vcruntime140.dll','vcruntime140_1.dll','LICENSE.txt','pyexpat.pyd','select.pyd','unicodedata.pyd','winsound.pyd','_asyncio.pyd','_bz2.pyd','_ctypes.pyd','_decimal.pyd','_elementtree.pyd','_hashlib.pyd','_lzma.pyd','_multiprocessing.pyd','_overlapped.pyd','_queue.pyd','_socket.pyd','_sqlite3.pyd','_ssl.pyd','_uuid.pyd','_wmi.pyd','_zoneinfo.pyd','libcrypto-3.dll','libffi-8.dll','libssl-3.dll','sqlite3.dll','python313.zip','python313._pth','python.cat')
SOURCES = {'controller.ps1':'tools/installed_smoke_controller.ps1',
           'tools/installed_workbuddy_smoke.py':'tools/installed_workbuddy_smoke.py',
           'src/native_harness_api/windows_job.py':'src/native_harness_api/windows_job.py',
           'launcher.py':'packaging/installed_smoke_launcher.py',
           'README.txt':'packaging/installed_smoke_README.txt'}
EMPTY_INIT = 'src/native_harness_api/__init__.py'
META = {'RELEASE-MANIFEST.json','SHA256SUMS'}
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    return bool(name) and name != '.' and not p.is_absolute() and '..' not in p.parts and '\\' not in name and ':' not in name and str(p) == name


def read_regular(path):
    path = Path(path).absolute()
    for ancestor in (*reversed(path.parents),path):
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0) & 0x400:
            raise ValueError('Links/reparse entries are not build inputs')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
        raise ValueError('Invalid build input')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev,opened.st_ino) != (info.st_dev,info.st_ino):
            raise ValueError('Input changed')
        data = stream.read(MAX_FILE)
        after = os.fstat(stream.fileno())
    if len(data) != info.st_size or (after.st_size,after.st_mtime_ns) != (info.st_size,info.st_mtime_ns):
        raise ValueError('Input changed')
    return data


def archive_files(data):
    result = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        total = 0
        for item in z.infolist():
            if (not safe_name(item.filename) or item.is_dir() or item.filename.casefold() in {n.casefold() for n in result}
                    or stat.S_ISLNK(item.external_attr >> 16) or item.file_size > MAX_FILE):
                raise ValueError('Unsafe archive member')
            total += item.file_size
            if total > MAX_TOTAL:
                raise ValueError('Archive limit')
            result[item.filename] = z.read(item)
    return result


def runtime_files(data):
    if digest(data) != RUNTIME_SHA256:
        raise ValueError('Official runtime SHA256 mismatch')
    files = archive_files(data)
    if set(files) != set(RUNTIME_NAMES):
        raise ValueError('Unexpected runtime inventory')
    return {'runtime/'+k:v for k,v in files.items()}


def verify_source_revision(code_dir, revision):
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Full audited commit required')
    code_dir = Path(code_dir).absolute()
    root = code_dir.parent
    def git(*args):
        return subprocess.run(['git','-C',str(root),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
    if Path(git('rev-parse','--show-toplevel').decode().strip()).resolve() != root.resolve():
        raise ValueError('Wrong repository')
    if git('rev-parse','HEAD').decode().strip() != revision:
        raise ValueError('HEAD mismatch')
    for relative in (*SOURCES.values(),'packaging/build_installed_smoke.py'):
        if read_regular(code_dir/relative) != git('show',revision+':code/'+relative):
            raise ValueError('Source differs from audited blob')


def build(code_dir, runtime_zip, output, revision):
    # Function permits synthetic fixture inputs; CLI always enforces Git pin.
    if not re.fullmatch('[0-9a-f]{40}',revision):
        raise ValueError('Full audited commit required')
    files = runtime_files(read_regular(runtime_zip))
    for name, relative in SOURCES.items():
        files[name] = read_regular(Path(code_dir)/relative).decode('utf-8').replace('\r\n','\n').replace('\r','\n').encode()
    files[EMPTY_INIT] = b''
    manifest = dict(format=1,kind='installed-workbuddy-entry-diagnostic-not-product',source_revision=revision,
                    runtime=dict(url=RUNTIME_URL,sha256=RUNTIME_SHA256),files={k:digest(v) for k,v in sorted(files.items())})
    files['RELEASE-MANIFEST.json'] = (json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
    files['SHA256SUMS'] = ''.join(f'{digest(v)}  {k}\n' for k,v in sorted(files.items())).encode()
    with Path(output).open('xb') as stream:
        with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as z:
            for name,data in sorted(files.items()):
                item = zipfile.ZipInfo(name,(1980,1,1,0,0,0));item.create_system=3;item.external_attr=0o100644<<16
                z.writestr(item,data)
    verify(output)
    return digest(read_regular(output))


def verify(path):
    files = archive_files(read_regular(path))
    expected = {'runtime/'+n for n in RUNTIME_NAMES} | set(SOURCES) | {EMPTY_INIT}
    if set(files) != expected | META or files[EMPTY_INIT] != b'':
        raise ValueError('Unexpected kit inventory')
    def pairs(items):
        result = {}
        for k,v in items:
            if k in result:raise ValueError('Duplicate JSON key')
            result[k]=v
        return result
    manifest = json.loads(files['RELEASE-MANIFEST.json'],object_pairs_hook=pairs)
    if (manifest.get('format') != 1 or manifest.get('kind') != 'installed-workbuddy-entry-diagnostic-not-product'
            or not re.fullmatch('[0-9a-f]{40}',manifest.get('source_revision',''))
            or manifest.get('runtime') != dict(url=RUNTIME_URL,sha256=RUNTIME_SHA256)
            or set(manifest.get('files',{})) != expected):
        raise ValueError('Invalid manifest')
    if any(digest(files[n]) != manifest['files'][n] for n in expected):
        raise ValueError('Member digest mismatch')
    sums = ''.join(f'{digest(v)}  {n}\n' for n,v in sorted(files.items()) if n != 'SHA256SUMS').encode()
    if files['SHA256SUMS'] != sums:
        raise ValueError('Checksum inventory mismatch')
    return manifest


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime-zip',required=True);p.add_argument('--output',required=True)
    p.add_argument('--source-revision',required=True)
    a=p.parse_args(argv);code=Path(__file__).absolute().parents[1]
    verify_source_revision(code,a.source_revision)
    print(build(code,a.runtime_zip,a.output,a.source_revision))

if __name__ == '__main__':
    main()
