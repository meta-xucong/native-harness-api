"""Deterministic fixture lifetime tests; no native inference."""
import socket
from threading import Event, Thread
import unittest
from urllib.parse import urlsplit

from native_harness_api.errors import ChannelError
from tests.fakes.server import running


class BlockingApplication:
    def __init__(self):
        self.entered, self.release, self.finished = Event(), Event(), Event()

    def handle(self, *_):
        self.entered.set()
        try:
            if not self.release.wait(5):
                raise AssertionError("test did not release handler")
            return 200, {}, {}
        finally:
            self.finished.set()


def abandon_request(url):
    # Client has departed while the application still owns its resources.
    with socket.create_connection(("127.0.0.1", urlsplit(url).port), timeout=2) as client:
        client.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")


class FakeServerTests(unittest.TestCase):
    def test_context_waits_for_abandoned_request_worker(self):
        app = BlockingApplication()
        context = running(app)
        url = context.__enter__()
        abandon_request(url)
        self.assertTrue(app.entered.wait(2))
        closing, closed = Event(), Event()
        failures = []

        def close():
            closing.set()
            try:
                context.__exit__(None, None, None)
            except BaseException as exc:
                failures.append(exc)
            finally:
                closed.set()

        closer = Thread(target=close)
        closer.start()
        try:
            self.assertTrue(closing.wait(2))
            self.assertFalse(closed.wait(0.1), "context returned with worker alive")
        finally:
            app.release.set()
            closer.join(3)
        self.assertTrue(app.finished.is_set())
        self.assertTrue(closed.is_set())
        self.assertEqual(failures, [])

    def test_drain_deadline_fails_instead_of_silent_cleanup(self):
        app = BlockingApplication()
        try:
            with self.assertRaisesRegex(AssertionError, "drainage incomplete"):
                with running(app, drain_timeout=0.01) as url:
                    abandon_request(url)
                    self.assertTrue(app.entered.wait(2))
        finally:
            app.release.set()
            self.assertTrue(app.finished.wait(2))

    def test_uncaught_worker_error_is_reported(self):
        class InvalidResponse:
            def handle(self, *_):
                return 200, {"not_json": object()}, {}

        with self.assertRaisesRegex(AssertionError, "request worker failed") as caught:
            with running(InvalidResponse()) as url:
                with socket.create_connection(("127.0.0.1", urlsplit(url).port), timeout=2) as client:
                    client.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
                    self.assertEqual(client.recv(1), b"")
        self.assertIsInstance(caught.exception.__cause__, ChannelError)
        self.assertEqual(caught.exception.__cause__.code, "INVALID_JSON")
