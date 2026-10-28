"""launchd-owned process supervisor for the local no-key demo."""

import fcntl
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path

INSTANCE_ROOT = Path(__file__).resolve().parents[1]
STATE = INSTANCE_ROOT / "state.json"
STOPPING = False


def read_json(url, headers=None, data=None, allow_error=False):
    request = urllib.request.Request(
        url,
        headers=headers or {},
        data=json.dumps(data).encode() if data is not None else None,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        response = opener.open(request, timeout=3)
    except urllib.error.HTTPError as error:
        if not allow_error:
            raise
        response = error
    with response:
        return json.load(response)


class ChildLog:
    """Drain child pipes into bounded files; children never hold a rotated file FD."""

    def __init__(self, path, max_bytes=10 * 1024 * 1024, backups=2):
        self.handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
        self.logger = logging.Logger("child-" + path.stem, level=logging.INFO)
        self.logger.addHandler(self.handler)
        self.threads = []

    def attach(self, process):
        self.threads = [thread for thread in self.threads if thread.is_alive()]

        def drain():
            try:
                for chunk in iter(lambda: process.stdout.readline(16384), ""):
                    self.logger.info(chunk.rstrip("\r\n"))
            finally:
                process.stdout.close()

        thread = threading.Thread(target=drain, name="log-" + str(process.pid), daemon=True)
        self.threads.append(thread)
        thread.start()

    def close(self):
        for thread in self.threads:
            thread.join(timeout=3)
        self.handler.close()


def sample_health(liveness, readiness, frontend, failures):
    alive = liveness.get("status") == "ok"
    mcp = readiness.get("components", {}).get("mcp")
    healthy = dict(backend=readiness.get("status") == "ok", frontend=frontend, mcp=mcp is True)
    failures["backend"] = 0 if alive else failures["backend"] + 1
    failures["frontend"] = 0 if frontend else failures["frontend"] + 1
    failures["mcp"] = failures["mcp"] + 1 if alive and mcp is False else 0
    return (healthy, dict(backend=alive, frontend=frontend))


def recovery_decision(failures, started, now, backend_grace=35):
    result = []
    for name in ("backend", "frontend"):
        if failures[name] >= 4 and now - started[name] > (backend_grace if name == "backend" else 35):
            result.append(name)
    if failures["mcp"] >= 6 and now - started["backend"] > backend_grace and ("backend" not in result):
        result.append("backend")
    return result


def stop_process(process):
    if process is None or process.poll() is not None:
        return

    def signal_owned(sig):
        try:
            os.killpg(process.pid, sig)
        except PermissionError:
            # launchd may deny group signaling; the Popen child is still owned.
            process.send_signal(sig)

    try:
        signal_owned(signal.SIGTERM)
        process.wait(timeout=8)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        try:
            signal_owned(signal.SIGKILL)
            process.wait(timeout=5)
        except ProcessLookupError:
            return


def save_state(data):
    temporary = STATE.with_suffix(".pending")
    temporary.write_text(json.dumps(data, indent=2))
    temporary.replace(STATE)


def main():
    global STOPPING
    os.umask(63)
    INSTANCE_ROOT.mkdir(parents=True, exist_ok=True)
    lock = (INSTANCE_ROOT / "supervisor.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    try:
        previous = json.loads(STATE.read_text())
    except (OSError, ValueError):
        previous = {}
    for name in ("backend", "frontend"):
        pid = previous.get(name + "_pid")
        if not isinstance(pid, int):
            continue
        command = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if command.startswith(str(INSTANCE_ROOT / "venv" / "bin" / "python") + " "):
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                command = subprocess.run(
                    ["/bin/ps", "-p", str(pid), "-o", "command="],
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                if not command.startswith(str(INSTANCE_ROOT / "venv" / "bin" / "python") + " "):
                    break
                time.sleep(0.2)
            else:
                os.killpg(pid, signal.SIGKILL)
    config = json.loads((INSTANCE_ROOT / "config.json").read_text())
    logs = INSTANCE_ROOT / "logs"
    logs.mkdir(exist_ok=True, mode=448)
    logs.chmod(448)
    env = os.environ.copy()
    env.update(
        RIDERECALL_AGENT_ENV="development",
        RIDERECALL_AGENT_RIDE_MODEL_MODE="demo",
        RIDERECALL_AGENT_RIDE_DB=str(INSTANCE_ROOT / "data" / "rides.sqlite3"),
        RIDERECALL_AGENT_INSTANCE_ID=config["instance"],
        PYTHONUNBUFFERED="1",
    )
    commands = {
        "backend": [
            sys.executable,
            "-m",
            "uvicorn",
            "ride_agent.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8010",
            "--limit-concurrency",
            "64",
            "--timeout-keep-alive",
            "5",
            "--no-proxy-headers",
            "--no-access-log",
        ],
        "frontend": [sys.executable, str(INSTANCE_ROOT / "runtime" / "frontend.py")],
    }
    processes = {}
    backend_was_alive = False
    failures = {"backend": 0, "frontend": 0, "mcp": 0}
    restarts = {"backend": 0, "frontend": 0, "mcp": 0}
    handles = {name: ChildLog(logs / (name + ".log")) for name in commands}
    status = {
        "supervisor_pid": os.getpid(),
        "instance": config["instance"],
        "status": "starting",
        "restarts": restarts,
    }

    def signal_stop(*args):
        global STOPPING
        STOPPING = True

    signal.signal(signal.SIGTERM, signal_stop)
    signal.signal(signal.SIGINT, signal_stop)

    def start(name):
        processes[name] = subprocess.Popen(
            commands[name],
            cwd=INSTANCE_ROOT / "app" / "backend",
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        handles[name].attach(processes[name])
        status[name + "_pid"] = processes[name].pid
        status[name + "_started"] = time.time()
        failures[name] = 0
        save_state(status)

    try:
        for name in commands:
            start(name)
        while not STOPPING:
            for name, process in list(processes.items()):
                if process.poll() is not None:
                    stop_process(process)
                    restarts[name] += 1
                    time.sleep(min(5, restarts[name]))
                    if not STOPPING:
                        start(name)
            if STOPPING:
                break
            liveness = {}
            readiness = {}
            frontend = False
            try:
                liveness = read_json("http://127.0.0.1:8010/livez")
            except Exception:
                pass
            try:
                readiness = read_json("http://127.0.0.1:8010/readyz", allow_error=True)
            except Exception:
                pass
            try:
                frontend = (
                    read_json("http://127.0.0.1:5175/__riderecallagent_health").get("instance") == config["instance"]
                )
            except Exception:
                pass
            healthy, alive = sample_health(liveness, readiness, frontend, failures)
            backend_was_alive = backend_was_alive or alive["backend"]
            started = {name: status[name + "_started"] for name in commands}
            for name in recovery_decision(
                failures, started, time.time(), backend_grace=35 if backend_was_alive else 120
            ):
                if name == "backend" and failures["mcp"] >= 6:
                    restarts["mcp"] += 1
                    failures["mcp"] = 0
                stop_process(processes[name])
                restarts[name] += 1
                start(name)
                healthy[name] = False
                alive[name] = False
                if name == "backend":
                    healthy["mcp"] = False
            phase = "ready" if all(healthy.values()) else "degraded" if all(alive.values()) else "recovering"
            status.update(status=phase, healthy=healthy, liveness=alive, checked_at=time.time())
            save_state(status)
            for _ in range(20):
                if STOPPING:
                    break
                time.sleep(0.25)
    finally:
        for process in processes.values():
            stop_process(process)
        status.update(status="stopped", checked_at=time.time())
        save_state(status)
        for handle in handles.values():
            handle.close()


if __name__ == "__main__":
    main()
