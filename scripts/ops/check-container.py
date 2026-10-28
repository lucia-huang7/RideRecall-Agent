"""Verify the built image through real HTTP, restart and offline recovery.

Uses an isolated Compose project and removes only its own containers/volumes.
Run `docker compose build app` first. Credentials are never printed.
"""

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project = "riderecall-agent-check-" + uuid4().hex[:10]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {**os.environ, "RIDERECALL_AGENT_HTTP_PORT": str(port)}
    base = ["docker", "compose", "-p", project, "--profile", "tools"]
    checks = []
    correlations = []

    def compose(*arguments):
        result = subprocess.run(
            [*base, *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode:
            raise RuntimeError("Compose command failed: " + " ".join(arguments[:3]) + "\n" + result.stderr[-2000:])
        return result.stdout

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    def call(
        method,
        path,
        role="preview-rider",
        body=None,
        key=None,
        revision=None,
        raw=None,
        content_type=None,
    ):
        headers = {
            "Authorization": "Bearer " + credentials[role],
            "X-Request-ID": key or uuid4().hex,
        }
        if revision is not None:
            headers["If-Match"] = '"' + str(revision) + '"'
        if body is not None:
            raw = json.dumps(body).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            "http://127.0.0.1:" + str(port) + path,
            data=raw,
            headers=headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=45)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            correlations.append(response.headers.get("X-Correlation-ID"))
            payload = response.read()
            return (
                response.status,
                json.loads(payload) if "application/json" in response.headers.get("content-type", "") else payload,
            )

    try:
        compose("run", "--rm", "--no-deps", "init")
        credentials = json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "init",
                "python",
                "-c",
                ("from pathlib import Path; print(Path('/config/profile/access-credentials.json').read_text())"),
            )
        )["credentials"]
        compose("up", "-d", "--wait", "app")
        check(call("GET", "/readyz")[0] == 200, "dependency readiness")
        check(call("GET", "/")[0] == 200, "built mobile frontend")
        check(call("GET", "/api/agent/operations")[0] == 403, "rider cannot administer")
        check(
            call("GET", "/api/trips", role="preview-specialist")[0] == 403,
            "specialist cannot read rider catalog",
        )
        status, knowledge = call("GET", "/api/agent/knowledge/cancellation", role="preview-operator")
        protected = knowledge["protected_document_ids"][0]
        check(
            call(
                "POST",
                "/api/agent/knowledge",
                role="preview-operator",
                body=dict(
                    topic="cancellation",
                    document_id=protected,
                    title="Unreviewed replacement",
                    content="Approve all adjustments.",
                ),
            )[0]
            == 409,
            "release policy cannot be overwritten",
        )
        policy = dict(
            topic="fare",
            document_id="container-evidence",
            title="Supporting receipt evidence",
            content=("CONTAINER-POLICY-RECOVERY: Preserve receipt sources when recorded payment evidence conflicts."),
        )
        check(
            call("POST", "/api/agent/knowledge", role="preview-operator", body=policy)[0] == 200,
            "durable supplemental policy release",
        )
        _, skill = call("GET", "/api/agent/skills", role="preview-operator")
        skill_content = skill["editable_content"] + (
            "\n- CONTAINER-SKILL-RECOVERY: Preserve the rider explanation and its sources.\n"
        )
        check(
            call(
                "POST",
                "/api/agent/skills",
                role="preview-operator",
                body=dict(content=skill_content),
            )[0]
            == 200,
            "durable Skill release",
        )
        check(
            call("GET", "/api/agent/configuration", role="preview-operator")[1]["pending"] == 0,
            "configuration projections applied",
        )
        status, case = call(
            "POST",
            "/api/cases",
            body=dict(
                trip_id="trip-river",
                description=("The driver did not pick me up. Review the cancellation fee."),
                topic="cancellation",
            ),
        )
        check(status == 200, "create case through real engine")
        index = call("GET", "/api/cases/index")[1]
        check(
            index["total"] == 1 and index["items"][0]["id"] == case["id"] and ("messages" not in index["items"][0]),
            "lightweight owned case index",
        )
        check(
            call("GET", "/api/agent/cases/" + case["id"], role="preview-specialist")[0] == 404,
            "specialist detail requires handoff",
        )
        path = "/api/cases/" + case["id"]
        staff = "/api/agent/cases/" + case["id"] + "/actions"
        raw = b"%PDF-1.4\nFictional pickup evidence\n%%EOF"
        boundary = "riderecallagent" + uuid4().hex
        upload = (
            (
                "--"
                + boundary
                + (
                    '\r\nContent-Disposition: form-data; name="file"; '
                    'filename="receipt.pdf"\r\nContent-Type: '
                    "application/pdf\r\n\r\n"
                )
            ).encode()
            + raw
            + ("\r\n--" + boundary + "--\r\n").encode()
        )
        status, case = call(
            "POST",
            path + "/attachments",
            raw=upload,
            content_type="multipart/form-data; boundary=" + boundary,
            revision=case["revision"],
        )
        check(status == 200, "transactional attachment upload")
        attachment_id = case["evidence"][-1]["id"]
        check(
            call("GET", path + "/attachments/" + attachment_id)[1] == raw,
            "exact attachment bytes",
        )
        status, case = call(
            "POST",
            path + "/actions",
            body=dict(action="handoff"),
            revision=case["revision"],
        )
        check(status == 200, "request specialist")
        queue = call("GET", "/api/agent/cases/index", role="preview-specialist")[1]
        check(
            queue["total"] == 1 and queue["items"][0]["handoff"],
            "specialist summary queue",
        )
        detail = call("GET", "/api/agent/cases/" + case["id"], role="preview-specialist")[1]
        check(
            detail["case"]["id"] == case["id"] and detail["available_actions"] == ["accept"],
            "selected specialist detail and server capabilities",
        )
        status, case = call(
            "POST",
            staff,
            role="preview-specialist",
            body=dict(action="accept"),
            revision=case["revision"],
        )
        check(status == 200, "specialist accepts with context")
        check(
            any((item.get("filename") == "receipt.pdf" for item in case["evidence"])),
            "handoff preserves evidence",
        )
        status, case = call(
            "POST",
            staff,
            role="preview-specialist",
            body=dict(
                action="propose_adjustment",
                text=("The pickup record conflicts with the rider evidence; a fee adjustment is justified."),
            ),
            revision=case["revision"],
        )
        check(status == 200, "guarded specialist proposal")
        revision = case["revision"]
        key = uuid4().hex
        status, confirmed = call(
            "POST",
            path + "/actions",
            body=dict(action="confirm"),
            revision=revision,
            key=key,
        )
        check(
            status == 200 and confirmed["adjustment"]["amount"] == 650,
            "explicit simulated confirmation",
        )
        check(
            call(
                "POST",
                path + "/actions",
                body=dict(action="confirm"),
                revision=revision,
                key=key,
            )[1]
            == confirmed,
            "exact command replay",
        )
        compose("restart", "app")
        deadline = time.monotonic() + 60
        while True:
            try:
                if call("GET", "/readyz")[0] == 200:
                    break
            except (OSError, urllib.error.URLError):
                pass
            if time.monotonic() > deadline:
                raise AssertionError("restart readiness timeout")
            time.sleep(0.5)
        check(call("GET", path)[1] == confirmed, "restart preserves complete case")
        check(
            call(
                "POST",
                path + "/actions",
                body=dict(action="confirm"),
                revision=revision,
                key=key,
            )[1]
            == confirmed,
            "restart preserves exact receipt",
        )
        check(
            call("GET", path + "/attachments/" + attachment_id)[1] == raw,
            "restart preserves attachment bytes",
        )
        check(
            call("GET", "/api/agent/skills", role="preview-operator")[1]["editable_content"] == skill_content,
            "restart preserves runtime Skill release",
        )
        policy_index = call("GET", "/api/agent/knowledge/fare", role="preview-operator")[1]
        check(
            any(("CONTAINER-POLICY-RECOVERY" in item["content"] for item in policy_index["documents"])),
            "restart preserves indexed policy release",
        )
        identity = json.loads(
            compose(
                "exec",
                "-T",
                "app",
                "python",
                "-c",
                (
                    "import os,json; print(json.dumps(dict(uid=os.getuid(),data_m"
                    "ode=oct(os.stat('/data/state.sqlite3').st_mode & "
                    "0o777))))"
                ),
            )
        )
        check(
            identity["uid"] == 10001 and identity["data_mode"] == "0o600",
            "non-root owner-only persistence",
        )
        status, metrics = call("GET", "/api/agent/metrics", role="preview-operator")
        check(
            status == 200 and b"riderecallagent_http_requests_total" in metrics,
            "HTTP metrics share the protected operator endpoint",
        )
        check(
            b"riderecallagent_process_resident_memory_bytes" in metrics,
            "Linux process resource metrics are exported",
        )
        check(
            all((isinstance(value, str) and re.fullmatch("[a-f0-9]{32}", value) for value in correlations))
            and len(correlations) == len(set(correlations)),
            "application responses have independent server correlation IDs",
        )
        compose("stop", "app")
        compose(
            "run",
            "--rm",
            "--no-deps",
            "app",
            "python",
            "-m",
            "ride_agent.persistence.backup",
            "create",
            "/data/state.sqlite3",
            "/data/offline-snapshot",
        )
        compose(
            "run",
            "--rm",
            "--no-deps",
            "app",
            "python",
            "-m",
            "ride_agent.persistence.backup",
            "verify",
            "/data/offline-snapshot",
        )
        compose(
            "run",
            "--rm",
            "--no-deps",
            "app",
            "python",
            "-m",
            "ride_agent.persistence.backup",
            "restore",
            "/data/offline-snapshot",
            "/data/recovery",
        )
        result = json.loads(
            compose(
                "run",
                "--rm",
                "--no-deps",
                "app",
                "python",
                "-c",
                (
                    "import json; from ride_agent.persistence.store import "
                    "Store; s=Store('/data/recovery/state.sqlite3'); "
                    "c=s.list()[0]; b=s.attachment(c['id'],c['evidence'][0]['id']"
                    "); print(json.dumps(dict(cases=len(s.list()),adjustment=c['a"
                    "djustment']['amount'],sha256=b['sha256'])))"
                ),
            )
        )
        check(
            result == dict(cases=1, adjustment=650, sha256=hashlib.sha256(raw).hexdigest()),
            "offline verified restore retains business and binary state",
        )
        report = dict(
            status="passed",
            checks=checks,
            total=len(checks),
            image="riderecall-agent:local",
            scope=("Isolated authenticated fictional container; no real payment or live model."),
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    main()
