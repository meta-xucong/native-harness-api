"""Real loopback HTTP to explicit TEST fixtures; not actual three-repo integration."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from native_harness_api.adapters import FakeHarness
from native_harness_api.bridge_client import BridgeClient, NodeClient, HTTPTransport, origin
from native_harness_api.contracts import Binding, digest
from native_harness_api.errors import ChannelError
from native_harness_api.facade import Facade, Service
from native_harness_api.node import Journal, NodePolicy, Runner
from tests.fakes.ledger import FakeLedger, SOURCE_TOKEN, NODE_TOKEN
from tests.fakes.gateway import FakeGateway, USER_TOKEN
from tests.fakes.server import running

FACADE_TOKEN = "test-only-facade-credential"


def request(text="hello"):
    return {"model": "synthetic-model", "messages": [{"role": "user", "content": text}]}


class HTTPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.ledger = FakeLedger(self.root / "fixture.sqlite")
        self.ledger_url = self.stack.enter_context(running(self.ledger))
        self.bridge = BridgeClient(self.ledger_url, SOURCE_TOKEN, self.ledger.instance)
        self.binding = Binding("fake-binding", 1, "synthetic-model", "fake-v1")
        self.service = Service(self.ledger.source, self.bridge, {self.binding.model_alias: self.binding}, True)
        self.facade = Facade({FACADE_TOKEN: self.service}, wait_seconds=0, mode="offline_prototype")
        self.facade_url = self.stack.enter_context(running(self.facade))
        self.routes = {"a": {"url": self.facade_url, "token": FACADE_TOKEN,
                              "binding_digest": digest(self.binding.wire())}}
        self.gateway = FakeGateway(self.root / "gateway.sqlite", self.routes, "a")
        self.gateway_url = self.stack.enter_context(running(self.gateway))
        self.http = HTTPTransport(self.gateway_url, USER_TOKEN, timeout=5)
        self.fake = FakeHarness(self.root / "native")
        self.policy = NodePolicy(self.ledger.source, self.ledger.instance,
                                 frozenset({("tenant-1", "key-1"), ("tenant-1", "key-2")}), (self.binding,))
        self.runner = Runner(NodeClient(self.ledger_url, NODE_TOKEN), Journal(self.root / "journal"), self.fake,
                             policy=self.policy)

    def send(self, key="key-1", body=None, **headers):
        return self.http.request("POST", "/v1/chat/completions", request() if body is None else body,
                                 extra_headers={"Idempotency-Key": key, **headers})

    def expect(self, code, callback):
        with self.assertRaises(ChannelError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def queue(self, key="key-1", body=None):
        return self.expect("TASK_WAIT_TIMEOUT", lambda: self.send(key, body))

    def complete(self):
        self.queue()
        self.assertEqual(self.runner.run_once()["outcome"], "succeeded")
        return self.send()

    def test_user_fixture_to_real_http_to_fake_node_and_replay(self):
        value = self.complete()
        self.assertEqual(value["choices"][0]["message"]["content"], "Synthetic FakeHarness result")
        self.assertNotIn("usage", value)
        self.assertEqual(self.send(), value)
        self.assertEqual(self.fake.start_count, 1)
        self.assertEqual(len(self.ledger.all_tasks()), 1)

    def test_fake_gateway_rebuilds_identity_not_client_headers(self):
        self.queue()
        self.runner.run_once()
        self.send(**{"X-Native-Tenant": "attacker", "X-Native-Principal": "attacker", "X-Native-Operation": "attacker"})
        task = self.ledger.all_tasks()[0]
        self.assertEqual(task["identity"]["tenant"], "tenant-1")
        self.assertEqual(task["identity"]["principal"], "key-1")
        self.assertNotEqual(task["identity"]["operation"], "attacker")

    def test_same_key_conflict_and_canonical_order(self):
        self.complete()
        self.expect("IDEMPOTENCY_CONFLICT", lambda: self.send(body=request("changed")))
        reordered = {"messages": request()["messages"], "model": "synthetic-model", "n": 1}
        self.assertEqual(self.send(body=reordered)["id"], self.send()["id"])
        self.assertEqual(self.fake.start_count, 1)

    def test_concurrent_same_key_is_single_task(self):
        # This asserts deduplication, not a 3/5-second disk/network benchmark.
        # Windows CI fsync and connection scheduling under 12 simultaneous
        # callers can exceed the normal fixture deadlines. Keep all requests
        # and strict outcomes, with a bounded stress-test-only budget per hop.
        self.http.timeout = self.bridge.http.timeout = 30
        self.gateway.transport_timeout = 30
        def call(_):
            try:
                self.send()
            except ChannelError as exc:
                return exc.code
            return "unexpected_success"
        with ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(set(pool.map(call, range(100))), {"TASK_WAIT_TIMEOUT"})
        self.assertEqual(len(self.ledger.all_tasks()), 1)
        self.runner.run_once()
        self.assertEqual(self.fake.start_count, 1)

    def test_create_ack_loss_recovers_same_task(self):
        self.ledger.fail_create_response = True
        self.expect("BRIDGE_TRANSPORT_UNCERTAIN", self.send)
        self.queue()
        self.runner.run_once()
        self.send()
        self.assertEqual(len(self.ledger.all_tasks()), 1)
        self.assertEqual(self.fake.start_count, 1)

    def test_result_ack_loss_only_reuploads(self):
        self.queue()
        self.ledger.fail_result_response = True
        self.assertEqual(self.runner.run_once()["code"], "RESULT_UPLOAD_PENDING")
        value = self.send()
        self.runner.run_once()
        self.assertEqual(self.send(), value)
        self.assertEqual(self.fake.start_count, 1)

    def test_offline_draining_revoked_new_permission_does_not_block_replay(self):
        value = self.complete()
        self.ledger.online = False
        self.gateway.allow_new = False
        self.facade.services[FACADE_TOKEN] = replace(self.service, bindings={}, accepting_new=False)
        self.assertEqual(self.send(), value)
        self.expect("EXECUTION_PERMISSION_REVOKED", lambda: self.send("new"))

    def test_route_frozen_after_preadmission_rejection_and_config_mutation(self):
        self.ledger.online = False
        self.expect("NODE_UNAVAILABLE", self.send)
        self.routes["a"]["url"] = "http://127.0.0.1:1"
        self.gateway.selected_route = "new-channel"
        self.ledger.online = True
        self.queue()
        self.runner.run_once()
        self.assertEqual(self.send()["model"], "synthetic-model")
        self.assertEqual(self.fake.start_count, 1)

    def test_frozen_binding_never_silently_changes_after_rejection(self):
        self.ledger.online = False
        self.expect("NODE_UNAVAILABLE", self.send)
        self.ledger.online = True
        changed = Binding("different-binding", 2, "synthetic-model", "fake-v2")
        self.facade.services[FACADE_TOKEN] = replace(self.service, bindings={"synthetic-model": changed})
        self.expect("MODEL_NOT_AVAILABLE", self.send)
        self.assertEqual(self.ledger.all_tasks(), [])

    def test_preadmission_retry_rechecks_permission(self):
        self.ledger.online = False
        self.expect("NODE_UNAVAILABLE", self.send)
        self.ledger.online = True
        self.gateway.allow_new = False
        self.expect("EXECUTION_PERMISSION_REVOKED", self.send)
        self.assertEqual(self.ledger.all_tasks(), [])

    def test_unknown_then_permission_revoked_cannot_create_new(self):
        # The route was bound, but transport did not reach Facade.
        self.routes["a"]["url"] = "http://127.0.0.1:1"
        self.expect("BRIDGE_TRANSPORT_UNCERTAIN", self.send)
        self.gateway.allow_new = False
        self.expect("BRIDGE_TRANSPORT_UNCERTAIN", self.send)
        self.assertEqual(self.ledger.all_tasks(), [])
        self.assertEqual(self.gateway.outbound_attempts, 2)  # one per caller, never failover

    def test_gateway_restart_keeps_route_and_operation(self):
        value = self.complete()
        restarted = FakeGateway(self.root / "gateway.sqlite", {}, "different")
        with running(restarted) as url:
            client = HTTPTransport(url, USER_TOKEN)
            actual = client.request("POST", "/v1/chat/completions", request(), extra_headers={"Idempotency-Key": "key-1"})
        self.assertEqual(actual, value)
        self.assertEqual(self.fake.start_count, 1)

    def test_pending_observation_error_not_terminal_failure(self):
        self.fake.mode = "observation_error"
        self.queue()
        result = self.runner.run_once()
        self.assertEqual(result["outcome"], "pending")
        task = self.ledger.all_tasks()[0]
        self.assertEqual((task["outcome"], task["liveness"]), ("pending", "unknown"))
        self.expect("CAPACITY_EXHAUSTED", lambda: self.send("other"))
        self.runner.run_once()
        self.assertEqual(self.fake.start_count, 1)

    def test_success_unknown_liveness_delivers_but_keeps_slot(self):
        self.fake.mode = "unknown_liveness"
        self.complete()
        self.expect("CAPACITY_EXHAUSTED", lambda: self.send("other"))
        self.assertEqual(self.runner.run_once()["code"], "SLOT_QUARANTINED")

    def test_model_mismatch_and_empty_output_never_success(self):
        self.fake.mode = "model_mismatch"
        self.queue()
        self.runner.run_once()
        self.expect("NATIVE_MODEL_MISMATCH", self.send)
        self.assertEqual(self.fake.start_count, 1)

    def test_expired_result_never_reexecutes(self):
        self.complete()
        self.ledger.expire_result(self.ledger.all_tasks()[0]["remote_task_id"])
        self.expect("RESULT_EXPIRED", self.send)
        self.assertEqual(self.fake.start_count, 1)

    def test_wrong_grant_and_terminal_conflict_rejected(self):
        self.complete()
        task = self.ledger.all_tasks()[0]
        node = NodeClient(self.ledger_url, NODE_TOKEN)
        self.expect("stale_grant", lambda: node.result(task["remote_task_id"], task["claim_id"], "wrong", task["result"]))
        changed = dict(task["result"], output_blocks=[{"type": "text", "text": "different"}])
        self.expect("TERMINAL_CONFLICT", lambda: node.result(task["remote_task_id"], task["claim_id"], task["start_grant_id"], changed))

    def test_existing_bridge_capability_cannot_silently_enable(self):
        class ExistingImageContract:
            def handle(self, *_):
                return 200, {"schema_version": "1", "profiles": ["fake-image"]}, {}
        with running(ExistingImageContract()) as url:
            client = BridgeClient(url, SOURCE_TOKEN, "fixture-ledger")
            self.expect("BRIDGE_HARNESS_EXTENSION_REQUIRED", client.capabilities)

    def test_transport_redirect_not_followed(self):
        class Redirect:
            def handle(self, *_):
                return 302, {}, {"Location": "http://127.0.0.1:1/"}
        with running(Redirect()) as url:
            client = BridgeClient(url, SOURCE_TOKEN, "fixture-ledger")
            self.expect("BRIDGE_REDIRECT_REJECTED", client.capabilities)

    def test_non_loopback_plaintext_or_url_credentials_rejected(self):
        for value in ["http://example.com", "https://user:secret@example.com", "https://example.com/path", "https://example.com?q=x"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                origin(value)

    def test_missing_and_duplicate_authorization_are_unauthorized(self):
        import http.client
        from urllib.parse import urlsplit
        for values in [[], [USER_TOKEN, USER_TOKEN]]:
            parsed = urlsplit(self.gateway_url)
            connection = http.client.HTTPConnection(parsed.hostname, parsed.port)
            connection.putrequest("POST", "/v1/chat/completions")
            for value in values:
                connection.putheader("Authorization", "Bearer " + value)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", "2")
            connection.endheaders(b"{}")
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
            connection.close()
        self.assertEqual(self.ledger.all_tasks(), [])

    def test_models_authenticated_and_synthetic_only(self):
        client = HTTPTransport(self.facade_url, FACADE_TOKEN)
        listing = client.request("GET", "/v1/models")
        self.assertEqual(listing["data"][0]["id"], "synthetic-model")
        self.assertEqual(listing["data"][0]["owned_by"], "p1a-synthetic")
        bad = Binding("real-binding", 1, "real-vendor-model", "vendor-model")
        with self.assertRaises(ValueError):
            Facade({FACADE_TOKEN: replace(self.service, bindings={bad.model_alias: bad})}, mode="offline_prototype")

    def test_different_authenticated_principal_same_key_is_not_replay(self):
        first = self.complete()
        self.gateway.principal = "key-2"
        self.queue()
        self.runner.run_once()
        second = self.send()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.fake.start_count, 2)

    def test_unknown_admission_does_not_bypass_recheck_at_facade(self):
        # Simulate an unknown receipt with no task: failed transport to Facade
        # before create, followed by an administrative revocation.
        from native_harness_api.contracts import canonical, digest
        with self.gateway.connection() as con:
            con.execute("INSERT INTO operations VALUES(?,?,?,?,?)", (
                canonical(["tenant-1", "key-1", "key-1"]),
                digest(dict(request(), stream=False, n=1)), "op-unknown",
                canonical(self.routes["a"]), "unknown"))
        self.gateway.allow_new = False
        self.expect("NEW_ADMISSION_DISABLED", self.send)
        self.assertEqual(self.ledger.all_tasks(), [])

    def test_empty_output_has_explicit_failure_not_placeholder(self):
        self.fake.mode = "empty"
        self.queue()
        self.runner.run_once()
        self.expect("NATIVE_EMPTY_RESULT", self.send)
        self.assertEqual(self.fake.start_count, 1)

    def test_observation_error_has_explicit_uncertain_code(self):
        self.fake.mode = "observation_error"
        self.queue()
        self.runner.run_once()
        self.expect("EXECUTION_UNCERTAIN", self.send)
        self.assertEqual(self.fake.start_count, 1)

    def test_replay_binding_drift_rejected_even_with_matching_output_labels(self):
        self.complete()
        task_id = self.ledger.all_tasks()[0]["remote_task_id"]
        with self.ledger.connection() as con:
            task = self.ledger._get(con, task_id)
            task["request"]["binding"]["native_model"] = "fake-imposter"
            task["result"]["model_evidence"] = {"selected": "fake-imposter", "reported": "fake-imposter"}
            self.ledger._save(con, task)
        self.expect("BRIDGE_BINDING_MISMATCH", self.send)
        self.assertEqual(self.fake.start_count, 1)

    def test_malformed_error_body_is_bounded_failure(self):
        class BadError:
            def handle(self, *_):
                return 502, {"error": []}, {}
        with running(BadError()) as url:
            self.expect("BRIDGE_REQUEST_FAILED", lambda: HTTPTransport(url, SOURCE_TOKEN).request("GET", "/x"))

    def test_truncated_http_response_is_uncertain_not_raw_exception(self):
        from http.client import IncompleteRead
        from unittest.mock import patch
        client = HTTPTransport(self.ledger_url, SOURCE_TOKEN)
        with patch.object(client.opener, "open", side_effect=IncompleteRead(b"partial", 42)):
            self.expect("BRIDGE_TRANSPORT_UNCERTAIN", lambda: client.request("GET", "/x"))

    def test_facade_rejects_mismatched_or_nonsynthetic_map_keys_at_start(self):
        for bindings in [{"vendor-model": self.binding}, {"synthetic-other": self.binding},
                         {None: self.binding}, {"synthetic-model": {}}, {"synthetic-model": None}]:
            with self.subTest(bindings=bindings), self.assertRaises(ValueError):
                Facade({FACADE_TOKEN: replace(self.service, bindings=bindings)}, mode="offline_prototype")
        self.assertEqual(self.ledger.all_tasks(), [])

    def test_facade_revalidates_mutated_binding_map_before_admission(self):
        self.service.bindings["vendor-model"] = self.binding
        client = HTTPTransport(self.facade_url, FACADE_TOKEN)
        self.expect("INVALID_SERVICE_CONFIGURATION", lambda: client.request("GET", "/v1/models"))
        self.expect("INVALID_SERVICE_CONFIGURATION", self.send)
        self.assertEqual(self.ledger.all_tasks(), [])
        self.assertEqual(self.fake.start_count, 0)

    def test_http_node_offer_tampering_rejected_before_claim(self):
        import copy
        self.queue()
        original = self.ledger.all_tasks()[0]
        def identity_tenant(task):
            task["identity"]["tenant"] = "unexpected-tenant"
        def ledger(task):
            task["ledger_instance"] = "unexpected-ledger"
        def message(task):
            task["request"]["messages"][0]["content"] = "changed executable input"
        def model(task):
            task["request"]["binding"]["native_model"] = "fake-other-model"
        def unknown_field(task):
            task["untrusted_execution_option"] = True
        for change in [identity_tenant, ledger, message, model, unknown_field]:
            with self.subTest(change=change.__name__):
                altered = copy.deepcopy(original)
                change(altered)
                with self.ledger.connection() as con:
                    self.ledger._save(con, altered)
                with self.assertRaises(ValueError):
                    self.runner.run_once()
                stored = self.ledger.all_tasks()[0]
                self.assertIsNone(stored["claim_id"])
                self.assertIsNone(stored["start_grant_id"])
                self.assertEqual(self.fake.start_count, 0)
        with self.ledger.connection() as con:
            self.ledger._save(con, original)
        self.assertEqual(self.runner.run_once()["outcome"], "succeeded")

    def test_node_http_binds_requested_offer_id_to_fetched_body(self):
        class WrongOffer:
            def handle(self, method, path, headers, payload):
                if path.endswith("/next"):
                    return 200, {"task_id": "offered-task"}, {}
                return 200, {"remote_task_id": "different-task"}, {}
        with running(WrongOffer()) as url:
            self.expect("BRIDGE_OFFER_ID_MISMATCH", NodeClient(url, NODE_TOKEN).next_task)
        self.assertEqual(self.fake.start_count, 0)

    def test_auth_production_mode_and_unsupported_params(self):
        with self.assertRaises(ValueError):
            Facade({FACADE_TOKEN: self.service})
        wrong = HTTPTransport(self.gateway_url, "not-valid-user-token")
        self.expect("UNAUTHORIZED", lambda: wrong.request("POST", "/v1/chat/completions", request()))
        self.expect("UNSUPPORTED_PARAMETER", lambda: self.send(body=dict(request(), tools=[])))
        self.assertEqual(self.ledger.all_tasks(), [])
        self.assertEqual(self.fake.start_count, 0)
