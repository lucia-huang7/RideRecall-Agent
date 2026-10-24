"""Offline verified snapshots. Stop the single-node application before copying state."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from ride_agent.persistence.runtime_lock import embedded_writer_lock


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def files(root):
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Snapshot paths must not contain symbolic links")
        if path.is_file():
            result.append(path)
        elif not path.is_dir():
            raise ValueError("Only regular files and directories are supported")
    return result


def check_database(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Database integrity verification failed")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Database foreign-key verification failed")
        return dict(
            schema_version=db.execute("PRAGMA user_version").fetchone()[0],
            cases=db.execute("SELECT COUNT(*) FROM cases").fetchone()[0],
            receipts=db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0],
        )


def private_tree(root):
    if os.name != "posix":
        return
    root.chmod(448)
    for path in root.rglob("*"):
        path.chmod(448 if path.is_dir() else 384)


def snapshot(database, destination):
    source = Path(database).resolve()
    target = Path(destination).resolve()
    if not source.is_file() or source.is_symlink():
        raise ValueError("An existing regular case database is required")
    for suffix in (".vectors", ".skills"):
        if target.is_relative_to(source.with_suffix(suffix)):
            raise ValueError("Choose a destination outside application state")
    if target.exists():
        raise FileExistsError("Snapshot destination must not already exist")
    with embedded_writer_lock(source):
        target.mkdir(parents=True, mode=448)
        try:
            with (
                closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as origin,
                closing(sqlite3.connect(target / "state.sqlite3")) as copy,
            ):
                origin.backup(copy)
                if copy.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                    raise ValueError("Snapshot did not leave WAL mode")
            for suffix in ("-wal", "-shm"):
                (target / ("state.sqlite3" + suffix)).unlink(missing_ok=True)
            for suffix, name in (
                (".vectors", "vectors"),
                (".skills", "skills"),
                (".baseline.json", "baseline.json"),
            ):
                path = source.with_suffix(suffix)
                if not path.exists():
                    continue
                if path.is_symlink():
                    raise ValueError("Application sidecars must not be symbolic links")
                if path.is_dir():
                    files(path)
                    shutil.copytree(path, target / name)
                else:
                    shutil.copy2(path, target / name)
            inventory = {
                str(path.relative_to(target)): dict(sha256=digest(path), bytes=path.stat().st_size)
                for path in files(target)
            }
            manifest = dict(
                format_version=1,
                created_at=datetime.now(timezone.utc).isoformat(),
                database=check_database(target / "state.sqlite3"),
                files=inventory,
            )
            (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            private_tree(target)
            return manifest
        except BaseException:
            shutil.rmtree(target)
            raise


def verify(directory):
    root = Path(directory).absolute()
    paths = files(root)
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("format_version") != 1:
        raise ValueError("Unsupported snapshot format")
    actual = {str(path.relative_to(root)) for path in paths if path != root / "manifest.json"}
    expected = manifest.get("files", {})
    if actual != set(expected) or "state.sqlite3" not in expected:
        raise ValueError("Snapshot inventory does not match its manifest")
    for relative, record in expected.items():
        path = root / relative
        if path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
            raise ValueError("Snapshot file checksum mismatch")
        if relative.split("/")[0] not in (
            "state.sqlite3",
            "vectors",
            "skills",
            "baseline.json",
        ):
            raise ValueError("Unsupported snapshot state path")
    if check_database(root / "state.sqlite3") != manifest["database"]:
        raise ValueError("Snapshot database summary mismatch")
    return manifest


def restore(directory, destination):
    root = Path(directory).absolute()
    target = Path(destination).absolute()
    if target.exists():
        raise FileExistsError("Restore requires a fresh directory; it never overwrites existing state")
    manifest = verify(root)
    target.mkdir(parents=True, mode=448)
    names = {
        "vectors": "state.vectors",
        "skills": "state.skills",
        "baseline.json": "state.baseline.json",
    }
    try:
        for source in (
            root / "state.sqlite3",
            root / "vectors",
            root / "skills",
            root / "baseline.json",
        ):
            if not source.exists():
                continue
            name = names.get(source.name, source.name)
            if source.is_dir():
                shutil.copytree(source, target / name)
            else:
                shutil.copy2(source, target / name)
        for relative, record in manifest["files"].items():
            parts = Path(relative).parts
            if digest(target.joinpath(names.get(parts[0], parts[0]), *parts[1:])) != record["sha256"]:
                raise ValueError("Restored file checksum mismatch")
        private_tree(target)
        return target / "state.sqlite3"
    except BaseException:
        shutil.rmtree(target)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    take = sub.add_parser("create")
    take.add_argument("database")
    take.add_argument("destination")
    check = sub.add_parser("verify")
    check.add_argument("snapshot")
    recover = sub.add_parser("restore")
    recover.add_argument("snapshot")
    recover.add_argument("destination")
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = snapshot(args.database, args.destination)
        elif args.command == "verify":
            result = verify(args.snapshot)
        else:
            result = dict(database=str(restore(args.snapshot, args.destination)))
        print(json.dumps(result, indent=2))
    except (
        OSError,
        ValueError,
        RuntimeError,
        sqlite3.Error,
        KeyError,
        TypeError,
    ) as error:
        parser.exit(1, "Snapshot operation failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
