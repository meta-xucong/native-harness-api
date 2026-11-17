"""Bounded loopback-only HTTP adapter for P1a. Not a production web server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass
import time
from .contracts import MAX_REQUEST_BYTES, canonical, parse_json
from .errors import ChannelError


@dataclass
class EventStream:
    frames: object
    deadline: float


def single_header(headers, name, *, required=True):
    values = headers.get_all(name, [])
    if len(values) != 1 or not values[0]:
        if not values and not required:
            return None
        raise ChannelError("INVALID_HEADER", 400)
    return values[0]


def bearer(headers):
    values = headers.get_all("Authorization", [])
    if len(values) != 1:
        raise ChannelError("UNAUTHORIZED", 401)
    value = values[0]
    if (not value.startswith("Bearer ") or not value[7:] or " " in value[7:]
            or any(ord(c) < 33 or ord(c) > 126 for c in value[7:])):
        raise ChannelError("UNAUTHORIZED", 401)
    return value[7:]


def make_server(application, host="127.0.0.1", port=0):
    if host != "127.0.0.1":
        raise ValueError("P1a server binds IPv4 loopback only")
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.handle_request()
        def do_POST(self):
            self.handle_request()
        def log_message(self, *_):
            pass  # No prompt, authorization or request URL logging.
        def handle_request(self):
            self.connection.settimeout(5)
            try:
                if self.headers.get_all("Transfer-Encoding"):
                    raise ChannelError("TRANSFER_ENCODING_NOT_SUPPORTED", 400)
                if "?" in self.path or "#" in self.path:
                    raise ChannelError("INVALID_PATH", 400)
                payload = None
                if self.command == "POST":
                    length = single_header(self.headers, "Content-Length")
                    if not length.isdecimal() or not 0 < int(length) <= getattr(application, "max_body_bytes", MAX_REQUEST_BYTES):
                        raise ChannelError("BODY_TOO_LARGE", 413)
                    if single_header(self.headers, "Content-Type").split(";", 1)[0] != "application/json":
                        raise ChannelError("UNSUPPORTED_MEDIA_TYPE", 415)
                    raw = self.rfile.read(int(length))
                    if len(raw) != int(length):
                        raise ChannelError("INCOMPLETE_BODY", 400)
                    payload = parse_json(raw, getattr(application, "max_body_bytes", MAX_REQUEST_BYTES))
                status, body, headers = application.handle(self.command, self.path, self.headers, payload)
            except ChannelError as exc:
                status, body, headers = exc.status, exc.body(), {}
                if exc.accepted is not None:
                    headers["X-Native-Accepted"] = str(exc.accepted).lower()
            except (TimeoutError, OSError):
                status, body, headers = 408, ChannelError("REQUEST_TIMEOUT", 408).body(), {}
            except Exception:
                status, body, headers = 500, ChannelError("INTERNAL_ERROR", 500).body(), {}
            if isinstance(body, EventStream):
                frames = iter(body.frames)
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Accel-Buffering", "no")
                    self.send_header("Connection", "close")
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.end_headers()
                    for frame in frames:
                        remaining = body.deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self.connection.settimeout(min(5, remaining))
                        self.wfile.write(frame)
                        self.wfile.flush()
                except OSError:
                    pass  # Only the observer departed, never cancel/re-submit.
                finally:
                    frames.close()
                    self.close_connection = True
                return
            encoded = canonical(body).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(encoded)
            except OSError:
                pass  # Client departure never mutates a task or cancels native work.
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
