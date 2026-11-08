"""Offline durable event transport tests, not real native execution evidence."""
import copy
from email.message import Message
import unittest
from native_harness_api.contracts import Identity, Binding, envelope, normalize_request, digest
from native_harness_api.event_contract import project_events, parse_cursor
from native_harness_api.facade import Facade, Service
from native_harness_api.errors import ChannelError
from tests.test_contracts import task_result, request


class EventTests(unittest.TestCase):
    def setUp(self):
        self.identity = Identity("source", "tenant", "principal", "operation")
        self.binding = Binding("binding", 1, "synthetic-model", "fake-v1")
        self.req = normalize_request(request())
        self.task = {**envelope(self.identity, self.req, self.binding, "ledger"),
                     "remote_task_id": "task-1", "created": 1}
        self.bridge = type("Bridge", (), {})()
        self.bridge.ledger_instance = "ledger"
        self.bridge.task = lambda _: self.task
        self.page = {"schema_version": "native-events-v1", "remote_task_id": "task-1", "events": [],
                     "next_cursor": 0, "has_more": False, "terminal_seq": None}

    def project(self, page=None, cursor=0):
        return project_events(self.page if page is None else page, self.task, self.identity,
                              digest(self.req), digest(self.binding.wire()), self.bridge, cursor)

    def test_snapshot_replace_and_idle_are_not_completion(self):
        self.page.update(events=[
            {"seq": 1, "type": "text_snapshot", "data": {"text": "long draft"}},
            {"seq": 2, "type": "text_snapshot", "data": {"text": "revision"}},
            {"seq": 3, "type": "native_status", "data": {"state": "idle", "stop_reason": "end_turn"}},
        ], next_cursor=3)
        self.assertEqual(self.project(), self.page)
        self.assertIsNone(self.project()["terminal_seq"])

    def test_terminal_rechecks_identity_and_authoritative_result(self):
        self.task["result"] = task_result()["result"]
        self.page.update(events=[{"seq": 1, "type": "task_terminal",
                                 "data": {"outcome": "succeeded", "liveness": "stopped_confirmed"}}],
                         next_cursor=1, terminal_seq=1)
        result = self.project()["events"][0]
        self.assertEqual(result["type"], "completion")
        self.assertNotIn("usage", result["data"])
        bad = copy.deepcopy(self.task)
        bad["identity"]["tenant"] = "attacker"
        self.bridge.task = lambda _: bad
        with self.assertRaises(ChannelError):
            self.project()

    def test_terminal_without_result_is_not_success_or_task_failure(self):
        self.page.update(events=[{"seq": 1, "type": "task_terminal",
                                 "data": {"outcome": "succeeded", "liveness": "stopped_confirmed"}}],
                         next_cursor=1, terminal_seq=1)
        with self.assertRaises(ChannelError):
            self.project()

    def test_invalid_gap_cursor_type_private_data_and_false_terminal(self):
        event = {"seq": 1, "type": "text_snapshot", "data": {"text": "x"}}
        good = {**self.page, "events": [event], "next_cursor": 1}
        for patch in [{"next_cursor": True}, {"terminal_seq": 1}, {"terminal_seq": 2}, {"next_cursor": 2},
                      {"events": [{**event, "seq": 2}]},
                      {"events": [{**event, "data": {"text": "x", "private": "secret"}}]}]:
            with self.subTest(patch=patch), self.assertRaises(ChannelError):
                self.project({**good, **patch})
        for cursor in ["", "01", "-1", "1.0", "9007199254740992", "1\n"]:
            with self.assertRaises(ChannelError):
                parse_cursor(cursor)

    def test_error_status_and_maximum_completion_separate_pages(self):
        self.page.update(events=[{"seq": 1, "type": "native_status",
                                 "data": {"state": "idle", "stop_reason": "_codebuddy.ai/error"}}],
                         next_cursor=1)
        self.assertEqual(self.project()["events"][0]["data"]["stop_reason"], "_codebuddy.ai/error")
        self.page.update(events=[{"seq": 1, "type": "text_snapshot", "data": {"text": "\x01" * 262144}}],
                         next_cursor=1, terminal_seq=2, has_more=True)
        self.assertEqual(self.project()["next_cursor"], 1)
        self.task["result"] = task_result()["result"]
        self.task["result"]["output_blocks"][0]["text"] = "\x01" * 262144
        self.page.update(events=[{"seq": 2, "type": "task_terminal",
                                 "data": {"outcome": "succeeded", "liveness": "stopped_confirmed"}}],
                         next_cursor=2, has_more=False)
        self.assertEqual(self.project(cursor=1)["events"][0]["type"], "completion")

    def test_facade_first_admission_then_readonly_resume(self):
        calls = []
        current = [None]
        self.bridge.lookup = lambda _: current[0]
        def submit(payload):
            calls.append(payload)
            current[0] = self.task
            return self.task
        self.bridge.submit = submit
        self.bridge.events = lambda task, cursor: self.page
        facade = Facade({"test-only-service-credential": Service("source", self.bridge,
                        {self.binding.model_alias: self.binding}, True)}, mode="p1b_integration")
        headers = Message()
        for key, value in {"Authorization": "Bearer test-only-service-credential", "X-Native-Source": "source",
                           "X-Native-Ledger": "ledger", "X-Native-Tenant": "tenant",
                           "X-Native-Principal": "principal", "X-Native-Operation": "operation",
                           "X-Native-Binding-Digest": digest(self.binding.wire()),
                           "X-Native-Admission-Allowed": "true", "X-Native-Event-Cursor": "0"}.items():
            headers[key] = value
        status, page, _ = facade.handle("POST", "/v1/native-harness/events", headers, request())
        self.assertEqual((status, page), (200, self.page))
        self.assertEqual(len(calls), 1)
        headers.replace_header("X-Native-Admission-Allowed", "false")
        self.assertEqual(facade.handle("POST", "/v1/native-harness/events", headers, request())[0], 200)
        current[0] = None
        with self.assertRaises(ChannelError) as exc:
            facade.handle("POST", "/v1/native-harness/events", headers, request())
        self.assertEqual(exc.exception.code, "NEW_ADMISSION_DISABLED")
        self.assertEqual(len(calls), 1)
