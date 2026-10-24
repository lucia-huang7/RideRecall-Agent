"""Loopback-only static frontend with a same-origin API proxy."""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "backend"))
from ride_agent.agent.http_boundary import loopback_authority, loopback_origin

MAX_BODY_BYTES = 4 * 1024 * 1024


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, max_handlers=16, request_timeout=15, **kwargs):
        if type(max_handlers) is not int or not 1 <= max_handlers <= 64 or (not 0 < request_timeout <= 30):
            raise ValueError("Invalid local proxy capacity or timeout")
        self.slots = threading.BoundedSemaphore(max_handlers)
        self.request_timeout = request_timeout
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                body = b'{"detail":"The local workspace is busy. Retry shortly."}'
                request.sendall(
                    (
                        ("HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: ")
                        + str(len(body))
                        + ("\r\nRetry-After: 2\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n")
                    ).encode()
                    + body
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, request, address):
        error = sys.exc_info()[1]
        sys.stderr.write(json.dumps(dict(event="frontend_handler_error", error_type=type(error).__name__)) + "\n")


HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
}


class Frontend(SimpleHTTPRequestHandler):
    backend_origin = "http://127.0.0.1:8010"

    def setup(self):
        super().setup()
        self.connection.settimeout(getattr(self.server, "request_timeout", 15))

    def log_error(self, format, *args):
        sys.stderr.write(json.dumps(dict(event="frontend_protocol_error")) + "\n")

    def parse_request(self):
        if not super().parse_request():
            return False
        if (
            len(self.headers.get_all("Host", [])) != 1
            or len(self.headers.get_all("Origin", [])) > 1
            or (not loopback_authority(self.headers.get("Host", "")))
            or (not loopback_origin(self.headers.get("Origin")))
        ):
            self.send_error(403, "Demo requests require a loopback host and origin.")
            return False
        return True

    def log_request(self, code="-", size="-"):
        import sys

        method = self.command if self.command in ("GET", "POST", "HEAD") else "OTHER"
        sys.stderr.write(
            json.dumps(
                dict(
                    event="frontend_request",
                    method=method,
                    status=str(code),
                    route="api_proxy" if self.path.startswith("/ride-api/") else "static",
                )
            )
            + "\n"
        )

    def do_GET(self):
        if self.path == "/__llmagentsystem_health":
            body = json.dumps(
                {
                    "service": "llmagentsystem-frontend",
                    "instance": os.environ["LLM_AGENT_SYSTEM_INSTANCE_ID"],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/ride-api/"):
            self.proxy()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/ride-api/"):
            self.proxy()
        else:
            self.send_error(405, "Only API requests accept POST")

    def reject_proxy(self, status, detail):
        data = json.dumps(dict(detail=detail)).encode()
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def proxy(self):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding", []):
            self.reject_proxy(501, "Chunked uploads are not supported by the local proxy")
            return
        if len(lengths) > 1 or (lengths and (not lengths[0].isascii() or not lengths[0].isdigit())):
            self.reject_proxy(400, "Invalid content length")
            return
        if lengths and (len(lengths[0]) > 12 or int(lengths[0]) > MAX_BODY_BYTES):
            self.reject_proxy(413, "Request is too large")
            return
        length = int(lengths[0]) if lengths else 0
        chunks = []
        remaining = length
        deadline = time.monotonic() + getattr(self.server, "request_timeout", 15)
        try:
            while remaining:
                budget = deadline - time.monotonic()
                if budget <= 0:
                    raise TimeoutError
                self.connection.settimeout(budget)
                chunk = self.rfile.read1(min(65536, remaining))
                if not chunk:
                    self.reject_proxy(400, "Content length does not match the request body")
                    return
                chunks.append(chunk)
                remaining -= len(chunk)
        except TimeoutError:
            self.reject_proxy(408, "Request body took too long to arrive")
            return
        finally:
            self.connection.settimeout(getattr(self.server, "request_timeout", 15))
        body = b"".join(chunks) if length else None
        del chunks
        headers = {
            k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS and k.lower() != "content-length"
        }
        request = urllib.request.Request(
            self.backend_origin + self.path[len("/ride-api") :],
            data=body,
            headers=headers,
            method=self.command,
        )
        sent = False
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                upstream = opener.open(request, timeout=100)
            except urllib.error.HTTPError as error:
                upstream = error
            with upstream:
                self.send_response(upstream.status)
                for key, value in upstream.headers.items():
                    if key.lower() not in HOP_HEADERS and key.lower() not in (
                        "content-length",
                        "cache-control",
                    ):
                        self.send_header(key, value)
                length_header = upstream.headers.get("Content-Length")
                if length_header and length_header.isascii() and length_header.isdigit():
                    self.send_header("Content-Length", length_header)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                sent = True
                while chunk := upstream.read(65536):
                    self.wfile.write(chunk)
        except (OSError, urllib.error.URLError):
            if not sent:
                data = b'{"detail":"The backend is restarting. Please retry in a few seconds."}'
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
        finally:
            self.close_connection = True


if __name__ == "__main__":
    directory = Path(__file__).resolve().parents[1] / "app" / "frontend" / "dist"
    server = BoundedHTTPServer(("127.0.0.1", 5175), partial(Frontend, directory=str(directory)))
    server.daemon_threads = True
    server.serve_forever()
