"""Run the three-repository synthetic HTTP chain.

Authentication lookup remains a fixture. Operation routing uses an in-memory
fixture by default, or real PostgreSQL only with --disposable-postgres and an
explicit operator-supplied disposable migrated DSN. No model/provider request,
credential extraction or deployment occurs. --harness exercises the generic
trial mode with a self-authored executor, NOT the named vendor runtime. Add
code/tests and code/integration to PYTHONPATH along with both repositories src.
"""
from contextlib import ExitStack
from pathlib import Path
import argparse
import json
import os
import subprocess
import tempfile
import threading

from render_bridge.api import create_app
from render_bridge.domain import BridgeStore, SourceScope
from render_bridge.harness import HarnessConfig, HarnessBinding
from render_bridge.node_api import NodeScope
from native_harness_api.adapters import FakeHarness
from native_harness_api.bridge_client import BridgeClient, NodeClient
from native_harness_api.contracts import Binding
from native_harness_api.facade import Facade, Service
from native_harness_api.node import Journal, NodePolicy, Runner
from native_harness_api.runtime import node_tick
from test_real_bridge import bridge_server, facade_server


def run(backend: Path, go: Path, *, disposable_postgres=False, harness=None):
    if not (backend / "go.mod").is_file() or not go.is_file():
        raise ValueError("Explicit checked-out Sub2API backend and Go binary required")
    if disposable_postgres and not os.environ.get("NATIVE_HARNESS_TEST_DSN"):
        raise ValueError("Explicit disposable PostgreSQL DSN required")
    if harness and disposable_postgres:
        raise ValueError("Generic-mode fixture uses the explicit memory route test")
    test_name = "TestNativeHarnessExternalBridge" if disposable_postgres else "TestNativeHarnessExternalBridgeMemoryRoute"
    with ExitStack() as stack:
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        binding = Binding("binding-test", 1, harness + "-test" if harness else "synthetic-test", "fixture-text" if harness else "fake-text")
        mode = "harness_local_trial" if harness else "synthetic"
        source, ledger, workspace, node, executor = "source-test", "ledger-test", "workspace-test", "node-test", "executor-test"
        source_token, node_token = "synthetic-test-bridge-source", "synthetic-test-bridge-node"
        facade_token = "synthetic-test-service-token"
        config = HarnessConfig(source_id=source, workspace_id=workspace, ledger_instance=ledger,
            node_id=node, executor_instance=executor, allowed_owners=(("user-7", "key-9"),),
            bindings=(HarnessBinding(**binding.wire()),), accepting_new=True, execution_mode=mode)
        store = BridgeStore(root / "bridge.sqlite", root / "artifacts", dispatch_mode="node_pull", harness=config)
        app = create_app(store=store, source_tokens={source_token: SourceScope(source, workspace)},
                         node_tokens={node_token: NodeScope(source, workspace, node, executor)})
        bridge_url = stack.enter_context(bridge_server(app))
        bridge = BridgeClient(bridge_url, source_token, ledger)
        bridge.capabilities()  # Fail closed if this isn't the typed extension.
        facade = Facade({facade_token: Service(source, bridge, {binding.model_alias: binding}, True, ("user-7", "key-9") if harness else None)},
                         mode="harness_local_trial" if harness else "p1b_integration", wait_seconds=0.2)
        facade_url = stack.enter_context(facade_server(facade))
        if harness:
            from native_harness_api.public_sfe_adapter import DurableNativeAdapter
            from test_multi_harness_slot import terminal
            calls = []
            def invoke(**kwargs):
                calls.append(kwargs['model_id'])
                kwargs['on_event']({'type':'text_snapshot','text':'fixture answer'})
                return 'fixture answer'
            (root / "evidence").mkdir()
            fake = DurableNativeAdapter(root / 'evidence', bindings=(binding,),
                resolve=lambda b: (invoke, terminal))
        else:
            fake = FakeHarness(root / "synthetic-native")
        runner = Runner(NodeClient(bridge_url, node_token), Journal(root / "journal"), fake,
                         policy=NodePolicy(source, ledger, frozenset({("user-7", "key-9")}), (binding,), mode="harness_local_trial" if harness else "p1b_integration"))
        node_tick(runner)
        stop = threading.Event()
        faults = []
        def work():
            while not stop.is_set():
                try:
                    node_tick(runner)
                except Exception as exc:
                    # Only collect bounded codes, never exception payloads/tokens.
                    code = getattr(exc, "code", type(exc).__name__)
                    if code != "store_busy":
                        faults.append(code)
                stop.wait(0.05)
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        try:
            env = dict(os.environ, NATIVE_HARNESS_TEST_FACADE=facade_url)
            if harness: env["NATIVE_HARNESS_TEST_ALIAS"] = binding.model_alias
            if not disposable_postgres:
                env.pop("NATIVE_HARNESS_TEST_DSN", None)  # Default never accesses a DB.
            result = subprocess.run([str(go), "test", "-tags=unit", "./internal/handler", "-count=1",
                    "-run", "^" + test_name + "$", "-timeout=90s", "-v"],
                    cwd=backend, env=env, text=True, capture_output=True, timeout=180)
        finally:
            stop.set()
            worker.join(10)
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
        print(json.dumps({"node_fault_codes": sorted(set(faults)), "synthetic_starts": (len(calls) if harness else fake.start_count),
            "journal_states": [{k: record.get(k) for k in ("outcome", "liveness", "launch_intent", "acknowledged")}
                               for record in runner.journal.records()]}, sort_keys=True))
        if result.returncode or "--- PASS: " + test_name not in result.stdout:
            raise RuntimeError("The explicit Sub2API integration test did not pass")
        if worker.is_alive() or faults or (len(calls) if harness else fake.start_count) != 1:
            raise RuntimeError("Node did not complete exactly one synthetic execution")
        print(json.dumps({"actual_bridge_http": True, "actual_sub2api_middleware_handler_http": True,
                          "synthetic_native_starts": (len(calls) if harness else fake.start_count), "auth_lookup_fixture": True,
                          "route_store_fixture": not disposable_postgres, "postgresql_verified": disposable_postgres,
                          "execution_mode": mode, "alias": binding.model_alias, "actual_native_model": False, "paid_inference": False}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sub2api-backend", type=Path, required=True)
    parser.add_argument("--go", type=Path, required=True)
    parser.add_argument("--harness", choices=("workbuddy", "codex"), help="Generic mode with self-authored fixture only")
    parser.add_argument("--disposable-postgres", action="store_true", help="Use explicit NATIVE_HARNESS_TEST_DSN; operator must supply disposable migrated DB")
    args = parser.parse_args()
    run(args.sub2api_backend.resolve(), args.go.resolve(), disposable_postgres=args.disposable_postgres, harness=args.harness)
