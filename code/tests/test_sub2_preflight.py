from copy import deepcopy
import threading
import unittest

from native_harness_api.errors import ChannelError
from native_harness_api.http_server import bearer, make_server
from native_harness_api.sub2_preflight import PreflightError, check


RPM_PATH = "/api/v1/admin/users/100/rpm-status"
ADMIN = "fixture-sub2-admin-token"
USER = "fixture-sub2-api-key"


class Sub2Fixture:
    def __init__(self):
        self.requests = []
        self.rpm = {"user_rpm_used": 0, "user_rpm_limit": 1, "per_group": [
            {"group_id": 200, "used": 0, "limit": 1, "source": "group"}]}
        self.models = ["synthetic-fixture"]
        self.after_catalog = lambda: None

    def handle(self, method, path, headers, body):
        self.requests.append((method, path))
        if method == "GET" and path == RPM_PATH and bearer(headers) == ADMIN:
            return 200, {"code": 0, "data": deepcopy(self.rpm)}, {}
        if method == "GET" and path == "/v1/models" and bearer(headers) == USER:
            self.after_catalog()
            return 200, {"data": [{"id": model} for model in self.models]}, {}
        raise ChannelError("fixture_rejected", 401)


class Sub2PreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Sub2Fixture()
        server = make_server(self.fixture)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}"

    def check(self, key=USER):
        return check(self.url, key, ADMIN, user_id=100, group_id=200, model="synthetic-fixture")

    def test_preflight_only_gets_and_leaves_a_one_request_rpm_budget(self):
        result = self.check()
        self.assertTrue(result["model_visible"])
        self.assertEqual(result["rpm_before"], {"user_used": 0, "user_limit": 1, "group_used": 0, "group_limit": 1})
        self.assertEqual(result["rpm_before"], result["rpm_after"])
        self.assertEqual(self.fixture.requests, [("GET", RPM_PATH), ("GET", "/v1/models"), ("GET", RPM_PATH)])

    def test_exhausted_user_is_detected_without_accessing_the_catalog(self):
        self.fixture.rpm["user_rpm_used"] = 1
        with self.assertRaisesRegex(PreflightError, "^SUB2_RPM_EXHAUSTED$"):
            self.check()
        self.assertEqual(self.fixture.requests, [("GET", RPM_PATH)])

    def test_group_capacity_is_checked_again_after_catalog(self):
        def consume_group_budget():
            self.fixture.rpm["per_group"][0]["used"] = 1
        self.fixture.after_catalog = consume_group_budget
        with self.assertRaisesRegex(PreflightError, "^SUB2_RPM_EXHAUSTED$"):
            self.check()
        self.assertEqual(self.fixture.requests[-1], ("GET", RPM_PATH))

    def test_zero_override_disables_group_limit_only(self):
        self.fixture.rpm["per_group"][0].update(source="override", used=6, limit=0)
        self.assertTrue(self.check()["model_visible"])
        self.fixture.rpm["user_rpm_used"] = 1
        with self.assertRaisesRegex(PreflightError, "^SUB2_RPM_EXHAUSTED$"):
            self.check()

    def test_catalog_requires_the_test_key_and_expected_alias(self):
        with self.assertRaisesRegex(PreflightError, "SUB2_MODELS_READ_FAILED \\(HTTP 401\\)"):
            self.check(key="fixture-invalid-api-key")
        self.fixture.models = ["synthetic-other"]
        with self.assertRaisesRegex(PreflightError, "^SUB2_MODEL_NOT_VISIBLE$"):
            self.check()

    def test_unverified_group_fails_closed(self):
        self.fixture.rpm["per_group"][0]["source"] = ""
        with self.assertRaisesRegex(PreflightError, "^SUB2_RPM_GROUP_UNVERIFIED$"):
            self.check()

    def test_remote_origin_is_rejected(self):
        with self.assertRaisesRegex(PreflightError, "^SUB2_LOOPBACK_OR_CREDENTIAL_INVALID$"):
            check("https://example.invalid", USER, ADMIN, user_id=100, group_id=200, model="synthetic-fixture")
