# kinder-uber

**Explain it once. Your support case remembers.**

A mobile ride-support workspace that keeps the trip, payment records, evidence, corrections and specialist review in one continuous case. Run the complete local workflow without a model API key.

Independent software prototype. Not affiliated with Uber. All supplied trips, policies and financial actions are fictional.

![Monochrome mobile rider workspace](docs/assets/mobile-trips.png)

## What works

- Rider flow: select a trip, describe a dispute, attach evidence, correct details, review a proposed adjustment or request a specialist.
- Case context: source-backed rider assertions, explicit withdrawn claims, clarification of ambiguous corrections and durable case history.
- Inspectable execution: intent routing, specialist roles, policy retrieval, guarded read-only tools and a real independent stdio MCP service.
- Specialist desk: accept a handoff, read the existing context, reply, propose a simulated adjustment and record an outcome.
- Consistent commands: version-aware case updates, exact replay receipts and crash-recoverable memory projections. A slow analysis cannot silently overwrite a newer specialist update.
- Operations: execution traces, metrics, durable knowledge/Skill releases, bounded inference admission, dependency checks and regression evaluation.
- Recovery: immutable deduplicated evidence, compressed replay receipts and verified offline snapshot/restore.
- Black-and-white mobile interface with optional Rainbow Mode; English interface and new code.

The default model adapter is deterministic. The pipeline actually runs, but offline results do not establish live-model reasoning quality or a multi-agent advantage.

## Start

On macOS, double-click **Start kinder-uber.command**, or run:

```bash
bash scripts/one-click.sh start --no-open
bash scripts/one-click.sh status
bash scripts/one-click.sh stop
```

Open [Trips](http://127.0.0.1:5175/#/trips), [Agent desk](http://127.0.0.1:5175/#/agent) or [Control room](http://127.0.0.1:5175/#/manage). The background supervisor checks the frontend, Python backend and independent MCP process. macOS must remain awake with the user logged in.

First setup requires Python 3.11/3.12, Node.js compatible with Vite 7 and access to dependency registries. No Docker, Redis server or model provider is required for the local demo.

For an authenticated, non-root container preview, run `bash scripts/container-demo.sh`. See [deployment and verification](docs/DEPLOYMENT.md) for isolated workflow checks and configuration.

For foreground development, stop the background service first:

```bash
bash scripts/run-rides.sh --dev
```

## Repository map

```text
backend/
  mobility/             Active ride API, case rules, storage and execution adapters
    app.py              FastAPI routes and application lifecycle
    service.py          Case workflow and guarded financial proposals
    engine.py           Intent, roles, retrieval, context and tool execution
    catalog.py          Shared typed trip/payment/policy contract
    security.py         Scoped identities, ownership and demo isolation
    readiness.py        Dependency-aware readiness
    http_limits.py      Body size/timeout limits before deserialization
    persistence.py      Schema migrations, compressed receipts and binary blobs
    backup.py           Offline verified snapshots and restore
    case_index.py       Transactional summaries and account-bound pagination
    configuration.py    Durable configuration releases and analysis/read-write gate
    admission.py        Bounded analysis capacity and deadlines
    projections.py      Durable memory projection worker
    runtime_lock.py     Embedded storage writer exclusion
    settings.py         Validated model-provider configuration
    diagnostics.py      Isolated transactional evaluation fixtures
    memory.py           Durable context adapter
    claims.py           Source-backed claim handling
    corrections.py      Correction resolution and ambiguity handling
    operations.py       Monitoring, skill/knowledge controls and MCP lifecycle
    mcp_demo_server.py  Independent read-only trip-record service
    simulation/         Dataset generation, reference oracle, audit and API replay
  agents/               Role orchestration
  core/                 Intent routing and policy retrieval
  memory/               Context storage primitives
  tooling/              Tool registry, execution guards and MCP transport
  monitor/              Metrics and tracing
  evaluation/           Evaluation utilities
  skills/               Task instructions used by the execution engine
  tests/                Unit, regression and integration checks
frontend/
  src/App.vue           Profile bootstrap and memory-only credential sign-in
  src/RideSupport.vue   Mobile rider and specialist experience
  src/OperationsPanel.vue
  src/lib/rideApi.js    HTTP client and retry identifiers
  src/ride.css          Shared monochrome and rainbow styles
  tests/                Browser-client unit tests
legacy-java/
  src/                  Separate Java 21 service and tests; outside the ride API path
  pom.xml
scripts/
  rides-control.py      Persistent macOS installation and control
  runtime/              Process supervisor and same-origin static/API server
  run-synthetic-demo.sh Opt-in generated-trip preview with isolated storage
datasets/
  ride_support_v1/      Runtime catalog, replay inputs and separate scorer labels
docs/
  ARCHITECTURE.md       Request path, module ownership and boundaries
  DATASETS.md           Dataset contract and reproduction commands
CHANGELOG.md            Release changes
```

## Data and evaluation

The synthetic generator creates 1,000 fictional event worlds and two dialogue variants per world. Variants stay in the same development/calibration/test split. Runtime catalog data and scorer-only reference answers are separate files. API tools, policy retrieval and the independent MCP process consume the same validated catalog.

```bash
cd backend
python -m mobility.simulation audit --dataset ../datasets/ride_support_v1
python -m mobility.simulation replay --dataset ../datasets/ride_support_v1 --output ../artifacts/my-replay --limit 16
```

From the root, `bash scripts/run-synthetic-demo.sh` opens the existing UI against generated trips on port 8011 using a separate database. Keep that terminal open. See [dataset details](docs/DATASETS.md).

Generated outcomes are reference-state checks, not human-reviewed ground truth or production accuracy. Historical measurements remain preserved outside this repository. New runs belong to their actual source version and are saved in ignored `artifacts/` directories.

### Full replay result — 2026-10-22

Frozen source `e1b30c2` executed **1,000 synthetic worlds / 2,000 dialogues / 27,772 API requests** without an API key. Specified mechanical checks passed for development **1,212/1,212**, calibration **438/438** and test **350/350**; zero episode exceptions. Wall time was **948.892 seconds**. All episode IDs, split isolation, raw-log counts and frozen source/data hashes were verified.

This completes seed `20261022`, whose test split had already been inspected. It is not a fresh blind test, human-reviewed accuracy or evidence of a multi-agent quality advantage. See automated checks.

## Verify

Install the hashed runtime and test-only requirements in separate commands:

```bash
python -m pip install -r backend/requirements.txt
python -m pip install -r backend/requirements-dev.txt
```

Then run:

```bash
cd backend
python -m pytest tests -q
```

```bash
cd frontend
npm ci
npm run lint
npm test
npm run build
```

```bash
cd legacy-java
# JAVA_HOME must point to JDK 21 or later.
./mvnw clean test
```

For the complete Java transport/storage checks, install `redis-server`, use the backend Python environment and JDK 21, then run `python scripts/check-java.py --with-chroma` from the root. It starts disposable loopback dependencies and stops them afterward; no model key is used.

## Deployment boundaries

The current default is a **single-node local demonstration**: SQLite, embedded retrieval and simulated payment actions. Demo session headers and the public specialist credential are not production authentication. Keep demo mode on loopback. An optional authenticated profile enforces rider ownership and distinct specialist/operator roles.

Local runtime data is under `.runtime/`. The macOS background installation uses `~/Library/Application Support/kinder-uber/<instance>/` for its copied application, database, logs and environment. Run Start again after source updates. Do not commit credentials, local databases, dependency caches or generated build output.
