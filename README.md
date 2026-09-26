# LLM-Agent-System: Stateful Ride-Support Agent

[![Verify](https://github.com/lucia-huang7/LLM-Agent-System/actions/workflows/verify.yml/badge.svg)](https://github.com/lucia-huang7/LLM-Agent-System/actions/workflows/verify.yml)

A support agent where rider claims stay separate from recorded trip facts, policy answers carry retrieved evidence, and model text cannot execute an adjustment.

**Deterministic offline evaluation:** 1,000 synthetic worlds · 2,000 dialogues per condition · 67.0% → 100.0% strict completion with durable memory. **Live-model results: not measured.**

![Mobile ride-support workspace](docs/assets/mobile-trips.png)

## Why this is hard

A rider can correct an earlier statement, contradict a recorded pickup, and request a refund in the same conversation. A useful support system must preserve the correction, distinguish claims from facts, retrieve the applicable policy, and hand the case to a specialist without losing its history.

## Architecture

```mermaid
flowchart LR
    UI[Vue mobile views] --> API[FastAPI routes]
    API --> P[Case pipeline]
    P --> I[Intent recognition]
    I --> O[Single / optional multi-agent]
    O --> T[Read-only tools]
    T --> R[Policy retrieval · Chroma]
    T --> M[Independent MCP service]
    O --> V[Evidence validation]
    V --> S[SQLite case + projection outbox]
```

The [API](backend/ride_agent/api/app.py) assembles middleware, lifespan management and route groups. The [pipeline](backend/ride_agent/agent/pipeline.py) loads case context, recognizes intent, builds scoped tools, orchestrates the analysis, validates evidence and prepares durable memory effects. [Case transitions](backend/ride_agent/domain/case.py) apply separate server-controlled commands; analysis never grants financial authority.

The [claim ledger](backend/ride_agent/domain/claims.py) records statement versions and corrections independently of trip records. [Memory](backend/ride_agent/agent/memory.py) restores the case snapshot and derives source-backed context. [Persistence](backend/ride_agent/persistence/store.py) commits case changes and projection work together; Chroma indexes remain derived state.

## Key design decisions

| Decision | Reason | Implementation |
|---|---|---|
| Separate rider claims and recorded facts | A corrected statement must not silently overwrite verified trip data | [Claim ledger](backend/ride_agent/domain/claims.py) |
| Require indexed policy anchors | Missing payment or topic evidence downgrades the analysis to partial | [Policy validation](backend/ride_agent/agent/pipeline.py), [case tools](backend/ride_agent/agent/case_tools.py) |
| Isolate adjustments from model output | Only a valid server proposal and explicit rider confirmation can change simulated payment state | [Case workflow](backend/ride_agent/domain/case.py) |
| Request IDs plus revisions and ETags | Retries must not duplicate an adjustment; stale writes must be rejected | [Case routes](backend/ride_agent/api/routes/cases.py), [store](backend/ride_agent/persistence/store.py) |
| Default to one agent with durable memory | Multiple agents produced no task-completion gain in the offline comparison | [Orchestrator](backend/ride_agent/agent/orchestrator.py) |
| Keep oracle labels outside agent inputs | Evaluation answers must not enter the production request pipeline | [Replay and oracle](backend/ride_agent/evaluation/simulation/) |

## Evaluation

The supplied dataset contains **1,000 synthetic worlds and 2,000 dialogue variants across eight failure families**. Both variants of a world remain in one development, calibration or test split. Replay sends requests through the real API; a separate oracle scores the resulting records.

These measurements were rerun on the **shipped dataset and frozen application/scorer source**. Each condition covers all 2,000 dialogues; the four conditions total 8,000 dialogue replays. [Results, exact dataset/source hashes, failures and per-split statistics](datasets/ride_support_v1/baseline.json).

The recent-window baseline reconstructs context from the last four rider-started rounds; durable conditions restore the case memory snapshot. The single-agent comparator has the union of read-only tools; multi-agent conditions use role-scoped routing.

The adapter is **deterministic and offline**: this experiment measures memory retention and workflow conformance, not an LLM's reasoning or answer quality. Four condition processes ran concurrently; dialogue times include resource contention and should not be used as an isolated performance ranking.

| Agent mode | Memory | Strict task completion | Context loss per eligible checkpoint | Median dialogue time |
|---|---|---:|---:|---:|
| Single | Recent 4 rider rounds | 1,340/2,000 · 67.00% | 34.09% | 460.6 ms |
| Multi | Recent 4 rider rounds | 1,340/2,000 · 67.00% | 34.09% | 504.7 ms |
| Single (default) | Durable | 2,000/2,000 · 100.00% | 0.00% | 452.8 ms |
| Multi (optional) | Durable | 2,000/2,000 · 100.00% | 0.00% | 492.0 ms |

| Split | Worlds / dialogues per condition | Single + recent 4 | Multi + recent 4 | Single + durable | Multi + durable |
|---|---:|---:|---:|---:|---:|
| Development | 581 / 1162 | 780/1162 | 780/1162 | 1162/1162 | 1162/1162 |
| Calibration | 217 / 434 | 274/434 | 274/434 | 434/434 | 434/434 |
| Test | 202 / 404 | 286/404 | 286/404 | 404/404 | 404/404 |

Multiple agents produced no completion improvement under either memory condition in this dataset. Single agent with durable memory remains the default; multi-agent is optional. Failed checks and example episode IDs are retained in the linked evidence.

**Live model: not evaluated.** No provider credentials were configured. Live completion, context loss, latency and cost therefore remain unavailable. An explicit [live evaluation harness](scripts/bench/check-live.py) uses the same HTTP workflow and mechanical scorer, records provider usage/failures and never silently substitutes demo answers. It has local failure-path regression coverage, which is not a live-model quality result.

Limitations:

- Synthetic fixtures and a deterministic adapter do not establish real-user accuracy, live-model reliability or universal policy grounding. Independent human review of the generated dataset is still pending.
- Business invariants passed in these runs, but this is not a general safety guarantee. No real payment was executed.
- SQLite and embedded Chroma target one state namespace and one worker; deployment needs managed identity, TLS, upload scanning and offsite recovery.

## Staff workspace

The Agent Desk receives the rider conversation, evidence and decision history for human review. The Control Room exposes live MCP connectivity, policy releases, tool metrics and pipeline checks.

![Control Room with the independent MCP service connected](docs/assets/control-room.png)

## Quickstart

Requires Python 3.11/3.12 and Node.js compatible with Vite 7. First setup downloads pinned dependencies.

```bash
git clone https://github.com/lucia-huang7/LLM-Agent-System.git
cd LLM-Agent-System
bash scripts/dev/one-click.sh start --no-open
```

Open [Trips](http://127.0.0.1:5175/#/trips). On macOS, double-click [Start LLM-Agent-System.command](scripts/macos/Start%20LLM-Agent-System.command). Frontend, backend and the independent MCP process run under the local supervisor. No model API key is required. See [operations](docs/operations.md) for stopping, development, authenticated previews, containers, backup and optional live-model configuration.

## Repository layout

```text
backend/ride_agent/  api · domain · agent · retrieval · integrations · persistence · ops · evaluation
backend/tests/       unit · integration · regression
datasets/            synthetic worlds, dialogue steps, separate oracle and recorded baseline
frontend/src/        Vue Router views, workspace composable and shared components/styles
scripts/             dev · ops · bench · macos
docs/                screenshot and operations guide
```

Java is preserved on the [`archive/java`](https://github.com/lucia-huang7/LLM-Agent-System/tree/archive/java/legacy-java) branch and does not serve this application.

Independent prototype; fictional trips and policies. Not affiliated with Uber.
