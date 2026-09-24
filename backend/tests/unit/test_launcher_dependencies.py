"""A source update must not silently build against a stale frontend dependency tree."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[3] / "scripts/ops/rides-control.py"
spec = importlib.util.spec_from_file_location("rides_control", PATH)
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


def setup(tmp_path):
    frontend = tmp_path / "frontend"
    installation = tmp_path / "installation"
    frontend.mkdir()
    installation.mkdir()
    (frontend / "package.json").write_text('{"dependencies":{"vue":"3.5.44"}}')
    (frontend / "package-lock.json").write_text('{"lockfileVersion":3}')
    return (frontend, installation)


def test_dependency_lock_change_reinstalls_even_when_node_modules_exists(tmp_path, monkeypatch):
    frontend, installation = setup(tmp_path)
    calls = []

    def install(*args, **kwargs):
        calls.append(args)
        (frontend / "node_modules").mkdir(exist_ok=True)

    monkeypatch.setattr(control, "run", install)
    assert control.ensure_frontend_dependencies(frontend, installation)
    assert not control.ensure_frontend_dependencies(frontend, installation)
    (frontend / "package-lock.json").write_text('{"lockfileVersion":3,"packages":{"updated":true}}')
    assert control.ensure_frontend_dependencies(frontend, installation)
    assert len(calls) == 2


def test_failed_dependency_install_does_not_mark_the_new_lock_as_installed(tmp_path, monkeypatch):
    frontend, installation = setup(tmp_path)
    marker = installation / "frontend-dependencies.sha256"
    marker.write_text("last-successful-lock")

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(control, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        control.ensure_frontend_dependencies(frontend, installation)
    assert marker.read_text() == "last-successful-lock"


def test_missing_dependency_tree_is_recreated_despite_a_matching_marker(tmp_path, monkeypatch):
    frontend, installation = setup(tmp_path)
    calls = []

    def install(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(control, "run", install)
    assert control.ensure_frontend_dependencies(frontend, installation)
    assert control.ensure_frontend_dependencies(frontend, installation)
    assert len(calls) == 2
