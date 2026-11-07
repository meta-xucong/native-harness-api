from contextlib import contextmanager
from threading import Thread
from native_harness_api.http_server import make_server

@contextmanager
def running(application):
    server = make_server(application)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
