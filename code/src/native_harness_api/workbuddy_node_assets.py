"""Fixed identity of the separately packaged WorkBuddy Node runtime."""

LEGACY_ADAPTER_ID = 'workbuddy.public-node.v1'
ADAPTER_ID = 'workbuddy.public-node.v2'
PIPE_ADAPTER_ID = 'workbuddy.public-node.pipe.v1'
CLIENT_KIND = 'native-harness-windows-workbuddy-node-client'
NODE_EXE = 'node/node-v22.23.5-win-x64/node.exe'
NODE_ENTRY = 'harness/launcher.mjs'
PRODUCT_VERSION = '5.11.3'
HOST_ARGS = ('--ide-name', 'WorkBuddy', '--ide-type', 'WorkBuddy', '--ide-version', PRODUCT_VERSION,
             '--ext-name', 'workbuddy-desktop', '--ext-version', PRODUCT_VERSION, '--user-agent', 'CLI/2.176.0')

# Frozen from the verified official downloads and local derived asset tree.
LEGACY_NODE_ASSETS = {
    'harness': {'sha256': '09004bf353767a4c795f9c1c95219e6c93da6bac7b7275e48049722c2dcf5e7b',
                'files': 12535, 'bytes': 253143134},
    'node': {'sha256': 'b49f2ac03ef9184010a77c01b054e02821c2a9a3dbffa3badd4a360d50bc1d78',
             'files': 2032, 'bytes': 99162991},
    'node_exe_sha256': '247e9693d316da9857e44990e5e11709f596f18112251b8026ec6eed80bdf38a',
    'product_sha256': '65e7e270a13413de3fcdb1810cd18e3cfd0037d9e2d4d1213cc26d48c6f054ac',
}
NODE_ASSETS = {**LEGACY_NODE_ASSETS,
               'product_sha256': 'e7b91f6ad620174abd1f8a97ca1a93221a80766517e815d28e01b26f2cf1e023',
               'host_args': HOST_ARGS}
NODE_RELEASES = {
    LEGACY_ADAPTER_ID: ('5.9.3', LEGACY_NODE_ASSETS),
    ADAPTER_ID: (PRODUCT_VERSION, NODE_ASSETS),
    PIPE_ADAPTER_ID: (PRODUCT_VERSION, NODE_ASSETS),
}
