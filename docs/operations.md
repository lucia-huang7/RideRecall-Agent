# Operations

## Run

On macOS, double-click `scripts/macos/Start RideRecall-Agent.command`, or run:

```bash
bash scripts/dev/one-click.sh start --no-open
bash scripts/dev/one-click.sh status
bash scripts/dev/one-click.sh stop
```

Open [Trips](http://127.0.0.1:5175/#/trips), [Help](http://127.0.0.1:5175/#/help) or [Account](http://127.0.0.1:5175/#/account). First setup needs Python 3.11/3.12, Node.js compatible with Vite 7, and dependency-registry access. The background installation supervises the frontend, backend and separate stdio MCP process. The Mac must remain awake and logged in. Run Start again after source updates; saved cases are retained.

For foreground development, stop the background installation, then run `bash scripts/dev/run-rides-dev.sh`.

## Use

Select a trip, describe a dispute, add evidence, review a proposed simulated adjustment or request a specialist. A specialist can read the existing history, accept the case and record a decision.

**Support approach** on Account, new-case and conversation screens selects the next analysis. Single agent is the default; multi-agent is optional. Both keep durable context. An explicit browser preference survives reload. Case records and execution traces show the selected mode.

API clients can pass `agent_mode: "single"` or `"multi"` on case creation or a `message` action. Omitting it defaults new/older cases to single and preserves an existing explicit case choice on follow-ups. State changes use request IDs and case revisions; model text cannot execute payments.

## Develop

```bash
python -m pip install --require-hashes -r backend/requirements.txt
python -m pip install -r backend/requirements-dev.txt
(cd backend && python -m pytest tests -q -W error)
(cd frontend && npm ci && npm test && npm run lint && npm run build)
```

Replay the supplied synthetic data through the real API pipeline:

```bash
cd backend
python -m ride_agent.evaluation.simulation audit --dataset ../datasets/ride_support_v1
python -m ride_agent.evaluation.simulation replay --dataset ../datasets/ride_support_v1 --output ../artifacts/my-replay
```

Replay refuses an existing output directory. Results, databases and credentials are ignored by Git.

## Optional authenticated and container previews

```bash
python scripts/ops/provision-auth-preview.py
bash scripts/ops/run-authenticated-demo.sh
```

Open `http://127.0.0.1:8012`. Generated fictional credentials are stored in the owner-only, ignored `.runtime/auth-preview/access-credentials.json`. They expire after 24 hours; browser sign-in holds them in memory only. For rotation, provision a new `--output` directory and pass it to the run script; existing registries are not overwritten.

With Docker Engine and Compose, `bash scripts/ops/container-demo.sh` starts a separate authenticated preview on `http://127.0.0.1:8013`. Read the required account credential locally:

```bash
docker compose --profile tools run --rm --no-deps init python -c \
  'import json; print(json.load(open("/config/profile/access-credentials.json"))["credentials"]["preview-rider"])'
```

Other preview accounts are `preview-specialist` and `preview-operator`. Keep credentials out of screenshots, URLs and source control. Container rotation requires a newly provisioned configuration directory selected through a Compose override. `docker compose stop app` preserves state; removing volumes deletes it.

## Runtime boundaries

Demo identities are role-play controls. Keep demo mode on loopback. The authenticated preview enforces ownership and staff roles, but is not managed identity or OIDC. Storage is single-node SQLite with embedded Chroma; use one worker per state namespace. Real deployments still need TLS, identity lifecycle management, upload scanning, dependency review and offsite recovery.

`/livez` checks the process; `/readyz` also checks storage, policy indexes, projections and actual MCP access. Local data lives under `.runtime/`; the persistent macOS installation uses `~/Library/Application Support/RideRecall-Agent/<instance>/`. Use `python -m ride_agent.persistence.backup --help` from `backend/` for offline backup/restore commands and stop the relevant instance before copying embedded state.

For live-model access, set `RIDERECALL_AGENT_RIDE_MODEL_MODE=live`, `RIDERECALL_AGENT_RIDE_MODEL` and `RIDERECALL_AGENT_RIDE_API_KEY` explicitly. The default is no-key `demo`.

## Reproduce the offline comparison

The published comparison uses 1,000 synthetic worlds and 2,000 dialogues per condition. It measures workflow completion and claim retention with the deterministic adapter. Live-model results and independent human dataset review are pending. Use the backend environment, including test dependencies:

```bash
python scripts/bench/run-ablation.py --output artifacts/my-ablation
python scripts/ops/check-evaluation-evidence.py
```

The runner records source and dataset hashes before execution, refuses existing output directories, preserves per-condition logs, scores and compressed HTTP responses, and verifies all 2,000 episode IDs in every condition. The default runs conditions sequentially. `--workers 4` runs the conditions concurrently; report that contention when presenting latency. The published evidence gate checks the curated baseline against the shipped manifest and split counts; it does not rerun the experiment.

Offline replay always uses the deterministic adapter. To evaluate a live model, configure the provider variables and run:

```bash
python scripts/bench/check-live.py --output artifacts/my-live-test \
  --max-calls "$CALL_LIMIT" \
  --input-usd-per-million "$INPUT_PRICE" \
  --output-usd-per-million "$OUTPUT_PRICE" \
  --pricing-date "$PRICING_DATE"
```

Choose the model and verify its current input/output prices before running. The harness requires live mode, model and API key and uses the same HTTP replay and scorer. It records every provider attempt, token usage, context loss, dialogue timing, strict completion and test coverage. `--limit` is a smoke run, not a full test result. The call ceiling is not a guaranteed dollar budget. Unknown failed-request usage or unpriced cache tokens make the total cost unavailable; estimates are not provider invoices. Raw responses may contain provider content and remain ignored locally.

## Storage upgrades

The local embedding uses 256-dimensional hash vectors, cosine policy indexes and L2 memory indexes. It requires no embedding service or model download.

Before upgrading an existing 0.5.x installation, stop it and create an offline snapshot with the existing environment:

```bash
bash scripts/dev/one-click.sh stop
# From backend/, using the existing Python environment:
python -m ride_agent.persistence.backup create /absolute/path/to/rides.sqlite3 /absolute/path/to/new-snapshot
python -m ride_agent.persistence.backup verify /absolute/path/to/new-snapshot
```

The snapshot includes the case database, receipts, binary evidence and derived sidecars. Chroma may migrate its index format on first open; keep the snapshot and the original dependency lock if rollback is needed. Do not open an upgraded index with the old Chroma package. Restart with the one-click command after upgrading.

The isolated Linux container regression can be reproduced with:

```bash
docker compose build app
python scripts/ops/check-container.py --output artifacts/my-container-check.json
```

It uses a separate Compose project and removes only its own containers and volumes. It checks specialist decisions, rider confirmation, restart persistence and offline recovery.

## Reliability workloads

`scripts/bench/check-capacity.py --help` describes the bounded authenticated HTTP concurrency workload; `scripts/bench/check-soak.py --help` describes the paced restart/MCP-fault workload. Both operate on an explicit frozen checkout and isolated state, preserve failures and refuse an existing output directory. `scripts/ops/check-local-recovery.py` checks the installed macOS service without discarding saved cases.
