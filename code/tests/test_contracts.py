import copy
import unittest
from native_harness_api.contracts import *
from native_harness_api.errors import ChannelError


def request():
    return {"model": "synthetic-model", "messages": [{"role": "user", "content": "hello"}]}


def task_result():
    return {"remote_task_id": "task-1", "created": 1, "request": {"binding": {"model_alias": "synthetic-model", "native_model": "fake-v1"}},
            "result": {"outcome": "succeeded", "liveness": "stopped_confirmed", "native_completion": {"confirmed": True},
                       "model_evidence": {"selected": "fake-v1", "reported": "fake-v1"},
                       "usage": {"state": "unknown"}, "output_blocks": [{"type": "text", "text": "real synthetic output"}]}}


class ContractsTests(unittest.TestCase):
    def test_normalization_and_untrusted_user(self):
        value = request()
        original = normalize_request(value)
        value.update(user="not-a-tenant", stream=False, n=1)
        self.assertEqual(digest(original), digest(normalize_request(value)))
        self.assertEqual(digest(original), digest(dict(reversed(list(original.items())))))

    def test_reject_unsupported_before_execution(self):
        for patch in [{"stream": True}, {"stream": 1}, {"n": True}, {"n": 1.0}, {"tools": []},
                      {"temperature": 0}, {"messages": []}, {"model": "../../escape"},
                      {"messages": [{"role": "system", "content": "x"}]},
                      {"messages": [{"role": [], "content": "x"}]},
                      {"messages": [{"role": "user", "content": []}]}]:
            with self.subTest(patch=patch), self.assertRaises(ChannelError):
                normalize_request(dict(request(), **patch))

    def test_json_reject_duplicate_nonfinite_surrogate_and_size(self):
        for raw in [b'{"a":1,"a":2}', b'{"a":NaN}', b'"\\ud800"', b'\xff']:
            with self.subTest(raw=raw), self.assertRaises(ChannelError):
                parse_json(raw)
        with self.assertRaises(ChannelError):
            parse_json(b' ' * (MAX_REQUEST_BYTES + 1))

    def test_scope_isolation(self):
        a = Identity("source", "tenant", "principal", "operation")
        b = Identity("source", "other", "principal", "operation")
        self.assertNotEqual(a.origin_key, b.origin_key)

    def test_usage_unknown_omitted(self):
        value = completion(task_result())
        self.assertNotIn("usage", value)
        self.assertEqual(value["model"], "synthetic-model")

    def test_refusal_not_failure(self):
        task = task_result()
        task["result"]["output_blocks"] = [{"type": "refusal", "text": "I cannot do that."}]
        value = completion(task)
        self.assertIsNone(value["choices"][0]["message"]["content"])
        self.assertEqual(value["choices"][0]["message"]["refusal"], "I cannot do that.")

    def test_output_truthfulness(self):
        patches = [{"native_completion": {"confirmed": 1}}, {"native_completion": {"confirmed": False}},
                   {"output_blocks": []}, {"output_blocks": [{"type": "tool_call", "text": "already executed"}]},
                   {"output_blocks": [{"type": [], "text": "x"}]}, {"usage": {"state": "unknown", "total_tokens": 0}},
                   {"model_evidence": {"selected": "wrong", "reported": "wrong"}}]
        for patch in patches:
            task = task_result()
            task["result"].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ChannelError):
                completion(task)

    def test_result_ttl_never_synthesizes(self):
        task = task_result()
        task["result_expired"] = True
        with self.assertRaises(ChannelError) as caught:
            completion(task)
        self.assertEqual(caught.exception.status, 410)
