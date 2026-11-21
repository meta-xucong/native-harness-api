"""Read-only verification of the complete pinned Windows client asset layout.

No download, install, native execution, discovery or credential access. This is
integrity verification, not an OS sandbox or protection against concurrent edits.
"""
import ctypes
import os
from pathlib import Path
from public_harness_catalog_assets import (AssetError, directory, regular, sha,
    tree_digest, SFE_TREE, NODE_TREE, EXE_SHA, NODE_EXE_SHA, OVERLAY_SHA)
from native_harness_api.workbuddy_node_assets import ADAPTER_ID, NODE_EXE, NODE_ENTRY, NODE_RELEASES, PIPE_ADAPTER_ID


def checked_local_path(path):
    text = os.fspath(path)
    if not isinstance(text, str) or text.startswith(('\\\\', '//')) or '\x00' in text:
        raise AssetError('LOCAL_PATH_REQUIRED')
    root = Path(text)
    if not root.is_absolute() or '..' in root.parts:
        raise AssetError('ABSOLUTE_PATH_REQUIRED')
    if os.name == 'nt':
        if len(root.drive) != 2 or root.drive[1] != ':' or '~' in text:
            raise AssetError('FIXED_LOCAL_DRIVE_REQUIRED')
        get_type = ctypes.windll.kernel32.GetDriveTypeW
        get_type.argtypes = [ctypes.c_wchar_p]; get_type.restype = ctypes.c_uint
        if get_type(root.anchor) != 3:
            raise AssetError('FIXED_LOCAL_DRIVE_REQUIRED')
    for parent in reversed((root, *root.parents)):
        directory(parent)
    return root


def verify_runtime_assets(assets_root):
    root = checked_local_path(assets_root)
    sfe, node = root / 'sfe', root / 'node'
    if tree_digest(sfe) != SFE_TREE or tree_digest(node) != NODE_TREE:
        raise AssetError('RUNTIME_TREE_MISMATCH')
    executable = sfe / 'package/bin/agent-harness.exe'
    host_node = node / 'node-v22.16.0-win-x64/node.exe'
    product = root / 'product.json'
    for path, expected in ((executable, EXE_SHA), (host_node, NODE_EXE_SHA), (product, OVERLAY_SHA)):
        regular(path)
        if sha(path) != expected:
            raise AssetError('RUNTIME_FILE_MISMATCH')
    return {'executable': executable, 'host_node': host_node, 'product_config': product,
            'pins': {'sfe_tree': dict(SFE_TREE), 'node_tree': dict(NODE_TREE),
                     'executable_sha256': EXE_SHA, 'node_sha256': NODE_EXE_SHA,
                     'product_sha256': OVERLAY_SHA}}


def verify_node_runtime_assets(assets_root, *, adapter_id=ADAPTER_ID):
    version, pins = NODE_RELEASES[adapter_id]
    root = checked_local_path(assets_root)
    for name in ('harness', 'node'):
        if tree_digest(root / name) != pins[name]:
            raise AssetError('NODE_RUNTIME_TREE_MISMATCH')
    node, launcher, product = (root / name for name in (NODE_EXE, NODE_ENTRY, 'product.json'))
    for path, wanted in ((node, pins['node_exe_sha256']), (product, pins['product_sha256'])):
        regular(path)
        if sha(path) != wanted:
            raise AssetError('NODE_RUNTIME_FILE_MISMATCH')
    regular(launcher)
    return {'adapter_id': adapter_id, 'host_node': node, 'launcher': launcher,
            'product_config': product, 'product_version': version}


def workbuddy_command(assets):
    adapter_id = assets.get('adapter_id')
    release = NODE_RELEASES.get(adapter_id)
    command = ([str(assets['host_node']), str(assets['launcher'])]
               if release else [str(assets['executable'])])
    command += ['--stdio', '--strict-mcp-config', '--product-config', str(assets['product_config'])]
    if release:
        command.extend(release[1].get('host_args', ()))
    if adapter_id == PIPE_ADAPTER_ID:
        relay = Path(__file__).resolve().with_name('workbuddy_named_pipe.mjs')
        regular(relay)
        command.insert(1, str(relay))
        command.remove('--stdio')
    return command
