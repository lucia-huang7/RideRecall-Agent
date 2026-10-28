"""Measure bounded concurrent HTTP admission on a private frozen-source instance.

Fictional owned records and deterministic inference only. This script cannot target
an existing deployment. Rejected commands retry with their original request IDs.
Results describe local HTTP behavior, not live-model capacity or a production SLO.
"""

import argparse
import hashlib
import json
import math
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("The isolated verifier requires POSIX process groups")
    if not 1 <= args.repeats <= 5:
        parser.error("Choose one to five repetitions per concurrency level")
    source = args.source_root.resolve()
    output = args.output.resolve()
    if not (source / "backend/ride_agent/api/app.py").is_file():
        parser.error("Source does not contain the ride application")
    if output.exists():
        parser.error("Refusing to overwrite an existing result directory")
    output.mkdir(parents=True, mode=448)
    sys.path.insert(0, str(source / "backend"))
    from ride_agent.domain.catalog import RideCatalog

    base = RideCatalog()
    levels = (1, 2, 4, 8, 16)
    total = sum(levels) * args.repeats
    credentials = {f"capacity-rider-{i}": secrets.token_urlsafe(32) for i in range(16)}
    credentials["capacity-operator"] = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    identities = [
        dict(
            subject=role,
            roles=["operator" if role == "capacity-operator" else "rider"],
            token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=expires,
        )
        for role, token in credentials.items()
    ]
    records = []
    for index in range(total):
        records.append(
            {
                **base.trips[index % len(base.trips)],
                "id": f"trip-capacity-{index:04}",
                "rider_id": f"capacity-rider-{index % 16}",
            }
        )
    registry = output / "identities.json"
    catalog = output / "catalog.json"
    database = output / "state.sqlite3"
    for path, value in (
        (registry, dict(version=1, identities=identities)),
        (
            catalog,
            dict(
                schema_version=1,
                dataset_id="isolated-capacity-check",
                trips=records,
                policies=base.policies,
            ),
        ),
    ):
        path.write_text(json.dumps(value))
        path.chmod(384)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {key: value for key, value in os.environ.items() if not key.endswith("_API_KEY")}
    environment.update(
        PYTHONPATH=str(source / "backend"),
        RIDERECALL_AGENT_ENV="production",
        RIDERECALL_AGENT_RIDE_MODEL_MODE="demo",
        RIDERECALL_AGENT_RIDE_DB=str(database),
        RIDERECALL_AGENT_RIDE_CATALOG_PATH=str(catalog),
        RIDERECALL_AGENT_IDENTITY_FILE=str(registry),
        PYTHONUNBUFFERED="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    metadata = dict(
        source_commit=args.source_commit,
        source_sha256={
            str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (source / "backend").rglob("*.py")
        },
        driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        started_at=datetime.now(timezone.utc).isoformat(),
        levels=levels,
        repeats=args.repeats,
        expected_cases=total,
        limitations=[
            ("Local deterministic HTTP workload on one machine; not live-model capacity or production SLO evidence."),
            "No real rider records, credentials or payments are used.",
        ],
    )
    (output / "run.json").write_text(json.dumps(metadata, indent=2) + "\n")
    shutil.copy2(__file__, output / "driver.py")
    events = (output / "events.jsonl").open("w")
    log = (output / "server.log").open("w")
    event_lock = threading.Lock()
    process = None
    stop = threading.Event()
    sampler = None
    failures = []
    stages = []
    completed = []
    health = []
    admission = None

    def call(method, path, role="capacity-rider-0", body=None, key=None):
        headers = {"Authorization": "Bearer " + credentials[role]}
        if key:
            headers["X-Request-ID"] = key
        raw = json.dumps(body).encode() if body is not None else None
        if raw is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"http://127.0.0.1:{port}" + path, data=raw, headers=headers, method=method)
        began = time.monotonic()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            response = opener.open(request, timeout=45)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            value = json.load(response)
            status = response.status
        elapsed = (time.monotonic() - began) * 1000
        with event_lock:
            events.write(
                json.dumps(
                    dict(
                        at=datetime.now(timezone.utc).isoformat(),
                        method=method,
                        route="/api/cases/{id}" if path.startswith("/api/cases/") else path,
                        status=status,
                        elapsed_ms=elapsed,
                    )
                )
                + "\n"
            )
            events.flush()
        return (status, value, elapsed)

    def ensure(condition, label):
        if not condition:
            raise AssertionError(label)

    def observe():
        while not stop.is_set():
            try:
                for path in ("/livez", "/readyz"):
                    status, value, elapsed = call("GET", path)
                    health.append(dict(route=path, status=status, elapsed_ms=elapsed))
            except Exception as error:
                health.append(dict(error_type=type(error).__name__))
            stop.wait(0.1)

    def interrupt(*unused):
        raise KeyboardInterrupt("Capacity run interrupted")

    signal.signal(signal.SIGTERM, interrupt)
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "ride_agent.api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
                "--limit-concurrency",
                "64",
                "--no-proxy-headers",
                "--no-access-log",
            ],
            cwd=source / "backend",
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Owned server exited during startup")
            try:
                status, _, _ = call("GET", "/readyz")
                if status == 200:
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("Owned server did not become ready")
        ensure(
            call("GET", "/api/session", "capacity-operator")[1]["subject"] == "capacity-operator",
            "Private instance identity mismatch",
        )
        sampler = threading.Thread(target=observe, daemon=True)
        sampler.start()
        index = 0
        for concurrency in levels:
            for repetition in range(args.repeats):
                batch = []
                for _ in range(concurrency):
                    record = records[index]
                    index += 1
                    batch.append(
                        dict(
                            role=record["rider_id"],
                            body=dict(
                                trip_id=record["id"],
                                description=("Please review this fictional trip charge and retain my case context."),
                            ),
                            key=uuid4().hex,
                        )
                    )
                barrier = threading.Barrier(concurrency)

                def submit(item):
                    barrier.wait(timeout=10)
                    return call("POST", "/api/cases", item["role"], item["body"], item["key"])

                began = time.monotonic()
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    responses = list(pool.map(submit, batch))
                wall = time.monotonic() - began
                stage = dict(
                    concurrency=concurrency,
                    repetition=repetition,
                    initial_statuses=dict(Counter((status for status, _, _ in responses))),
                    burst_wall_seconds=wall,
                    initial_latencies_ms=[elapsed for _, _, elapsed in responses],
                    retry_attempts=0,
                )
                for item, response in zip(batch, responses, strict=True):
                    status, case, _ = response
                    ensure(status in (200, 429, 503), "Unexpected initial command status")
                    for _ in range(3):
                        if status == 200:
                            break
                        time.sleep(2)
                        stage["retry_attempts"] += 1
                        status, case, _ = call(
                            "POST",
                            "/api/cases",
                            item["role"],
                            item["body"],
                            item["key"],
                        )
                    ensure(
                        status == 200,
                        "Rejected command did not recover with the same request ID",
                    )
                    ensure(
                        case["trip_id"] == item["body"]["trip_id"] and case["adjustment"] is None,
                        "Case identity or read-only financial boundary failed",
                    )
                    ensure(
                        case["engine_runs"][-1]["status"] == "success",
                        "Concurrent engine execution was incomplete",
                    )
                    ensure(
                        call(
                            "POST",
                            "/api/cases",
                            item["role"],
                            item["body"],
                            item["key"],
                        )[1]
                        == case,
                        "Exact create receipt replay failed",
                    )
                    foreign = "capacity-rider-1" if item["role"] != "capacity-rider-1" else "capacity-rider-0"
                    ensure(
                        call("GET", "/api/cases/" + case["id"], foreign)[0] == 404,
                        "Concurrent case ownership boundary failed",
                    )
                    completed.append(case["id"])
                stages.append(stage)
        status, operations, _ = call("GET", "/api/agent/operations", "capacity-operator")
        ensure(
            status == 200 and operations["analysis_admission"]["active"] == 0,
            "Admission slots were not released",
        )
        admission = operations["analysis_admission"]
    except (Exception, KeyboardInterrupt) as error:
        failures.append(dict(error_type=type(error).__name__, detail=str(error)[:300]))
    finally:
        stop.set()
        if sampler:
            sampler.join(timeout=50)
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        log.close()
        events.close()
        integrity = None
        if database.exists():
            try:
                with sqlite3.connect(database) as db:
                    integrity = dict(
                        quick_check=db.execute("PRAGMA quick_check").fetchall(),
                        foreign_key_errors=db.execute("PRAGMA foreign_key_check").fetchall(),
                        cases=db.execute("SELECT COUNT(*) FROM cases").fetchone()[0],
                        receipts=db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0],
                    )
                if (
                    integrity["quick_check"] != [("ok",)]
                    or integrity["foreign_key_errors"]
                    or integrity["cases"] != total
                    or (integrity["receipts"] != total)
                ):
                    failures.append(dict(detail="Final database integrity/count check failed"))
            except sqlite3.Error as error:
                failures.append(
                    dict(
                        error_type=type(error).__name__,
                        detail="Final database audit could not complete",
                    )
                )
        else:
            failures.append(dict(detail="Owned case database is missing at shutdown"))
        if not health or any((row.get("status") != 200 for row in health)):
            failures.append(dict(detail="Liveness/readiness was unavailable during the workload"))
        latencies = sorted((row["elapsed_ms"] for row in health if "elapsed_ms" in row))
        report = dict(
            source_commit=args.source_commit,
            status="passed" if not failures else "failed",
            completed_cases=len(completed),
            stages=stages,
            admission=admission,
            health_samples=len(health),
            health_p95_ms=latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else None,
            integrity=integrity,
            failures=failures,
            limitations=metadata["limitations"],
        )
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
