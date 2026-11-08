"""Pinned public assets, data-only acquisition. No automatic entry point or hooks."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile
import time
import urllib.request
import zipfile

SFE_URL = 'https://registry.npmjs.org/@tencent-ai/agent-harness-win32-x64/-/agent-harness-win32-x64-0.2.0-beta.16.tgz'
SFE_SHA = 'e1e47c6ee6364251fcf7dffece12fed8287af6a6866dfa1192a40f9180585bda'
EXE_SHA = '5a55b64f73eadc515120212f117cdfe4b25d4633ab4a57717aab2ba90b6e97b2'
NODE_URL = 'https://nodejs.org/dist/v22.16.0/node-v22.16.0-win-x64.zip'
SUMS_URL = 'https://nodejs.org/dist/v22.16.0/SHASUMS256.txt'
NODE_SHA = '21c2d9735c80b8f86dab19305aa6a9f6f59bbc808f68de3eef09d5832e3bfbbd'
PRODUCT_SHA = 'a73bea91defd7d5f38d5672c19111681ba52244ee8b9b64cc59dc3e3992eed61'
SFE_TREE = {'sha256': 'ba4ea62cc1a2be04098416a91d97951bd6fe4e6a7aee02713d1a5e9912420488', 'files': 65, 'bytes': 193650089}
NODE_TREE = {'sha256': '560eaae5d491412a3a2b0b84a39e3d9ded151d7e103ff6b2ff54df53fde4fdb0', 'files': 2447, 'bytes': 98429225}
NODE_EXE_SHA = 'c5ff4c736112dd483c750fd4149d30c8a116db1a49b8b3ec88be4b65e6c86c19'
OVERLAY_SHA = '65e7e270a13413de3fcdb1810cd18e3cfd0037d9e2d4d1213cc26d48c6f054ac'
MAX_FILES = 20000
MAX_FILE = 200 * 1024 * 1024
MAX_TOTAL = 600 * 1024 * 1024

class AssetError(RuntimeError):
    pass

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def regular(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise AssetError('UNSAFE_FILE')
    return info

def directory(path):
    info = Path(path).lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise AssetError('UNSAFE_DIRECTORY')

def member_parts(name):
    if not isinstance(name, str) or not name or len(name) > 240 or '\\' in name or ':' in name or name.startswith('/'):
        raise AssetError('UNSAFE_MEMBER')
    parts = name.rstrip('/').split('/')
    for part in parts:
        if not part or part in ('.', '..') or part[-1] in '. ' or any(ord(c) < 32 for c in part) or any(c in '<>"|?*~' for c in part):
            raise AssetError('UNSAFE_MEMBER')
        if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I):
            raise AssetError('UNSAFE_MEMBER')
    return tuple(parts)

def validate_inventory(entries):
    """entries: (name, size, is_directory); reject aliases even in parent names."""
    if len(entries) > MAX_FILES:
        raise AssetError('ENTRY_LIMIT')
    nodes, explicit, total = {}, set(), 0
    for name, size, is_dir in entries:
        parts = member_parts(name)
        if not isinstance(size, int) or size < 0 or size > MAX_FILE or (is_dir and size):
            raise AssetError('SIZE_LIMIT')
        total += size
        if total > MAX_TOTAL:
            raise AssetError('TOTAL_LIMIT')
        for i in range(1, len(parts) + 1):
            spelling = '/'.join(parts[:i]); key = spelling.casefold()
            kind = is_dir if i == len(parts) else True
            old = nodes.get(key)
            if old is not None and old != (spelling, kind):
                raise AssetError('MEMBER_ALIAS')
            nodes[key] = (spelling, kind)
        key = '/'.join(parts).casefold()
        if key in explicit:
            raise AssetError('DUPLICATE_MEMBER')
        explicit.add(key)
    return total

def _write(target, name, source, size):
    dest = target.joinpath(*member_parts(name))
    dest.parent.mkdir(parents=True, exist_ok=True)
    for parent in (dest.parent, *dest.parent.parents):
        directory(parent)
        if parent == target:
            break
    count = 0
    with dest.open('xb') as output:
        while chunk := source.read(min(1024 * 1024, size - count + 1)):
            count += len(chunk)
            if count > size:
                raise AssetError('MEMBER_SIZE_MISMATCH')
            output.write(chunk)
    if count != size:
        raise AssetError('MEMBER_SIZE_MISMATCH')

def extract_tar(archive, target, expected_sha):
    if regular(archive).st_size > MAX_FILE:
        raise AssetError('ARCHIVE_SIZE')
    if sha(archive) != expected_sha:
        raise AssetError('ARCHIVE_HASH')
    with tarfile.open(archive, 'r:gz') as bundle:
        members = []
        for item in bundle:
            if len(members) >= MAX_FILES or not (item.isfile() or item.isdir()) or item.linkname or item.sparse:
                raise AssetError('TAR_TYPE_OR_LIMIT')
            members.append(item)
        validate_inventory([(m.name, m.size, m.isdir()) for m in members])
        target = Path(target); target.mkdir()
        for item in members:
            if item.isdir():
                target.joinpath(*member_parts(item.name)).mkdir(parents=True, exist_ok=True)
            else:
                with bundle.extractfile(item) as source:
                    _write(target, item.name, source, item.size)
    return tree_digest(target)

def extract_zip(archive, target, expected_sha):
    if regular(archive).st_size > MAX_FILE:
        raise AssetError('ARCHIVE_SIZE')
    if sha(archive) != expected_sha:
        raise AssetError('ARCHIVE_HASH')
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        for m in members:
            mode = m.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if m.flag_bits & 1 or kind not in (0, stat.S_IFREG, stat.S_IFDIR) or bool(m.external_attr & 0x400):
                raise AssetError('ZIP_TYPE')
            if kind == stat.S_IFDIR and not m.is_dir():
                raise AssetError('ZIP_TYPE')
        validate_inventory([(m.filename, m.file_size, m.is_dir()) for m in members])
        target = Path(target); target.mkdir()
        for m in members:
            if m.is_dir():
                target.joinpath(*member_parts(m.filename)).mkdir(parents=True, exist_ok=True)
            else:
                with bundle.open(m) as source:
                    _write(target, m.filename, source, m.file_size)
    return tree_digest(target)

def tree_digest(root):
    directory(root)
    records, count, total = [], 0, 0
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            directory(Path(current) / name)
        for name in files:
            path = Path(current) / name; info = regular(path)
            count += 1; total += info.st_size
            if count > MAX_FILES or total > MAX_TOTAL:
                raise AssetError('TREE_LIMIT')
            records.append([path.relative_to(root).as_posix(), info.st_size, sha(path)])
    records.sort()
    digest = hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()
    return {'sha256': digest, 'files': count, 'bytes': total}

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AssetError('REDIRECT')

def download(url, path, limit):
    if url not in (SFE_URL, NODE_URL, SUMS_URL):
        raise AssetError('URL_NOT_PINNED')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    deadline = time.monotonic() + 300; total = 0
    with opener.open(url, timeout=60) as response, Path(path).open('xb') as output:
        if response.url != url:
            raise AssetError('REDIRECT')
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > limit or time.monotonic() > deadline:
                raise AssetError('DOWNLOAD_LIMIT')
            output.write(chunk)

def verify_node_sums(data):
    lines = data.decode('ascii').splitlines()
    matches = [line.split() for line in lines if line.split() and line.split()[-1] == 'node-v22.16.0-win-x64.zip']
    if matches != [[NODE_SHA, 'node-v22.16.0-win-x64.zip']]:
        raise AssetError('NODE_SUMS_MISMATCH')

def product_overlay(path):
    info = regular(path)
    if info.st_size > 1024 * 1024 or sha(path) != PRODUCT_SHA:
        raise AssetError('PRODUCT_HASH')
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    auth = config.get('authentication', {})
    if (config.get('productName'), config.get('applicationName'), auth.get('id'), auth.get('type'), auth.get('attributes', {}).get('platform')) != ('WorkBuddy', 'WorkBuddy', 'workbuddy-desktop', 'account-scoped-external-link', 'workbuddy'):
        raise AssetError('PRODUCT_IDENTITY')
    for key in ('include', 'productConfigEnv', 'productConfigPathEnv', 'replacements', 'mergeStrategy'):
        config.pop(key, None)
    config['productVersion'] = '5.9.3'
    return {'schemaVersion': 1, 'kind': 'overlay', 'configuration': config}

def acquire_assets(root, seven_zip):
    """Root must already be an exclusive, private attempt directory. No vendor execution."""
    root = Path(root).absolute(); directory(root)
    for parent in root.parents:
        directory(parent)
    sfe, node, sums = root / 'sfe.tgz', root / 'node.zip', root / 'node-SHASUMS256.txt'
    download(SFE_URL, sfe, MAX_FILE)
    download(SUMS_URL, sums, 1024 * 1024)
    verify_node_sums(sums.read_bytes())
    download(NODE_URL, node, MAX_FILE)
    sfe_tree = extract_tar(sfe, root / 'sfe', SFE_SHA)
    node_tree = extract_zip(node, root / 'node', NODE_SHA)
    if sfe_tree != SFE_TREE or node_tree != NODE_TREE:
        raise AssetError('TREE_PIN_MISMATCH')
    executable = root / 'sfe/package/bin/agent-harness.exe'
    host_node = root / 'node/node-v22.16.0-win-x64/node.exe'
    regular(executable); regular(host_node)
    if sha(executable) != EXE_SHA or sha(host_node) != NODE_EXE_SHA:
        raise AssetError('EXECUTABLE_HASH')
    # Existing acquisition uses approved archive tooling only; never launches WorkBuddy.
    from official_workbuddy_smoke import acquire
    source = root / 'workbuddy-product-source'; source.mkdir()
    acquire(source, seven_zip)
    original = source / 'official/resources/app.asar.unpacked/cli/product.json'
    overlay = product_overlay(original)
    product = root / 'workbuddy-product.json'
    with product.open('x', encoding='utf-8') as out:
        json.dump(overlay, out, ensure_ascii=False, separators=(',', ':'))
    if sha(product) != OVERLAY_SHA:
        raise AssetError('OVERLAY_PIN_MISMATCH')
    return {'executable': executable, 'host_node': host_node, 'product_config': product,
            'pins': {'sfe_archive': SFE_SHA, 'node_archive': NODE_SHA, 'executable': EXE_SHA,
                     'host_node': sha(host_node), 'product_source': PRODUCT_SHA,
                     'product_config': sha(product), 'node_sums': sha(sums),
                     'sfe_tree': sfe_tree, 'node_tree': node_tree}}
