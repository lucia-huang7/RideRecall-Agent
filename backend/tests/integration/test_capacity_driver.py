"""The owned workload must report startup/audit failure without orphaning a child."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_capacity_driver_preserves_failure_report_when_startup_leaves_an_empty_database(
    tmp_path,
):
    root = Path(__file__).resolve().parents[3]
    source = tmp_path / "frozen"
    package = source / "backend" / "ride_agent"
    (package / "api").mkdir(parents=True)
    (package / "domain").mkdir()
    for name in ("catalog.py", "seed.py"):
        shutil.copy2(root / "backend" / "ride_agent" / "domain" / name, package / "domain" / name)
    (package / "__init__.py").touch()
    (package / "api" / "__init__.py").touch()
    (package / "domain" / "__init__.py").touch()
    (package / "api" / "app.py").write_text(
        (
            "import os, sqlite3\nsqlite3.connect(os.environ['RIDERECALL_"
            "AGENT_RIDE_DB']).close()\nraise RuntimeError('Intentional "
            "private preflight "
            "failure')\n"
        )
    )
    output = tmp_path / "result"
    environment = {**os.environ, "PYTHONPATH": str(source / "backend")}
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/bench/check-capacity.py"),
            "--source-root",
            str(source),
            "--source-commit",
            "isolated-failure-fixture",
            "--output",
            str(output),
            "--repeats",
            "1",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1, result.stderr
    report = json.loads((output / "summary.json").read_text())
    assert report["status"] == "failed" and report["completed_cases"] == 0
    assert any((row.get("detail") == "Final database audit could not complete" for row in report["failures"]))
    assert any((row.get("detail") == "Owned server exited during startup" for row in report["failures"]))
    assert report["integrity"] is None and report["health_samples"] == 0
    assert not (output / "access-credentials.json").exists()
