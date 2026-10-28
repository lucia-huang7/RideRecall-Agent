"""Run the unchanged HTTP replay/scorer with an explicit live provider and usage accounting."""

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def main():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "backend"))
    from anthropic import AsyncAnthropic
    from ride_agent.evaluation.simulation import replay as replay_module
    from ride_agent.evaluation.simulation.replay import replay
    from ride_agent.integrations.settings import ModelSettings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=root / "datasets/ride_support_v1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--input-usd-per-million", type=float, required=True)
    parser.add_argument("--output-usd-per-million", type=float, required=True)
    parser.add_argument("--pricing-date", required=True)
    args = parser.parse_args()
    prices = (args.input_usd_per_million, args.output_usd_per_million)
    if args.max_calls < 1 or any(not math.isfinite(p) or p < 0 for p in prices):
        parser.error("Call limit must be positive and prices nonnegative")
    if args.limit is not None and args.limit < 1:
        parser.error("Episode limit must be positive")
    if os.environ.get("RIDERECALL_AGENT_RIDE_MODEL_MODE") != "live":
        parser.error("Explicit live mode and locally configured provider credentials are required")
    settings = ModelSettings.from_environment()
    output = args.output.resolve()
    if output.exists():
        parser.error("Refusing to overwrite an evidence directory")
    output.mkdir(parents=True)
    dataset = args.dataset.resolve()
    source_paths = list((root / "backend/ride_agent").rglob("*.py")) + [Path(__file__)]
    frozen = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    manifest = json.loads((dataset / "manifest.json").read_text())
    (output / "freeze.json").write_text(
        json.dumps({"source_sha256": frozen, "dataset_sha256": manifest["files"], "model": settings.model}, indent=2)
        + "\n"
    )
    records = []
    contexts = {}
    original_score = replay_module.score_case

    def score(case, label, step):
        checks = original_score(case, label, step)
        if "engine_status" in label:
            contexts[(case.get("owner"), case["engine_runs"][-1]["id"])] = {
                "eligible": bool(label.get("claims")) and not label["requires_clarification"],
                "lost": any(checks.get(k) is False for k in ("active_claims", "claim_answer_coverage")),
            }
        return checks

    attempted = 0
    exhausted = False

    class MeteredClient:
        def __init__(self, **kwargs):
            self.sdk = AsyncAnthropic(**kwargs)
            self.calls = []
            self.total_calls = 0
            self.messages = SimpleNamespace(create=self.create)

        async def create(self, **kwargs):
            nonlocal attempted, exhausted
            if attempted >= args.max_calls:
                exhausted = True
                raise RuntimeError("Live evaluation call limit reached")
            attempted += 1
            self.total_calls += 1
            call = {"sequence": attempted}
            self.calls.append(call)
            start = time.perf_counter()
            try:
                result = await self.sdk.messages.create(**kwargs)
                usage = result.usage
                call.update(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    succeeded=True,
                )
                return result
            except Exception as exc:
                call.update(succeeded=False, error_type=type(exc).__name__)
                raise
            finally:
                call["elapsed_ms"] = (time.perf_counter() - start) * 1000
                records.append(call)
                with (output / "provider-usage.jsonl").open("a") as handle:
                    handle.write(json.dumps(call) + "\n")

        async def close(self):
            await self.sdk.close()

    # Inject validated live settings rather than silently accepting replay's demo environment.
    # Neither model answers, case transitions, scenario steps nor scoring rules are patched.
    try:
        with (
            patch.object(ModelSettings, "from_environment", return_value=settings),
            patch("ride_agent.agent.pipeline.AsyncAnthropic", MeteredClient),
            patch.object(replay_module, "score_case", score),
        ):
            result = replay(dataset, output / "replay", limit=args.limit, split="test")
    finally:
        tokens = {
            name: sum(r.get(name, 0) for r in records)
            for name in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        }
        complete_usage = (
            bool(records)
            and all(r["succeeded"] for r in records)
            and not any(tokens[name] for name in ("cache_creation_input_tokens", "cache_read_input_tokens"))
        )
        cost = (
            tokens["input_tokens"] * args.input_usd_per_million + tokens["output_tokens"] * args.output_usd_per_million
        ) / 1e6
        evidence = {
            "mode": "live",
            "model": settings.model,
            "split": "test",
            "attempted_provider_calls": attempted,
            "call_limit_reached": exhausted,
            "usage": tokens,
            "estimated_cost_usd": cost if complete_usage else None,
            "pricing_date": args.pricing_date,
            "input_usd_per_million": args.input_usd_per_million,
            "output_usd_per_million": args.output_usd_per_million,
            "cost_note": (
                "Token-based estimate, not a provider invoice. "
                "Unknown usage or cache pricing makes total cost unavailable."
            ),
            "source_unchanged": all(
                hashlib.sha256((root / name).read_bytes()).hexdigest() == digest for name, digest in frozen.items()
            ),
        }
        (output / "live-usage.json").write_text(json.dumps(evidence, indent=2) + "\n")
    evidence["dialogues"] = result["episodes"]
    evidence["strict_completed"] = result["passed"]
    eligible = [c for c in contexts.values() if c["eligible"]]
    evidence["context_eligible_checkpoints"] = len(eligible)
    evidence["context_lost_checkpoints"] = sum(c["lost"] for c in eligible)
    evidence["context_loss_rate"] = sum(c["lost"] for c in eligible) / len(eligible) if eligible else None
    evidence["median_http_ms"] = result["median_http_ms"]
    scores = [json.loads(line) for line in (output / "replay/scores.jsonl").read_text().splitlines()]
    evidence["median_dialogue_ms"] = statistics.median(row["elapsed_ms"] for row in scores) if scores else None
    scenarios = [json.loads(line) for line in (dataset / "scenarios.jsonl").read_text().splitlines()]
    evidence["full_test_coverage"] = len(scores) == sum(row["split"] == "test" for row in scenarios)
    result["mode"] = "Actual FastAPI pipeline with a live Messages provider; unchanged mechanical scorer"
    result["limitations"] = [
        "Synthetic data and mechanical checks, not human judgments of answer quality.",
        "HTTP/dialogue timings include hosted inference; provider latency is recorded separately.",
        "Call-count ceiling limits requests, not a guaranteed dollar budget.",
        "Failed requests may have unknown provider charges. No real payments are executed.",
    ]
    (output / "replay/summary.json").write_text(json.dumps(result, indent=2) + "\n")
    evidence["estimated_cost_per_dialogue_usd"] = (
        evidence["estimated_cost_usd"] / result["episodes"] if complete_usage and result["episodes"] else None
    )
    (output / "live-usage.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2))
    if exhausted or not evidence["source_unchanged"]:
        raise SystemExit("Incomplete or invalid run; retain all evidence and do not publish it as a full test result")


if __name__ == "__main__":
    main()
