"""Text budget is not a JSON envelope budget; exercise actual bounded HTTP IO."""
import threading
import unittest
from native_harness_api.bridge_client import HTTPTransport
from native_harness_api.contracts import MAX_REQUEST_BYTES, MAX_RESULT_BYTES, MAX_RESULT_WIRE_BYTES, canonical, completion
from native_harness_api.errors import ChannelError
from native_harness_api.http_server import make_server
from test_contracts import task_result


class ResponseWireBudgetTests(unittest.TestCase):
    def test_maximum_text_crosses_facade_http_and_bridge_reader(self):
        for text in ('x' * MAX_RESULT_BYTES, '\x01' * MAX_RESULT_BYTES,
                     '\\"' * (MAX_RESULT_BYTES // 2), '界' * (MAX_RESULT_BYTES // 3) + 'x'):
            task = task_result()
            task['result']['output_blocks'] = [{'type': 'text', 'text': text}]
            expected = completion(task)
            task['request']['client_request'] = {'messages': [{'role': 'user', 'content': 'x' * (MAX_REQUEST_BYTES - 1024)}]}
            self.assertLessEqual(len(canonical(expected).encode()), MAX_RESULT_WIRE_BYTES)
            self.assertGreater(len(canonical(expected).encode()), MAX_RESULT_BYTES)
            class Application:
                def handle(self, method, path, *_):
                    return 200, task if path == '/task' else expected, {}
            server = make_server(Application())
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                transport = HTTPTransport('http://127.0.0.1:' + str(server.server_address[1]), 'test-token-12345678')
                self.assertEqual(transport.request('GET', '/result'), expected)
                self.assertEqual(transport.request('GET', '/task'), task)
                # Request framing remains exactly 64 KiB even though responses grew.
                with self.assertRaises(ChannelError):
                    transport.request('POST', '/result', {'text': 'x' * MAX_REQUEST_BYTES})
            finally:
                server.shutdown(); server.server_close(); thread.join(5)

    def test_decoded_aggregate_text_limit_is_not_relaxed(self):
        for blocks in ([{'type': 'text', 'text': 'x' * (MAX_RESULT_BYTES + 1)}],
                       [{'type': 'text', 'text': 'x' * MAX_RESULT_BYTES}, {'type': 'refusal', 'text': 'r'}],
                       [{'type': 'text', 'text': '界' * (MAX_RESULT_BYTES // 3 + 1)}]):
            task = task_result(); task['result']['output_blocks'] = blocks
            with self.assertRaises(ChannelError) as error: completion(task)
            self.assertEqual(error.exception.code, 'RESULT_TOO_LARGE')

    def test_bounded_bridge_response_cannot_be_configured_unbounded(self):
        with self.assertRaises(ValueError):
            HTTPTransport('http://127.0.0.1', 'test-token-12345678',
                          max_response=MAX_RESULT_WIRE_BYTES + 128 * 1024 + 1)
