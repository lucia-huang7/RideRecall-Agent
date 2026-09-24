"""Readiness failures must not trigger restart storms; child logs stay bounded."""

import importlib.util
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

PATH = Path(__file__).resolve().parents[3] / "scripts/ops/runtime/supervise.py"
spec = importlib.util.spec_from_file_location("runtime_supervisor", PATH)
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


def test_database_or_configuration_outage_does_not_restart_a_live_backend():
    failures = dict(backend=0, frontend=0, mcp=0)
    for _ in range(100):
        healthy, alive = supervisor.sample_health(
            {"status": "ok"},
            {
                "status": "not_ready",
                "components": {"database": False, "configuration": False, "mcp": True},
            },
            True,
            failures,
        )
        assert healthy == dict(backend=False, frontend=True, mcp=True)
        assert alive["backend"]
        assert supervisor.recovery_decision(failures, dict(backend=0, frontend=0), 1000) == []


def test_unresponsive_process_and_known_mcp_failure_have_distinct_recovery_thresholds():
    failures = dict(backend=0, frontend=0, mcp=0)
    for _ in range(4):
        supervisor.sample_health({}, {}, True, failures)
    assert supervisor.recovery_decision(failures, dict(backend=0, frontend=0), 20) == []
    assert supervisor.recovery_decision(failures, dict(backend=0, frontend=0), 40) == ["backend"]
    failures = dict(backend=0, frontend=0, mcp=0)
    for _ in range(6):
        supervisor.sample_health(
            {"status": "ok"},
            {"status": "not_ready", "components": {"mcp": False}},
            True,
            failures,
        )
    assert failures["backend"] == 0 and failures["mcp"] == 6
    assert supervisor.recovery_decision(failures, dict(backend=0, frontend=0), 40) == ["backend"]


def test_readiness_json_is_inspected_even_on_http_503(monkeypatch):

    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError(
                "http://127.0.0.1/readyz",
                503,
                "Unavailable",
                {},
                BytesIO(b'{"status":"not_ready","components":{"mcp":false}}'),
            )

    monkeypatch.setattr(supervisor.urllib.request, "build_opener", lambda *args: Opener())
    assert supervisor.read_json("http://127.0.0.1/readyz", allow_error=True)["components"]["mcp"] is False
    with pytest.raises(HTTPError):
        supervisor.read_json("http://127.0.0.1/readyz")


def test_child_log_rotation_bounds_files_and_continues_after_replacement(tmp_path):
    path = tmp_path / "child.log"
    log = supervisor.ChildLog(path, max_bytes=1024, backups=2)
    for marker in ("FIRST", "LATEST"):
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys; print(('x'*100+'\\n')*80); print(sys.argv[1])",
                marker,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log.attach(process)
        assert process.wait(timeout=10) == 0
        for thread in log.threads:
            thread.join(timeout=3)
    log.close()
    files = list(tmp_path.glob("child.log*"))
    assert 1 <= len(files) <= 3 and all((file.stat().st_size <= 1024 for file in files))
    assert "LATEST" in path.read_text()


def test_stopping_an_exited_child_never_signals_a_stale_group(monkeypatch):
    from types import SimpleNamespace

    def forbidden(*args):
        raise AssertionError("Exited child groups must not be signalled")

    monkeypatch.setattr(supervisor.os, "killpg", forbidden)
    supervisor.stop_process(SimpleNamespace(poll=lambda: 1))


def test_group_signal_denial_falls_back_to_owned_child(monkeypatch):
    from types import SimpleNamespace

    sent = []
    waited = []

    def denied(*args):
        raise PermissionError("Group signal denied")

    child = SimpleNamespace(pid=123, poll=lambda: None, send_signal=sent.append, wait=lambda **kw: waited.append(kw))
    monkeypatch.setattr(supervisor.os, "killpg", denied)
    supervisor.stop_process(child)
    assert sent == [supervisor.signal.SIGTERM]
    assert waited == [{"timeout": 8}]
