# Deployment and verification

## Authenticated container preview

The container serves the built Vue mobile app and Python API on one origin. The MCP trip-record server is a separate stdio child process. This is a single-node, one-worker deployment with embedded persistence. The supplied Compose profile uses fictional identities/catalogs and simulated financial actions; it needs no model API key.

With Docker Engine and Compose available, run:

```sh
bash scripts/container-demo.sh
```

Open `http://127.0.0.1:8013`. The first run builds digest-pinned base images, installs the hashed Python dependency lock, creates an owner-only identity registry and starts the app after dependency health checks. Persistent case state and configuration live in separate named volumes. The service runs as UID 10001 with a read-only root filesystem, dropped capabilities and temporary scratch space. Only the loopback host port is published. Docker startup does not depend on the macOS launcher.

The generated preview credentials expire in 24 hours. Read the desired credential locally, then paste it into the sign-in form; it is held only in page memory. Never put it in an issue, screenshot, URL or repository:

```sh
docker compose --profile tools run --rm --no-deps init python -c \
  'import json; print(json.load(open("/config/profile/access-credentials.json"))["credentials"]["preview-rider"])'
```

The other distinct accounts are `preview-specialist` and `preview-operator`. The macOS installer also fingerprints the frontend package/lock files and reinstalls dependencies when they change, even if node_modules already exists. Dependency installation has bounded registry retries and does not depend on the advisory endpoint; audit is a separate check.

Stop/start preserves state:

```sh
docker compose stop app
docker compose up -d --wait app
docker compose logs --tail=50 app
```

Do not run `down --volumes` against an instance you want to keep. Preview credential rotation requires provisioning a new identity directory and explicitly selecting it in a reviewed Compose override; the helper deliberately refuses to overwrite an existing registry. Production should use managed identity issuance, revocation and delivery rather than this short-lived preview helper.

## Image verification

After building, run:

```sh
python scripts/check-container.py --output artifacts/container-check.json
```

The verifier creates a uniquely named temporary Compose project, selects an available loopback port and removes only its own resources. It exercises the real authenticated HTTP workflow, role rejection, evidence bytes, handoff, guarded proposal, explicit confirmation and exact replay. It then restarts the service and checks state/receipt/blob persistence, verifies non-root file ownership, stops the app, and performs an offline snapshot/restore through separate containers sharing that test volume. Credentials never appear in its report.

## Production configuration

The image defaults to the authenticated profile and refuses startup without an explicit identity registry and owned catalog. Mount reviewed configuration read-only under `/config`, persistent writable state under `/data`, and supply `KINDER_UBER_IDENTITY_FILE` and `KINDER_UBER_RIDE_CATALOG_PATH`. Every trip must belong to a configured rider. Use the documented opaque-token profile only within its scope; it is not an OIDC implementation.

Keep one worker per database/index namespace. Do not share embedded state between replicas. Put externally exposed deployments behind a correctly configured TLS/authentication gateway with deployment-specific rate limits and resource budgets. Proxy headers are disabled in the image, so a caller cannot spoof loopback identity through forwarded headers. Financial execution remains simulated, including in the authenticated profile. Live-model configuration is separate and never grants payment permissions.

`/livez` tests the process; `/readyz` checks database schema, policy index, memory projections and an actual MCP read. A dependency failure makes readiness fail without deleting source state. See [state and recovery](STATE_AND_RECOVERY.md) before upgrades and automated checks before choosing exposure boundaries. Backups need independent offsite storage and a deployment-specific recovery policy.

## CI

`.github/workflows/verify.yml` is manual (`workflow_dispatch`) to avoid unexpected hosted runs in a private repository. It installs locked runtime dependencies, runs Python/frontend/Java checks, and optionally builds and exercises the container. Actions are pinned by commit SHA and granted read-only repository access. The same commands can be run locally without hosted CI charges. A workflow definition is not evidence that GitHub Actions has run; local execution results are recorded separately.


## Local recovery and diagnostics

On a quiescent macOS no-key demo, `python scripts/check-local-recovery.py --output artifacts/local-recovery.json` verifies this repository's installation ownership and demo mode, kills only its owned backend/frontend/MCP one at a time, waits for actual health and compares source case/receipt/evidence/index hashes. Do not run fault injection against a real deployment or while another user is editing the demo. Successful recovery still has a measured outage; it is not zero-downtime availability.

The supervisor uses liveness for process restart decisions and readiness for service availability. A retrying configuration release or database dependency failure does not itself cause a restart storm. Known persistent MCP failure can restart its owner after grace/threshold checks. Backend/frontend output is drained through the supervisor into three bounded files per component (10 MiB each), inside owner-only application/log directories. Container logs use the same 10 MiB/three-file retention budget through the JSON-file driver.

The installed local static proxy admits at most sixteen connection handlers and returns a retryable JSON 503 when full. API uploads require one valid Content-Length, at most 4 MiB, and complete acquisition within fifteen seconds even if small chunks continue arriving. Duplicate/invalid lengths and incomplete bodies are rejected before forwarding. Chunked uploads receive 501 on this local proxy; the direct FastAPI request boundary separately supports bounded chunked bodies. Upstream responses stream in 64 KiB reads, preserving binary bytes and response metadata without buffering the entire response. Socket/parser failures use sanitized log categories. These are local proxy limits, separate from the backend's four active rider analyses and its HTTP connection budget.

Protected metrics include bounded-label HTTP counts, preparation latency and active requests, with standard Linux process/GC resource metrics where supported. Correlation IDs identify one HTTP attempt; they are not command idempotency keys. They are never metric labels. The deterministic adapter retains only its latest 512 metadata-only call stamps while a separate lifetime attempt count continues to increase. Runtime counters reset on restart; canonical business records and replay receipts persist.
