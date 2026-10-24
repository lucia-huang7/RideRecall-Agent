"""Paced authenticated HTTP/restart/MCP workload against a frozen source tree.

Fictional data and deterministic model only. Uses a private loopback instance,
keeps failures and source hashes, and never touches the user's running demo.
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
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def utc():
    return datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    duration = parser.add_mutually_exclusive_group(required=True)
    duration.add_argument("--until", help="Timezone-aware ISO timestamp")
    duration.add_argument("--duration-seconds", type=int)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--mcp-fault-every", type=int, default=15)
    parser.add_argument("--restart-every", type=int, default=30)
    args = parser.parse_args()
    deadline = datetime.fromisoformat(args.until).timestamp() if args.until else time.time() + args.duration_seconds
    if args.until and datetime.fromisoformat(args.until).tzinfo is None:
        parser.error("--until requires an explicit timezone")
    if not 0 < deadline - time.time() <= 12 * 3600 or not 5 <= args.interval <= 300:
        parser.error(("Choose a future deadline within twelve hours and an interval of 5–300 seconds"))
    if min(args.mcp_fault_every, args.restart_every) < 1:
        parser.error("Fault intervals must be positive")
    source = args.source_root.resolve()
    output = args.output.resolve()
    if not (source / "backend/ride_agent/api/app.py").is_file():
        parser.error("Source tree does not contain the ride application")
    if output.exists():
        parser.error("Refusing to overwrite an existing workload run")
    output.mkdir(parents=True, mode=448)
    sys.path.insert(0, str(source / "backend"))
    from ride_agent.domain.catalog import RideCatalog
    from ride_agent.persistence.runtime_lock import embedded_writer_lock
    from ride_agent.persistence.store import Store

    base = RideCatalog()
    count = math.ceil((deadline - time.time()) / args.interval) + 10
    credentials = {
        role: secrets.token_urlsafe(32) for role in ("soak-rider-a", "soak-rider-b", "soak-specialist", "soak-operator")
    }
    identities = [
        dict(
            subject=role,
            roles=["specialist" if role == "soak-specialist" else "operator" if role == "soak-operator" else "rider"],
            token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=datetime.fromtimestamp(deadline + 3600, timezone.utc).isoformat(),
        )
        for role, token in credentials.items()
    ]
    trips = []
    for index in range(count):
        trip = {
            **base.trips[index % len(base.trips)],
            "id": f"trip-soak-{index:05}",
            "rider_id": "soak-rider-a" if index % 2 == 0 else "soak-rider-b",
        }
        trips.append(trip)
    catalog = output / "catalog.json"
    registry = output / "identities.json"
    database = output / "state.sqlite3"
    for path, value in (
        (
            catalog,
            dict(
                schema_version=1,
                dataset_id="isolated-paced-workload",
                trips=trips,
                policies=base.policies,
            ),
        ),
        (registry, dict(version=1, identities=identities)),
    ):
        path.write_text(json.dumps(value))
        path.chmod(384)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    environment.update(
        PYTHONPATH=str(source / "backend"),
        LLM_AGENT_SYSTEM_ENV="production",
        LLM_AGENT_SYSTEM_RIDE_MODEL_MODE="demo",
        LLM_AGENT_SYSTEM_RIDE_DB=str(database),
        LLM_AGENT_SYSTEM_RIDE_CATALOG_PATH=str(catalog),
        LLM_AGENT_SYSTEM_IDENTITY_FILE=str(registry),
        PYTHONUNBUFFERED="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    events = (output / "events.jsonl").open("w")
    server_log = (output / "server.log").open("w")
    process = None
    stopping = False
    started = utc()
    started_clock = time.monotonic()
    codes = Counter()
    checks = Counter()
    faults = Counter()
    latencies = []
    failures = []
    completed = 0
    owned = {role: set() for role in ("soak-rider-a", "soak-rider-b")}
    checkpoint = None
    metadata = dict(
        source_commit=args.source_commit,
        source_sha256={
            str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (source / "backend").rglob("*.py")
        },
        driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        started_at=started,
        deadline=datetime.fromtimestamp(deadline, timezone.utc).isoformat(),
        interval_seconds=args.interval,
        model_mode="demo",
        auth_mode="production",
        base_url=f"http://127.0.0.1:{port}",
        limitations=[
            ("Paced local deterministic workload, not concurrent production capacity or live-model reasoning quality."),
            "Financial actions and all trip/evidence data are fictional.",
        ],
    )
    (output / "run.json").write_text(json.dumps(metadata, indent=2) + "\n")
    shutil.copy2(__file__, output / "driver.py")

    def event(event_type, **data):
        events.write(json.dumps({"at": utc(), "event": event_type, **data}) + "\n")
        events.flush()

    def check(condition, label):
        checks[label] += 1
        if not condition:
            raise AssertionError(label)

    def request(
        method,
        path,
        role="soak-rider-a",
        body=None,
        key=None,
        revision=None,
        raw=None,
        content_type=None,
        expected_fault=False,
    ):
        headers = {"Authorization": "Bearer " + credentials[role]}
        if method == "POST":
            headers["X-Request-ID"] = key or uuid4().hex
        if revision is not None:
            headers["If-Match"] = '"' + str(revision) + '"'
        if body is not None:
            raw = json.dumps(body).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        began = time.monotonic()
        try:
            response = opener.open(
                urllib.request.Request(
                    f"http://127.0.0.1:{port}" + path,
                    data=raw,
                    headers=headers,
                    method=method,
                ),
                timeout=40,
            )
        except urllib.error.HTTPError as error:
            response = error
        except OSError:
            codes["network_error"] += 1
            event(
                "http",
                method=method,
                path=path,
                status="network_error",
                expected_fault=expected_fault,
            )
            raise
        with response:
            data = response.read()
            value = json.loads(data) if "application/json" in response.headers.get("content-type", "") else data
            status = response.status
        elapsed = (time.monotonic() - began) * 1000
        latencies.append(elapsed)
        codes[str(status)] += 1
        event(
            "http",
            method=method,
            path=path,
            status=status,
            elapsed_ms=round(elapsed, 3),
            expected_fault=expected_fault,
        )
        return (status, value)

    def start_server():
        nonlocal process
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
            stdout=server_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        event("server_start", pid=process.pid)

    def stop_server(crash=False):
        if process is None or process.poll() is not None:
            return
        os.killpg(process.pid, signal.SIGKILL if crash else signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

    def ready():
        end = time.monotonic() + 30
        while time.monotonic() < end:
            try:
                if request("GET", "/readyz", expected_fault=True)[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(0.5)
        raise AssertionError("dependency readiness did not recover within thirty seconds")

    def mutate(path, record, body, role, key=None):
        status, value = request("POST", path, role, body, key=key, revision=record["revision"])
        check(status == 200, "mutation accepted")
        return value

    def verify_checkpoint():
        if not checkpoint:
            return
        role, path, record, body, key, revision = checkpoint
        check(
            request("GET", path, role)[1] == record,
            "restart preserves last complete case",
        )
        check(
            request("POST", path + "/actions", role, body, key=key, revision=revision)[1] == record,
            "restart preserves exact command receipt",
        )

    def verify_pages():
        for role, expected in owned.items():
            found = set()
            cursor = None
            while True:
                path = "/api/cases/index?limit=7" + ("&cursor=" + urllib.parse.quote(cursor, safe="") if cursor else "")
                status, page = request("GET", path, role)
                check(status == 200, "owned index readable")
                check(
                    all(("messages" not in item for item in page["items"])),
                    "summaries exclude conversation payloads",
                )
                found.update((item["id"] for item in page["items"]))
                cursor = page["next_cursor"]
                if not cursor:
                    break
            check(found == expected, "paged case identity set complete")

    def mcp_fault():
        rows = subprocess.check_output(["ps", "-Ao", "pid=,ppid=,command="], text=True).splitlines()
        matching = [
            int(parts[0])
            for row in rows
            if len((parts := row.strip().split(None, 2))) == 3
            and parts[1] == str(process.pid)
            and (str(source / "backend/ride_agent/integrations/mcp_server.py") in parts[2])
        ]
        check(len(matching) == 1, "MCP fault targets only the isolated child")
        os.kill(matching[0], signal.SIGKILL)
        faults["mcp_child_killed"] += 1
        event("expected_fault", kind="mcp_child", pid=matching[0])
        ready()
        verify_checkpoint()

    def workflow(index):
        nonlocal checkpoint
        trip = trips[index]
        role = trip["rider_id"]
        other = "soak-rider-b" if role == "soak-rider-a" else "soak-rider-a"
        status, case = request(
            "POST",
            "/api/cases",
            role,
            dict(
                trip_id=trip["id"],
                description="Review my " + trip["issue"] + " issue and preserve the recorded payment evidence.",
                topic=trip["issue"],
            ),
        )
        check(status == 200, "create through actual engine")
        owned[role].add(case["id"])
        path = "/api/cases/" + case["id"]
        raw = ("%PDF-1.4\nFictional evidence " + str(index) + "\n%%EOF").encode()
        boundary = "soak" + uuid4().hex
        upload = (
            (
                "--"
                + boundary
                + (
                    '\r\nContent-Disposition: form-data; name="file"; '
                    'filename="fictional-receipt.pdf"\r\nContent-Type: '
                    "application/pdf\r\n\r\n"
                )
            ).encode()
            + raw
            + ("\r\n--" + boundary + "--\r\n").encode()
        )
        status, case = request(
            "POST",
            path + "/attachments",
            role,
            revision=case["revision"],
            raw=upload,
            content_type="multipart/form-data; boundary=" + boundary,
        )
        check(status == 200, "evidence upload")
        attachment = case["evidence"][-1]["id"]
        check(
            request("GET", path + "/attachments/" + attachment, role)[1] == raw,
            "attachment bytes exact",
        )
        check(request("GET", path, other)[0] == 404, "foreign rider cannot read case")
        text = "Preserve my receipt reference SOAK-" + str(index) + ". Explain the evidence before any payment action."
        case = mutate(path + "/actions", case, dict(action="message", text=text), role)
        check(
            any((m["role"] == "rider" and m["content"] == text for m in case["messages"])),
            "rider context preserved",
        )
        check(not case["adjustment"], "analysis is financially read-only")
        if trip["issue"] == "cancellation":
            case = mutate(path + "/actions", case, dict(action="handoff"), role)
            detail = request("GET", "/api/agent/cases/" + case["id"], "soak-specialist")[1]
            check(
                detail["case"]["messages"] == case["messages"],
                "handoff preserves full conversation",
            )
            staff = "/api/agent/cases/" + case["id"] + "/actions"
            case = mutate(staff, case, dict(action="accept"), "soak-specialist")
            case = mutate(
                staff,
                case,
                dict(
                    action="propose_adjustment",
                    text=(
                        "The fictional pickup evidence conflicts with the arrival "
                        "record. A fee adjustment is "
                        "justified."
                    ),
                ),
                "soak-specialist",
            )
        action = "close" if trip["issue"] == "payment" else "confirm"
        body = dict(action=action)
        key = uuid4().hex
        revision = case["revision"]
        case = mutate(path + "/actions", case, body, role, key)
        check(case["status"] == "resolved", "workflow reaches a recorded outcome")
        check(
            sum((e["kind"] == "adjustment" for e in case["events"])) <= 1,
            "at most one adjustment event",
        )
        if action == "confirm":
            check(
                case["adjustment"]["amount"]
                == (trip["total"] if trip["issue"] == "cancellation" else trip["total"] - trip["estimated"]),
                "authoritative adjustment amount",
            )
        else:
            check(case["adjustment"] is None, "payment clarification moves no money")
        check(
            request("POST", path + "/actions", role, body, key=key, revision=revision)[1] == case,
            "exact financial/outcome replay",
        )
        check(
            request("POST", path + "/actions", role, body, revision=revision)[0] == 412,
            "stale new command rejected",
        )
        checkpoint = (role, path, case, body, key, revision)

    def interrupt(*unused):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)

    def report(final=False):
        elapsed = time.monotonic() - started_clock
        ordered = sorted(latencies)
        data = dict(
            source_commit=args.source_commit,
            started_at=started,
            updated_at=utc(),
            elapsed_seconds=round(elapsed, 3),
            reached_deadline=time.time() >= deadline,
            status="passed"
            if not failures and time.time() >= deadline
            else "failed"
            if failures
            else "interrupted"
            if final
            else "running",
            workflows_completed=completed,
            created_cases=sum(map(len, owned.values())),
            http_statuses=dict(codes),
            checks=dict(checks),
            expected_faults=dict(faults),
            unexpected_failures=failures,
            median_http_ms=ordered[len(ordered) // 2] if ordered else None,
            p95_http_ms=ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] if ordered else None,
            limitations=metadata["limitations"],
        )
        temporary = output / "summary.pending"
        temporary.write_text(json.dumps(data, indent=2) + "\n")
        temporary.replace(output / "summary.json")
        return data

    try:
        start_server()
        ready()
        check(
            request("GET", "/runtime-config")[1]["model_mode"] == "demo",
            "no provider key required",
        )
        next_work = time.monotonic()
        next_health = time.monotonic()
        index = 0
        while not stopping and time.time() < deadline:
            now = time.monotonic()
            if now >= next_work and deadline - time.time() > 3:
                try:
                    workflow(index)
                    completed += 1
                    event("workflow_complete", index=index, trip_id=trips[index]["id"])
                    if completed % args.mcp_fault_every == 0:
                        mcp_fault()
                    if completed % args.restart_every == 0:
                        stop_server(crash=True)
                        faults["backend_crash"] += 1
                        event("expected_fault", kind="backend_crash")
                        start_server()
                        ready()
                        verify_checkpoint()
                    if completed % 10 == 0:
                        verify_pages()
                except Exception as error:
                    failures.append(
                        dict(
                            at=utc(),
                            index=index,
                            error_type=type(error).__name__,
                            detail=str(error)[:400],
                        )
                    )
                    event("failure", **failures[-1])
                index += 1
                next_work = max(next_work + args.interval, time.monotonic() + 0.1)
                report()
                if index >= len(trips):
                    raise RuntimeError("The preallocated workload catalog was exhausted")
            if time.monotonic() >= next_health:
                try:
                    status, value = request("GET", "/readyz")
                    check(
                        status == 200 and value["status"] == "ok",
                        "sustained dependency readiness",
                    )
                except Exception as error:
                    failures.append(
                        dict(
                            at=utc(),
                            error_type=type(error).__name__,
                            detail="Readiness failure: " + str(error)[:300],
                        )
                    )
                    event("failure", **failures[-1])
                    if process.poll() is not None:
                        start_server()
                        ready()
                next_health = time.monotonic() + 5
            time.sleep(min(0.5, max(0, deadline - time.time())))
        verify_pages()
        verify_checkpoint()
    except Exception as error:
        failures.append(dict(at=utc(), error_type=type(error).__name__, detail=str(error)[:400]))
        event("failure", **failures[-1])
    finally:
        stop_server()
        if database.exists():
            try:
                with embedded_writer_lock(database):
                    store = Store(database)
                    with store.connect() as db:
                        check(
                            db.execute("PRAGMA quick_check").fetchall() == [("ok",)],
                            "final SQLite integrity",
                        )
                        check(
                            not db.execute("PRAGMA foreign_key_check").fetchall(),
                            "final foreign-key integrity",
                        )
                        check(
                            db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == sum(map(len, owned.values())),
                            "final case count matches accepted creates",
                        )
                        check(
                            db.execute("SELECT COUNT(*) FROM case_index").fetchone()[0]
                            == sum(map(len, owned.values())),
                            "final index count matches source",
                        )
            except Exception as error:
                failures.append(
                    dict(
                        at=utc(),
                        error_type=type(error).__name__,
                        detail=str(error)[:400],
                    )
                )
        result = report(final=True)
        event("finished", status=result["status"])
        events.close()
        server_log.close()
        print(json.dumps(result, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
