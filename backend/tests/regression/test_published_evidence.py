"""Published claims must refer to the dataset readers actually receive."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("published_evidence", ROOT / "scripts/ops/check-evaluation-evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def test_shipped_evidence_matches_shipped_dataset():
    result = evidence.verify(ROOT / "datasets/ride_support")
    assert result["worlds"] == 1000
    assert result["dialogues"] == 2000
    assert result["splits"]["test"] == 404


@pytest.mark.parametrize("corruption", ["dataset_hash", "test_count"])
def test_rejects_stale_dataset_or_split_counts(tmp_path, corruption):
    dataset = ROOT / "datasets/ride_support"
    for path in dataset.iterdir():
        if path.is_file() and path.name != "baseline.json":
            (tmp_path / path.name).symlink_to(path)
    baseline = json.loads((dataset / "baseline.json").read_text())
    if corruption == "dataset_hash":
        baseline["dataset_sha256"]["scenarios.jsonl"] = "0" * 64
    else:
        baseline["conditions"]["single_durable"]["splits"]["test"]["dialogues"] = 350
    (tmp_path / "baseline.json").write_text(json.dumps(baseline))
    with pytest.raises(ValueError, match="different dataset|Incorrect split coverage"):
        evidence.verify(tmp_path)
