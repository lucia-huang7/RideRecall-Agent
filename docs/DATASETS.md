# Synthetic dataset contract

The workload uses the existing HTTP API, engine, context, retrieval, tools and independent MCP process. It is not a separate model benchmark.

| File | Consumer | Purpose |
|---|---|---|
| catalog.json | Application and MCP | Trips, payment records and scoped/versioned policies |
| catalog-preview.json | Application and MCP | Same contract, smaller UI preview |
| catalog.schema.json | Validation tools | JSON Schema, including forbidden extra fields |
| scenarios.jsonl | Replay driver | Ordered HTTP workflow steps |
| world-events.jsonl | Reference evaluation | Event worlds and transformation metadata |
| oracle.jsonl | Scorer only | Expected states, claims, amounts and checkpoint outcomes |
| human-review.jsonl | Future independent reviewer | Explicitly pending review samples |
| manifest.json | Audit/replay | Seed, grouped split sizes, file/source hashes and limitations |

Only the catalog reaches the running application. It derives eligibility from actual payment totals and policy limits rather than consuming an expected-outcome field.

Eight families cover pickup disputes, eligible fare differences, over-limit fare differences, pending authorizations, duplicate settlements, mismatched-trip evidence, real MCP disconnection/reconnection and no-positive-excess explanations. Variants include corrections, hypotheticals, ambiguity and background conversation gaps. Related world variants never cross dataset splits.

The reference oracle is separately implemented without importing application business rules or claim parsing. It is not independently human-reviewed truth. Mismatched evidence remains unverified; the workload does not establish automated fraud detection.

## Commands

Run with a Python environment containing the demo and test dependencies:

```bash
cd backend
python -m mobility.simulation generate --output ../datasets/my_release --worlds 1000 --seed 20261021
python -m mobility.simulation audit --dataset ../datasets/my_release
python -m mobility.simulation replay --dataset ../datasets/my_release --output ../artifacts/my_replay
python -m mobility.simulation replay --dataset ../datasets/my_release --output ../artifacts/test_replay --split test
```

Existing release/result directories are never overwritten. Replay validates file hashes, creates disposable persistence, executes actual FastAPI requests and saves full gzip JSONL responses, checkpoint scores and a summary. Completed episodes are purged from that isolated database after raw responses are flushed; use `--retain-db` for small inspected runs.

From the root, run `bash scripts/run-synthetic-demo.sh` and open `http://127.0.0.1:8011/#/trips`. The existing mobile UI, API and MCP use the same preview catalog. `RIDE_PYTHON` chooses an environment, `PORT` changes the port. Keep the terminal open.

## Interpretation

Pass rates describe specified mechanical checks under generated inputs. Serial HTTP timing excludes live-model inference and does not measure concurrent production capacity. These workloads cannot establish real-user satisfaction, broad language understanding, live-model agent advantage or payment-system safety. Human review is pending.
