"""Test HTTP lifetime includes request workers, not just the listener."""
from contextlib import contextmanager
import sys
from threading import Thread
from time import monotonic
from native_harness_api.http_server import make_server


@contextmanager
def running(application, *, drain_timeout=5):
    server = make_server(application)
    workers, errors = [], []

    def process_request(request, address):
        worker = Thread(target=server.process_request_thread,
                        args=(request, address), daemon=True)
        workers.append(worker)
        worker.start()

    def handle_error(request, address):
        errors.append(sys.exception())

    server.process_request = process_request
    server.handle_error = handle_error
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        deadline = monotonic() + drain_timeout
        # shutdown stops admission before the worker list is inspected.
        for worker in [thread, *workers]:
            worker.join(timeout=max(0, deadline - monotonic()))
        alive = sum(worker.is_alive() for worker in [thread, *workers])
        if alive:
            raise AssertionError(f"HTTP fixture drainage incomplete: {alive} thread(s) alive")
        if errors:
            raise AssertionError("HTTP fixture request worker failed") from errors[0]
