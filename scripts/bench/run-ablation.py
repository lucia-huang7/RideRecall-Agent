"""Freeze source/data hashes and run all offline ablations without overwriting evidence."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

CONFIGURATIONS = ("single_recent4", "multi_recent4", "single_durable", "multi_durable")


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=root / "datasets/ride_support_v1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=1)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Refusing to overwrite an existing evidence directory")
    dataset = args.dataset.resolve()
    tracked = subprocess.check_output(["git", "ls-files"], cwd=root, text=True).splitlines()

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    freeze = {
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "frozen_root": str(root),
        "dataset": str(dataset),
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "workers": args.workers,
        "source_sha256": {
            name: sha(root / name) for name in tracked if name.startswith(("backend/", "scripts/bench/"))
        },
        "dataset_sha256": {
            path.name: sha(path) for path in dataset.iterdir() if path.is_file() and path.name != "baseline.json"
        },
    }
    output.mkdir(parents=True)
    (output / "freeze.json").write_text(json.dumps(freeze, indent=2) + "\n")
    environment = dict(os.environ, PYTHONPATH=str(root / "backend"))

    def run(configuration):
        with (output / f"{configuration}.log").open("w") as log:
            result = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts/bench/check-ablation.py"),
                    "--dataset",
                    str(dataset),
                    "--output",
                    str(output / configuration),
                    "--configuration",
                    configuration,
                ],
                cwd=root,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
            )
        return configuration, result.returncode

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        codes = dict(pool.map(run, CONFIGURATIONS))
    (output / "exit-codes.json").write_text(json.dumps(codes, indent=2) + "\n")
    if any(codes.values()):
        raise SystemExit("A condition failed; inspect preserved logs and partial evidence")
    subprocess.run(
        [sys.executable, str(root / "scripts/bench/report-ablation.py"), "--run", str(output)],
        cwd=root,
        check=True,
    )


if __name__ == "__main__":
    main()
