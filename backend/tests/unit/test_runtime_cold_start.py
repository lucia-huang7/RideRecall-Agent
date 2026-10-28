"""Cold startup gets time to load dependencies without delaying later recovery."""

import importlib.util
from pathlib import Path


def test_backend_startup_grace_keeps_frontend_recovery_and_warm_failure_detection():
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("cold_start_supervisor", root / "scripts/ops/runtime/supervise.py")
    supervisor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(supervisor)
    started = dict(backend=0, frontend=0)
    failed = dict(backend=8, frontend=0, mcp=0)
    assert supervisor.recovery_decision(failed, started, 90, backend_grace=120) == []
    assert supervisor.recovery_decision(failed, started, 121, backend_grace=120) == ["backend"]
    assert supervisor.recovery_decision(failed, started, 40) == ["backend"]
    failed["frontend"] = 8
    assert supervisor.recovery_decision(failed, started, 40, backend_grace=120) == ["frontend"]
