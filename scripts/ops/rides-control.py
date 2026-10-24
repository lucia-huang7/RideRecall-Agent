"""Install and control the macOS user-level LLM-Agent-System demo service."""

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTANCE = hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]
INSTALL = Path.home() / "Library" / "Application Support" / "LLM-Agent-System" / INSTANCE
LABEL = "com.llmagentsystem.rides." + INSTANCE
PLIST = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
DOMAIN = "gui/" + str(os.getuid())
TARGET = DOMAIN + "/" + LABEL
URL = "http://127.0.0.1:5175/#/manage"


def run(args, check=True, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=check, **kwargs)


def loaded():
    return (
        run(
            ["/bin/launchctl", "print", TARGET],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def state():
    try:
        return json.loads((INSTALL / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def available(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def digest(paths):
    value = hashlib.sha256()
    for path in sorted(paths):
        if path.is_file() and "__pycache__" not in path.parts:
            value.update(str(path.relative_to(ROOT)).encode())
            value.update(path.read_bytes())
    return value.hexdigest()


def stop():
    if loaded():
        run(["/bin/launchctl", "bootout", TARGET])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and any((not available(port) for port in (5175, 8010))):
            time.sleep(0.25)
    print("LLM-Agent-System background service stopped. Saved cases remain in " + str(INSTALL / "data"))


def ensure_frontend_dependencies(frontend, installation):
    fingerprint = hashlib.sha256(
        (frontend / "package.json").read_bytes() + (frontend / "package-lock.json").read_bytes()
    ).hexdigest()
    marker = installation / "frontend-dependencies.sha256"
    if (frontend / "node_modules").exists() and marker.exists() and (marker.read_text() == fingerprint):
        return False
    run(
        [
            "npm",
            "ci",
            "--no-audit",
            "--no-fund",
            "--fetch-timeout=30000",
            "--fetch-retries=1",
        ],
        cwd=frontend,
    )
    marker.write_text(fingerprint)
    return True


def install():
    if sys.version_info[:2] not in [(3, 11), (3, 12)]:
        raise RuntimeError("Use Python 3.11 or 3.12 to install the pinned demo dependencies.")
    INSTALL.mkdir(parents=True, exist_ok=True, mode=448)
    INSTALL.chmod(448)
    (INSTALL / "logs").mkdir(exist_ok=True, mode=448)
    (INSTALL / "logs").chmod(448)
    python = INSTALL / "venv" / "bin" / "python"
    requirements = ROOT / "backend" / "requirements.txt"
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = INSTALL / "requirements.sha256"
    if not python.exists():
        print("Creating a persistent Python environment…", flush=True)
        venv.EnvBuilder(with_pip=True).create(INSTALL / "venv")
    if not marker.exists() or marker.read_text() != fingerprint:
        print(
            ("Installing demo dependencies. This is needed only on first setup or dependency updates."),
            flush=True,
        )
        run([python, "-m", "pip", "install", "-r", requirements])
        marker.write_text(fingerprint)
    frontend = ROOT / "frontend"
    source_files = [
        requirements,
        *list((ROOT / "backend").rglob("*.py")),
        *list((ROOT / "backend" / "ride_agent" / "skills").rglob("*")),
        *list((ROOT / "scripts" / "ops" / "runtime").glob("*.py")),
        *list((frontend / "src").rglob("*")),
        frontend / "package-lock.json",
        frontend / "package.json",
        frontend / "vite.config.js",
        frontend / "eslint.config.js",
        frontend / "index.html",
    ]
    source_hash = digest(
        (
            path
            for path in source_files
            if not any((part in {".git", ".venv", "tests"} for part in path.relative_to(ROOT).parts))
        )
    )
    source_marker = INSTALL / "source.sha256"
    changed = not source_marker.exists() or source_marker.read_text() != source_hash
    if changed or not (frontend / "dist" / "index.html").exists():
        ensure_frontend_dependencies(frontend, INSTALL)
        print("Building the production frontend…", flush=True)
        run(["npm", "run", "lint"], cwd=frontend)
        run(["npm", "run", "build"], cwd=frontend)
    if changed:
        if loaded():
            stop()
        app = INSTALL / "app"
        app.mkdir(exist_ok=True)
        if (app / "backend").exists():
            shutil.rmtree(app / "backend")
        for package in ("ride_agent",):
            shutil.copytree(
                ROOT / "backend" / package,
                app / "backend" / package,
                ignore=shutil.ignore_patterns("__pycache__", ".env*", "*.pyc"),
            )
        shutil.copytree(frontend / "dist", app / "frontend" / "dist", dirs_exist_ok=True)
        shutil.copytree(
            ROOT / "scripts" / "ops" / "runtime",
            INSTALL / "runtime",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        source_marker.write_text(source_hash)
    data = INSTALL / "data"
    if not (data / "rides.sqlite3").exists():
        data.mkdir(exist_ok=True)
        old = ROOT / ".runtime" / "rides.sqlite3"
        if old.exists():
            print("Migrating existing demo cases and indexes…", flush=True)
            with (
                sqlite3.connect(old) as origin,
                sqlite3.connect(data / "rides.sqlite3.migrating") as target,
            ):
                origin.backup(target)
            for name in ("rides.vectors", "rides.skills"):
                if (old.parent / name).exists():
                    shutil.copytree(old.parent / name, data / name, dirs_exist_ok=True)
            baseline = old.with_suffix(".baseline.json")
            if baseline.exists():
                shutil.copy2(baseline, data / baseline.name)
            (data / "rides.sqlite3.migrating").replace(data / "rides.sqlite3")
    (INSTALL / "config.json").write_text(json.dumps({"instance": INSTANCE, "source": str(ROOT)}))
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    definition = {
        "Label": LABEL,
        "ProgramArguments": [str(python), str(INSTALL / "runtime" / "supervise.py")],
        "WorkingDirectory": str(INSTALL),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "ExitTimeOut": 30,
        "EnvironmentVariables": {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
        },
        "StandardOutPath": str(INSTALL / "logs" / "supervisor.log"),
        "StandardErrorPath": str(INSTALL / "logs" / "supervisor.log"),
    }
    PLIST.write_bytes(plistlib.dumps(definition))
    PLIST.chmod(384)


def start(open_browser=True):
    if not loaded():
        busy = [str(port) for port in (5175, 8010) if not available(port)]
        if busy:
            raise RuntimeError(
                "Port "
                + ", ".join(busy)
                + (" is already in use. Stop the development server or conflicting app first.")
            )
    install()
    if not loaded():
        busy = [str(port) for port in (5175, 8010) if not available(port)]
        if busy:
            raise RuntimeError(
                "Port "
                + ", ".join(busy)
                + (" is already in use. Stop the development server or conflicting app before starting this service.")
            )
        run(["/bin/launchctl", "bootstrap", DOMAIN, PLIST])
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        current = state()
        if current.get("status") == "ready" and time.time() - current.get("checked_at", 0) < 12:
            print(
                "Ready: frontend, backend and independent MCP service are healthy.\n"
                + URL
                + "\nData and logs: "
                + str(INSTALL),
                flush=True,
            )
            if open_browser:
                import webbrowser

                webbrowser.open(URL)
            return
        time.sleep(0.5)
    raise RuntimeError(
        ("Service did not become healthy within 90 seconds. Open the Status command and inspect ")
        + str(INSTALL / "logs")
    )


def main():
    if sys.platform != "darwin":
        raise RuntimeError(
            ("The background launcher requires macOS. Use scripts/dev/run-rides.sh --dev on other systems.")
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status", "restart"])
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    if args.action == "stop":
        stop()
    elif args.action == "status":
        running = loaded()
        current = state()
        if not running and current:
            current.update(
                status="stopped",
                healthy={"frontend": False, "backend": False, "mcp": False},
            )
        print(
            json.dumps(
                {
                    "loaded": running,
                    "url": URL,
                    "installation": str(INSTALL),
                    "service": current,
                },
                indent=2,
            )
        )
    else:
        if args.action == "restart":
            stop()
        start(not args.no_open)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(
            "LLM-Agent-System could not complete this action: " + str(error)[:600],
            file=sys.stderr,
        )
        sys.exit(1)
