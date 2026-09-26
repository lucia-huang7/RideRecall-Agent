"""Reject published evaluation evidence that does not match the shipped dataset."""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


def verify(dataset):
    dataset = Path(dataset)
    manifest = json.loads((dataset / "manifest.json").read_text())
    baseline = json.loads((dataset / "baseline.json").read_text())
    root = Path(__file__).resolve().parents[2]
    measured_sources = baseline.get("source_sha256", {})
    required_sources = {str(p.relative_to(root)) for p in (root / "backend/ride_agent").rglob("*.py")}
    if not required_sources.issubset(measured_sources):
        raise ValueError("Published baseline lacks complete runtime source hashes")
    for name, digest in measured_sources.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Measured source changed; rerun or withdraw the published baseline: {name}")
    expected = dict(manifest["files"])
    expected["manifest.json"] = hashlib.sha256((dataset / "manifest.json").read_bytes()).hexdigest()
    if baseline["dataset_sha256"] != expected:
        raise ValueError("Published baseline references a different dataset")
    for name, digest in expected.items():
        if hashlib.sha256((dataset / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Dataset integrity failure: {name}")
    scenarios = [json.loads(line) for line in (dataset / "scenarios.jsonl").read_text().splitlines()]
    worlds = defaultdict(set)
    for row in scenarios:
        worlds[row["world_id"]].add(row["split"])
    if len({row["id"] for row in scenarios}) != len(scenarios) or any(len(s) != 1 for s in worlds.values()):
        raise ValueError("Duplicate episodes or worlds leaking across splits")
    splits = Counter(row["split"] for row in scenarios)
    if set(baseline["conditions"]) != {"single_recent4", "multi_recent4", "single_durable", "multi_durable"}:
        raise ValueError("Published baseline must include all four conditions")
    for name, condition in baseline["conditions"].items():
        if condition["overall"]["dialogues"] != len(scenarios) or condition["overall"]["worlds"] != len(worlds):
            raise ValueError(f"Incomplete evaluation coverage: {name}")
        if set(condition["splits"]) != set(splits):
            raise ValueError(f"Incorrect split names: {name}")
        for split, count in splits.items():
            result = condition["splits"][split]
            world_count = sum(split in values for values in worlds.values())
            if result["dialogues"] != count or result["worlds"] != world_count:
                raise ValueError(f"Incorrect split coverage: {name}/{split}")
        for result in [condition["overall"], *condition["splits"].values()]:
            if result["strict_completion_rate"] != result["strict_completed"] / result["dialogues"]:
                raise ValueError(f"Invalid completion arithmetic: {name}")
        if sum(v["strict_completed"] for v in condition["splits"].values()) != condition["overall"]["strict_completed"]:
            raise ValueError(f"Split completion counts do not sum to the overall result: {name}")
    return {"worlds": len(worlds), "dialogues": len(scenarios), "splits": dict(splits), "hashes_match": True}


if __name__ == "__main__":
    print(json.dumps(verify(Path(__file__).resolve().parents[2] / "datasets/ride_support_v1"), indent=2))
