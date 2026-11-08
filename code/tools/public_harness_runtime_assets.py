"""Read-only verification of the complete pinned Windows client asset layout.

No download, install, native execution, discovery or credential access. This is
integrity verification, not an OS sandbox or protection against concurrent edits.
"""
import ctypes
import os
from pathlib import Path
from public_harness_catalog_assets import (AssetError, directory, regular, sha,
    tree_digest, SFE_TREE, NODE_TREE, EXE_SHA, NODE_EXE_SHA, OVERLAY_SHA)


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
