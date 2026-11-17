"""Native task backend on loopback, using the existing Native HTTP wire contract."""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import ssl

from .contracts import parse_json
from .http_server import make_server
from .ledger_contract import HarnessConfig
from .ledger_http import LedgerHTTP
from .ledger_migrate import migrate_settled
from .ledger_store import NativeLedger


@dataclass(frozen=True)
class LedgerRuntimeConfig:
    database: Path
    harness: HarnessConfig
    port: int
    tls_certfile: str
    tls_keyfile: str
    source_token_env: str = 'NATIVE_BRIDGE_SOURCE_TOKEN'
    node_token_env: str = 'NATIVE_BRIDGE_NODE_TOKEN'

    @classmethod
    def load(cls, path):
        with Path(path).open('rb') as stream:
            value = parse_json(stream.read(65_537))
        if type(value) is not dict:
            raise ValueError('Native ledger configuration required')
        schema_version = value.pop('schema_version', None)
        if type(schema_version) is not int or schema_version != 1:
            raise ValueError('Native ledger configuration required')
        value['database'] = Path(value['database'])
        value['harness'] = HarnessConfig.from_dict(value['harness'])
        config = cls(**value)
        if (not config.database.is_absolute() or config.database.is_symlink()
                or type(config.port) is not int or not 1 <= config.port <= 65535
                or config.source_token_env == config.node_token_env
                or any(not re.fullmatch(r'[A-Z_][A-Z_0-9]*', name)
                       for name in (config.source_token_env, config.node_token_env))):
            raise ValueError('Invalid Native ledger configuration')
        if not all(Path(value).is_absolute() and Path(value).is_file() for value in (config.tls_certfile, config.tls_keyfile)):
            raise ValueError('Explicit TLS certificate and key files required')
        return config


@contextmanager
def serve(config, environment=None):
    environment = os.environ if environment is None else environment
    store = NativeLedger(config.database, config.harness)
    api = LedgerHTTP(store, environment[config.source_token_env], environment[config.node_token_env])
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(config.tls_certfile, config.tls_keyfile)
    with make_server(api, port=config.port, exclusive=True) as server:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        yield server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--import-bridge', type=Path, metavar='BRIDGE_SNAPSHOT')
    parser.add_argument('--journal-snapshot', type=Path)
    args = parser.parse_args()
    if bool(args.import_bridge) != bool(args.journal_snapshot):
        parser.error('Import requires both SQLite snapshots')
    config = LedgerRuntimeConfig.load(args.config)
    if args.import_bridge:
        migrate_settled(args.import_bridge, args.journal_snapshot, config.database, config.harness)
        print('Native ledger imported. Admission closed.')
        return
    with serve(config) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
