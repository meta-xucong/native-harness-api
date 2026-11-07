"""Actual socket regressions, not mocked urllib exceptions."""
from contextlib import contextmanager
import socket
import threading
import unittest
from native_harness_api.bridge_client import HTTPTransport
from native_harness_api.errors import ChannelError


@contextmanager
def raw_peer(response):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    def serve():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(5)
                request = b""
                while b"\r\n\r\n" not in request and len(request) < 65536:
                    piece = connection.recv(4096)
                    if not piece:
                        break
                    request += piece
                connection.sendall(response)
                connection.shutdown(socket.SHUT_WR)
        finally:
            listener.close()
    thread = threading.Thread(target=serve, daemon=True)
    port = listener.getsockname()[1]
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        thread.join(6)
        if thread.is_alive():
            raise AssertionError("Raw HTTP peer did not finish")


def packet(headers, body=b'{"ok":true}', status=b"200 OK"):
    return b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\n" + headers + b"Connection: close\r\n\r\n" + body


class TransportFramingTests(unittest.TestCase):
    def call(self, response):
        with raw_peer(response) as url:
            return HTTPTransport(url, "test-only-framing-token", timeout=2).request("GET", "/test")

    def test_complete_json_but_longer_declared_length_is_uncertain(self):
        body = b'{"ok":true}'
        response = packet(f"Content-Length: {len(body)+100}\r\n".encode(), body)
        with self.assertRaises(ChannelError) as caught:
            self.call(response)
        self.assertEqual(caught.exception.code, "BRIDGE_TRANSPORT_UNCERTAIN")
        self.assertIsNone(caught.exception.accepted)

    def test_truncated_error_body_cannot_claim_not_accepted(self):
        body = b'{"error":{"code":"NODE_UNAVAILABLE"}}'
        response = packet(f"Content-Length: {len(body)+100}\r\nX-Native-Accepted: false\r\n".encode(), body, b"503 Unavailable")
        with self.assertRaises(ChannelError) as caught:
            self.call(response)
        self.assertEqual(caught.exception.code, "BRIDGE_TRANSPORT_UNCERTAIN")
        self.assertIsNone(caught.exception.accepted)

    def test_exact_length_socket_response_succeeds(self):
        self.assertEqual(self.call(packet(b"Content-Length: 11\r\n")), {"ok": True})

    def test_duplicate_or_ambiguous_length_fails_closed(self):
        for headers in [b"Content-Length: 11\r\nContent-Length: 11\r\n", b"Content-Length: 11,11\r\n",
                        b"Content-Length: -1\r\n", b"Content-Length: 11\r\nTransfer-Encoding: chunked\r\n", b""]:
            with self.subTest(headers=headers), self.assertRaises(ChannelError) as caught:
                self.call(packet(headers))
            self.assertEqual(caught.exception.code, "BRIDGE_INVALID_FRAMING")

    def test_chunked_truncated_responses_are_not_supported(self):
        for body in [b'b\r\n{"ok":true}\r\n', b'b\r\n{"ok":true}\r\n0\r\n']:
            with self.subTest(body=body):
                with self.assertRaises(ChannelError) as caught:
                    self.call(packet(b"Transfer-Encoding: chunked\r\n", body))
                self.assertEqual(caught.exception.code, "BRIDGE_INVALID_FRAMING")

    def test_complete_chunked_socket_response_also_rejected_explicitly(self):
        with self.assertRaises(ChannelError) as caught:
            self.call(packet(b"Transfer-Encoding: chunked\r\n", b'b\r\n{"ok":true}\r\n0\r\n\r\n'))
        self.assertEqual(caught.exception.code, "BRIDGE_INVALID_FRAMING")
