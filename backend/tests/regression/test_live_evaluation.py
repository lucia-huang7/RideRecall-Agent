"""Live evaluation must not silently substitute deterministic model answers."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def command(output):
    return [
        sys.executable,
        str(ROOT / "scripts/bench/check-live.py"),
        "--output",
        str(output),
        "--limit",
        "1",
        "--max-calls",
        "1",
        "--input-usd-per-million",
        "0",
        "--output-usd-per-million",
        "0",
        "--pricing-date",
        "local-fixture-only",
    ]


def test_missing_live_configuration_fails_before_creating_evidence(tmp_path):
    environment = {k: v for k, v in os.environ.items() if not k.startswith("LLM_AGENT_SYSTEM_RIDE_")}
    output = tmp_path / "missing"
    result = subprocess.run(command(output), env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert "Explicit live mode" in result.stderr
    assert not output.exists()


def test_provider_failure_is_not_scored_as_demo_success(tmp_path):
    class FailedProvider(BaseHTTPRequestHandler):
        calls = 0

        def do_POST(self):
            type(self).calls += 1
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"type":"error","error":{"type":"api_error","message":"Local fixture failure"}}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), FailedProvider)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    environment = dict(
        os.environ,
        LLM_AGENT_SYSTEM_RIDE_MODEL_MODE="live",
        LLM_AGENT_SYSTEM_RIDE_MODEL="local-failure-fixture",
        LLM_AGENT_SYSTEM_RIDE_API_KEY="local-fixture-only",
        LLM_AGENT_SYSTEM_RIDE_BASE_URL=f"http://127.0.0.1:{server.server_port}",
    )
    output = tmp_path / "failed-live"
    try:
        result = subprocess.run(command(output), env=environment, capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert result.returncode != 0
    evidence = json.loads((output / "live-usage.json").read_text())
    assert FailedProvider.calls == evidence["attempted_provider_calls"] == 1
    assert evidence["call_limit_reached"]
    assert evidence["strict_completed"] == 0
    assert evidence["estimated_cost_usd"] is None
    assert not evidence["full_test_coverage"]
    assert evidence["mode"] == "live"
    summary = json.loads((output / "replay/summary.json").read_text())
    assert "live Messages provider" in summary["mode"]
