"""The owned workload must report startup/audit failure without orphaning a child."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_capacity_driver_preserves_failure_report_when_startup_leaves_an_empty_database(
    tmp_path,
):
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "frozen"
    package = source / "backend" / "mobility"
    package.mkdir(parents=True)
    for name in ("__init__.py", "catalog.py", "seed.py"):
        shutil.copy2(root / "backend" / "mobility" / name, package / name)
    (package / "app.py").write_text(
        "import os, sqlite3\nsqlite3.connect(os.environ['LLM_AGENT_SYSTEM_RIDE_DB']).close()\nraise RuntimeError('Intentional private preflight failure')\n"
    )
    output = tmp_path / "result"
    environment = {**os.environ, "PYTHONPATH": str(source / "backend")}
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/check-capacity.py"),
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
    assert any(
        row.get("detail") == "Final database audit could not complete"
        for row in report["failures"]
    )
    assert any(
        row.get("detail") == "Owned server exited during startup"
        for row in report["failures"]
    )
    assert report["integrity"] is None and report["health_samples"] == 0
    assert not (output / "access-credentials.json").exists()
