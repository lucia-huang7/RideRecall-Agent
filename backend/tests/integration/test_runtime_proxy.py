"""Exercise real proxy sockets, bounded upload time, framing and binary responses."""

import http.client
import importlib.util
import json
import socket
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def proxy(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "bounded_frontend",
        Path(__file__).resolve().parents[3] / "scripts/ops/runtime/frontend.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    binary = b"%PDF-" + bytes(range(256)) * 8192

    class Backend(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("ETag", '"verified-source"')
            self.send_header("Content-Length", str(len(binary)))
            self.end_headers()
            for start in range(0, len(binary), 8192):
                self.wfile.write(binary[start : start + 8192])

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    upstream.daemon_threads = True
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    module.Frontend.backend_origin = f"http://127.0.0.1:{upstream.server_port}"
    (tmp_path / "index.html").write_text("Local workspace")
    server = module.BoundedHTTPServer(
        ("127.0.0.1", 0),
        partial(module.Frontend, directory=str(tmp_path)),
        max_handlers=2,
        request_timeout=1,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (server, calls, binary)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=3)


def connect(server, path="/ride-api/test", headers=(), body=b""):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=4)
    connection.putrequest("POST", path)
    for key, value in headers:
        connection.putheader(key, value)
    connection.endheaders(body)
    return connection


def test_proxy_rejects_invalid_framing_before_forwarding_and_preserves_binary_stream(
    proxy,
):
    server, calls, binary = proxy
    for headers, status in [
        ([("Content-Length", "invalid")], 400),
        ([("Content-Length", "-1")], 400),
        ([("Content-Length", "4194305")], 413),
        ([("Content-Length", "0"), ("Content-Length", "0")], 400),
        ([("Transfer-Encoding", "chunked")], 501),
    ]:
        connection = connect(server, headers=headers)
        try:
            response = connection.getresponse()
            assert response.status == status
            assert isinstance(json.loads(response.read())["detail"], str)
        finally:
            connection.close()
    connection = connect(server, headers=[("Content-Length", "10")], body=b"short")
    try:
        connection.sock.shutdown(socket.SHUT_WR)
        response = connection.getresponse()
        assert response.status == 400
        response.read()
    finally:
        connection.close()
    assert calls == []
    connection = connect(server, headers=[("Content-Length", "3")], body=b"abc")
    try:
        response = connection.getresponse()
        assert response.status == 200 and response.getheader("ETag") == '"verified-source"'
        assert response.getheader("Content-Length") == str(len(binary))
        assert response.getheader("Content-Type") == "application/pdf"
        assert response.getheader("Cache-Control") == "no-store"
        assert response.read() == binary
    finally:
        connection.close()
    assert calls == [b"abc"]


def test_proxy_limits_handlers_and_enforces_total_body_deadline_even_with_progress(
    proxy,
):
    server, calls, _ = proxy
    held = [connect(server, headers=[("Content-Length", "100")], body=b"x") for _ in range(2)]
    try:
        excess = connect(server, headers=[("Content-Length", "0")])
        try:
            response = excess.getresponse()
            assert response.status == 503 and response.getheader("Retry-After") == "2"
            assert "busy" in json.loads(response.read())["detail"]
        finally:
            excess.close()
        for connection in held:
            response = connection.getresponse()
            assert response.status == 408
            response.read()
    finally:
        for connection in held:
            connection.close()
    deadline = time.monotonic() + 2
    while True:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request("GET", "/")
        response = connection.getresponse()
        response.read()
        status = response.status
        connection.close()
        if status == 200:
            break
        assert time.monotonic() < deadline
        time.sleep(0.02)
    began = time.monotonic()
    connection = connect(server, headers=[("Content-Length", "100")], body=b"x")
    wire = connection.sock
    stop = threading.Event()

    def drip():
        while not stop.wait(0.1):
            try:
                wire.sendall(b"x")
            except OSError:
                return

    writer = threading.Thread(target=drip, daemon=True)
    writer.start()
    try:
        response = connection.getresponse()
        assert response.status == 408
        response.read()
        assert time.monotonic() - began < 2.5
    finally:
        stop.set()
        writer.join(timeout=1)
        connection.close()
    assert calls == []
