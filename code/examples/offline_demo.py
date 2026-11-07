"""One synthetic operation through real loopback HTTP and explicit test fakes."""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from native_harness_api.adapters import FakeHarness
from native_harness_api.bridge_client import BridgeClient, NodeClient, HTTPTransport
from native_harness_api.contracts import Binding, digest
from native_harness_api.errors import ChannelError
from native_harness_api.facade import Facade, Service
from native_harness_api.node import Journal, NodePolicy, Runner
from tests.fakes.gateway import FakeGateway, USER_TOKEN
from tests.fakes.ledger import FakeLedger, SOURCE_TOKEN, NODE_TOKEN
from tests.fakes.server import running


def main():
    with ExitStack() as stack:
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        ledger = FakeLedger(root / "test-ledger.sqlite")
        ledger_url = stack.enter_context(running(ledger))
        binding = Binding("fake-binding", 1, "synthetic-model", "fake-v1")
        bridge = BridgeClient(ledger_url, SOURCE_TOKEN, ledger.instance)
        service_token = "test-only-demo-facade-credential"
        facade = Facade({service_token: Service(ledger.source, bridge, {binding.model_alias: binding}, True)},
                        wait_seconds=0, mode="offline_prototype")
        facade_url = stack.enter_context(running(facade))
        gateway = FakeGateway(root / "test-gateway.sqlite", {"route": {"url": facade_url,
                              "token": service_token, "binding_digest": digest(binding.wire())}}, "route")
        gateway_url = stack.enter_context(running(gateway))
        client = HTTPTransport(gateway_url, USER_TOKEN)
        request = {"model": "synthetic-model", "messages": [{"role": "user", "content": "Synthetic offline demonstration"}]}
        headers = {"Idempotency-Key": "one-demo-operation"}
        try:
            client.request("POST", "/v1/chat/completions", request, extra_headers=headers)
        except ChannelError as exc:
            if exc.code != "TASK_WAIT_TIMEOUT":
                raise
        fake = FakeHarness(root / "fake-native")
        policy = NodePolicy(ledger.source, ledger.instance, frozenset({("tenant-1", "key-1")}), (binding,))
        Runner(NodeClient(ledger_url, NODE_TOKEN), Journal(root / "node"), fake, policy=policy).run_once()
        response = client.request("POST", "/v1/chat/completions", request, extra_headers=headers)
        replay = client.request("POST", "/v1/chat/completions", request, extra_headers=headers)
        assert response == replay and fake.start_count == 1
        print(json.dumps({"stage": "P1a", "all_execution_synthetic": True,
                          "actual_bridge_integrated": False, "actual_sub2api_integrated": False,
                          "native_starts": fake.start_count, "replay_identical": response == replay,
                          "response": response}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
