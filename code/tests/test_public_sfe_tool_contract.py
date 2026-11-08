"""Synthetic compatibility boundary: native agent tools are not API tool_calls."""
import json
from pathlib import Path
import tempfile
import unittest

from native_harness_api.contracts import Binding, digest, normalize_request
from native_harness_api.errors import ChannelError
from native_harness_api.public_sfe_adapter import PublicSfeAdapter, project_result
import test_public_sfe_session as fixtures
import test_public_sfe_adapter as adapter_fixtures


class PublicSfeToolContractTests(unittest.TestCase):
    def test_external_tool_parameters_rejected_not_silently_ignored(self):
        base = {'model': 'wb', 'messages': [{'role': 'user', 'content': 'hello'}]}
        for key, value in [('tools', []), ('tool_choice', 'none'), ('parallel_tool_calls', False),
                           ('functions', []), ('function_call', 'none')]:
            with self.subTest(key=key), self.assertRaises(ChannelError) as raised:
                normalize_request({**base, key: value})
            self.assertEqual(raised.exception.code, 'UNSUPPORTED_PARAMETER')

    def test_tool_messages_and_assistant_tool_calls_rejected(self):
        for message in [{'role': 'tool', 'content': 'result'},
                        {'role': 'function', 'content': 'result'},
                        {'role': 'assistant', 'content': '', 'tool_calls': []}]:
            with self.assertRaises(ChannelError):
                normalize_request({'model': 'wb', 'messages': [message, {'role': 'user', 'content': 'continue'}]})

    def test_tool_input_cannot_reach_native_invocation_or_execution_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            binding = Binding('binding', 1, 'wb', 'synthetic-model')
            adapter = PublicSfeAdapter(Path(directory).resolve(), binding=binding,
                                       invoke=lambda **kw: calls.append(kw))
            request = {'model': 'wb', 'messages': [{'role': 'user', 'content': 'hello'}], 'tools': []}
            task = {'remote_task_id': 'task', 'request_digest': digest(request),
                    'request': {'client_request': request, 'messages': request['messages'], 'binding': binding.wire()}}
            with self.assertRaises(ChannelError):
                adapter.start(task, {'start_grant_id': 'grant'})
            self.assertEqual(calls, [])
            with adapter._connect() as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM evidence').fetchone()[0], 0)

    def test_native_interaction_cancel_never_becomes_external_tool_call_or_success(self):
        for method, expected in [('session/request_permission', {'outcome': {'outcome': 'cancelled'}}),
                                 ('elicitation/create', {'action': 'cancel'})]:
            protocol = fixtures.PublicSfeSessionTests().selected()
            request = fixtures.unpack(protocol.prompt('synthetic'))[0]
            fixtures.response(protocol, request, {'messageId': 'input', '_meta': {'codebuddy.ai/requestId': 'run'}})
            reply = fixtures.unpack(protocol.receive(fixtures.wire({'jsonrpc': '2.0', 'id': 91, 'method': method,
                'params': {'private': 'PRIVATE_PAYLOAD', 'options': [{'kind': 'allow_always'}]}})))
            self.assertEqual(reply[0]['result'], expected)
            fixtures.event(protocol, fixtures.terminal())
            evidence = protocol.evidence()
            self.assertEqual(evidence['outcome'], 'indeterminate')
            self.assertEqual(evidence['failure_code'], 'interaction_refused')
            outer = adapter_fixtures.success()
            self.assertEqual(project_result(outer, 'synthetic-model')['outcome'], 'succeeded')
            outer['session'] = evidence
            result = project_result(outer, 'synthetic-model')
            self.assertEqual(result['outcome'], 'pending')
            self.assertNotIn('tool_calls', json.dumps(result))
            self.assertNotIn('PRIVATE_PAYLOAD', json.dumps(evidence))
