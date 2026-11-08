"""Pinned, unmodified Sub2API forwarder -> real Bridge/Provider -> FakeHarness.

Run with Native src, Bridge src and integration/ on PYTHONPATH. No accounts,
repositories of users/keys, billing database or real model are involved.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading

UPSTREAM_SHA = '6b4b37db43443b5e88e424fe3c69602cc3810dfb'
BRIDGE_SHA = '52dc083207a8a02dfc14fe2ec1ea10f0cc0a54a8'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True, type=Path)
    parser.add_argument('--bridge', required=True, type=Path)
    parser.add_argument('--go', default='go')
    args = parser.parse_args()
    for repo, revision in ((args.upstream, UPSTREAM_SHA), (args.bridge, BRIDGE_SHA)):
        actual = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != revision:
            raise RuntimeError('Fixture requires the documented exact source revision')
        if subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'], text=True).strip():
            raise RuntimeError('Upstream/Bridge fixture source must be unmodified')
    # Fail rather than auto-install a compiler or quietly skip integration.
    subprocess.run([args.go, 'version'], check=True, timeout=15)
    from render_bridge.api import create_app
    from render_bridge.domain import BridgeStore, SourceScope
    from render_bridge.harness import HarnessConfig, HarnessBinding
    from render_bridge.node_api import NodeScope
    from native_harness_api.adapters import FakeHarness
    from native_harness_api.bridge_client import BridgeClient, NodeClient
    from native_harness_api.contracts import Binding
    from native_harness_api.facade import Service
    from native_harness_api.node import Journal, NodePolicy, Runner
    from native_harness_api.openai_provider import OpenAIProvider
    from native_harness_api.runtime import node_tick
    from test_real_bridge import bridge_server, facade_server

    with ExitStack() as stack:
        root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='native-provider-chain-')))
        binding = Binding('fixture-binding', 1, 'synthetic-basic', 'fake-model')
        owner = ('fixture-tenant', 'fixture-principal')
        cfg = HarnessConfig(source_id='fixture-source', workspace_id='fixture-workspace',
            ledger_instance='fixture-ledger', node_id='fixture-node', executor_instance='fixture-executor',
            allowed_owners=(owner,), bindings=(HarnessBinding(**binding.wire()),), accepting_new=True)
        store = BridgeStore(root/'bridge.sqlite', root/'artifacts', dispatch_mode='node_pull', harness=cfg)
        app = create_app(store=store,
            source_tokens={'fixture-bridge-source-token': SourceScope('fixture-source', 'fixture-workspace')},
            node_tokens={'fixture-bridge-node-token': NodeScope('fixture-source','fixture-workspace','fixture-node','fixture-executor')})
        bridge_url = stack.enter_context(bridge_server(app))
        bridge = BridgeClient(bridge_url, 'fixture-bridge-source-token', 'fixture-ledger')
        service = Service('fixture-source', bridge, {binding.model_alias: binding}, True, owner)
        provider = OpenAIProvider({'fixture-native-provider-token': service}, mode='p1b_integration', wait_seconds=20, poll_seconds=.02)
        provider_url = stack.enter_context(facade_server(provider))
        fake = FakeHarness(root/'fake-native')
        runner = Runner(NodeClient(bridge_url, 'fixture-bridge-node-token'), Journal(root/'journal'), fake,
            policy=NodePolicy('fixture-source','fixture-ledger',frozenset({owner}),(binding,)))
        node_tick(runner)  # Register availability before the first HTTP request.
        stop, faults = threading.Event(), []
        def work():
            try:
                while not stop.wait(.02):
                    node_tick(runner)
            except Exception as exc:
                faults.append(type(exc).__name__)
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        try:
            overlay = root/'overlay.json'
            virtual = args.upstream.resolve()/'backend/internal/service/native_provider_chain_test.go'
            fixture = Path(__file__).resolve().parent/'go/native_provider_chain_test.go'
            overlay.write_text(json.dumps({'Replace': {str(virtual): str(fixture)}}))
            env = dict(os.environ, NATIVE_PROVIDER_FIXTURE_ORIGIN=provider_url, GOTOOLCHAIN='local')
            subprocess.run([args.go, 'test', '-tags', 'unit', '-overlay', str(overlay),
                            './internal/service', '-run', '^TestNativeProviderChain$', '-count=1', '-v'],
                           cwd=args.upstream.resolve()/'backend', env=env, check=True, timeout=900)
        finally:
            stop.set()
            worker.join(10)
        if worker.is_alive() or faults:
            raise RuntimeError('Synthetic Node failed or did not stop')
        if fake.start_count != 2:
            raise AssertionError(f'Expected two independent native starts, got {fake.start_count}')
        print('PASS: original Sub2API forwarder, real HTTP Bridge/Provider, 2 synthetic starts; no real account or billing')


if __name__ == '__main__':
    main()
