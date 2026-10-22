# LLM-Agent-System: Stateful Ride-Support Agent

[![Verify](https://github.com/lucia-huang7/LLM-Agent-System/actions/workflows/verify.yml/badge.svg)](https://github.com/lucia-huang7/LLM-Agent-System/actions/workflows/verify.yml)

A support agent where rider claims stay separate from recorded trip facts, policy answers carry retrieved evidence, and model text cannot execute an adjustment.

**Deterministic offline evaluation:** 1,000 synthetic worlds · 2,000 dialogues per condition · 67.0% → 100.0% strict completion with durable memory. **Live-model results: not measured.**

![Product showcase with three actual application screens in concept phone frames](docs/assets/device-showcase.jpg)

<sub>Actual app screenshots in illustrative device shells · [Editable HTML/CSS presentation](docs/assets/device-showcase.html)</sub>

## One dispute. No starting over.

A canceled ride, a $6.50 charge, a corrected pickup account, and a specialist who receives the history. These are actual mobile screenshots, presented in concept phone frames, from the offline demo: fictional data, a deterministic model adapter, and real local tool execution. The specialist is role-play; no real payment is made. The device shells and sequential story times are presentation artwork, not an official Apple model or evidence of a native iOS app. Click a panel for its original full-size screenshot.

| **01 · Choose the charged trip** | **02 · Explain what happened** |
|---|---|
| <a href="docs/assets/story/01-trips.jpg"><img src="docs/assets/devices/01-trips.jpg" alt="Choose the charged trip: A canceled River North ride still shows a $6.50 charge." width="400" /></a> | <a href="docs/assets/story/03-open-dispute.jpg"><img src="docs/assets/devices/03-open-dispute.jpg" alt="Explain what happened: The rider disputes the fee in their own words." width="400" /></a> |
| A canceled River North ride still shows a $6.50 charge. | The rider disputes the fee in their own words. |

| **03 · Correct the account** | **04 · Read the reason** |
|---|---|
| <a href="docs/assets/story/05-correction-chat.jpg"><img src="docs/assets/devices/05-correction-chat.jpg" alt="Correct the account: The rider changes north entrance to south entrance in the same conversation." width="400" /></a> | <a href="docs/assets/story/07-policy.jpg"><img src="docs/assets/devices/07-policy.jpg" alt="Read the reason: The fictional policy explains why conflicting accounts need human review." width="400" /></a> |
| The rider changes north entrance to south entrance in the same conversation. | The fictional policy explains why conflicting accounts need human review. |

| **05 · Pick up the whole case** | **06 · Propose a fair outcome** |
|---|---|
| <a href="docs/assets/story/09-agent-desk.jpg"><img src="docs/assets/devices/09-agent-desk.jpg" alt="Pick up the whole case: The simulated specialist receives the existing case and conversation." width="400" /></a> | <a href="docs/assets/story/10-specialist-decision.jpg"><img src="docs/assets/devices/10-specialist-decision.jpg" alt="Propose a fair outcome: The specialist acknowledges the correction and offers a $6.50 adjustment." width="400" /></a> |
| The simulated specialist receives the existing case and conversation. | The specialist acknowledges the correction and offers a $6.50 adjustment. |

| **07 · Keep the rider in control** | **08 · Close the loop** |
|---|---|
| <a href="docs/assets/story/11-confirm-adjustment.jpg"><img src="docs/assets/devices/11-confirm-adjustment.jpg" alt="Keep the rider in control: The proposal takes effect only after explicit rider confirmation." width="400" /></a> | <a href="docs/assets/story/12-resolution.jpg"><img src="docs/assets/devices/12-resolution.jpg" alt="Close the loop: A simulated adjustment is recorded; the history remains available." width="400" /></a> |
| The proposal takes effect only after explicit rider confirmation. | A simulated adjustment is recorded; the history remains available. |

<details>
<summary><strong>Explore every other screen — evidence, handoff, Help, settings, all five Control Room tabs, and support drawers</strong></summary>

The full collection contains 25 captures across all nine main views, the five operations tabs, support drawers and key workflow states. Captions describe observed behavior; console fixture results are not live-model quality measurements.

| **Read the trip record** | **Flag the conflict** |
|---|---|
| <a href="docs/assets/story/02-trip-record.jpg"><img src="docs/assets/devices/02-trip-record.jpg" alt="Read the trip record: The recorded pickup, destination and settled charge stay attached." width="400" /></a> | <a href="docs/assets/story/04-conflict-review.jpg"><img src="docs/assets/devices/04-conflict-review.jpg" alt="Flag the conflict: A recorded arrival does not prove the rider found the driver." width="400" /></a> |
| The recorded pickup, destination and settled charge stay attached. | A recorded arrival does not prove the rider found the driver. |

| **Attach supporting evidence** | **Join the review queue** |
|---|---|
| <a href="docs/assets/story/06-evidence.jpg"><img src="docs/assets/devices/06-evidence.jpg" alt="Attach supporting evidence: A note joins the existing case for specialist review." width="400" /></a> | <a href="docs/assets/story/08-handoff.jpg"><img src="docs/assets/devices/08-handoff.jpg" alt="Join the review queue: The case moves to human review with its explanation and evidence attached." width="400" /></a> |
| A note joins the existing case for specialist review. | The case moves to human review with its explanation and evidence attached. |

| **Monitor the runtime** | **Inspect the tool connection** |
|---|---|
| <a href="docs/assets/story/13-monitor.jpg"><img src="docs/assets/devices/13-monitor.jpg" alt="Monitor the runtime: Inspect observed capacity, agent activity and tool metrics." width="400" /></a> | <a href="docs/assets/story/14-mcp.jpg"><img src="docs/assets/devices/14-mcp.jpg" alt="Inspect the tool connection: The independent read-only MCP service reports its actual connection." width="400" /></a> |
| Inspect observed capacity, agent activity and tool metrics. | The independent read-only MCP service reports its actual connection. |

| **Review local regression results** | **Find the case again** |
|---|---|
| <a href="docs/assets/story/15-regression-console.jpg"><img src="docs/assets/devices/15-regression-console.jpg" alt="Review local regression results: These are mechanical fixture checks, not real-world model accuracy." width="400" /></a> | <a href="docs/assets/story/16-help.jpg"><img src="docs/assets/devices/16-help.jpg" alt="Find the case again: Help keeps the resolved case and support explanations together." width="400" /></a> |
| These are mechanical fixture checks, not real-world model accuracy. | Help keeps the resolved case and support explanations together. |

| **Choose the support settings** | **Inspect the actual analysis** |
|---|---|
| <a href="docs/assets/story/17-account.jpg"><img src="docs/assets/devices/17-account.jpg" alt="Choose the support settings: Single agent and durable context remain the default." width="400" /></a> | <a href="docs/assets/story/18-execution-trace.jpg"><img src="docs/assets/devices/18-execution-trace.jpg" alt="Inspect the actual analysis: Intent branches, the executed role and retrieved evidence are inspectable." width="400" /></a> |
| Single agent and durable context remain the default. | Intent branches, the executed role and retrieved evidence are inspectable. |

| **Inspect tools and memory** | **Start from Help** |
|---|---|
| <a href="docs/assets/story/19-tools-memory.jpg"><img src="docs/assets/devices/19-tools-memory.jpg" alt="Inspect tools and memory: Actual tool calls and source-backed case context appear in the trace." width="400" /></a> | <a href="docs/assets/story/20-trip-selector.jpg"><img src="docs/assets/devices/20-trip-selector.jpg" alt="Start from Help: The trip selector links the right ride without re-entering its details." width="400" /></a> |
| Actual tool calls and source-backed case context appear in the trace. | The trip selector links the right ride without re-entering its details. |

| **Manage retrieval sources** | **Inspect runtime instructions** |
|---|---|
| <a href="docs/assets/story/21-policy-management.jpg"><img src="docs/assets/devices/21-policy-management.jpg" alt="Manage retrieval sources: Operators can inspect document versions and reindex source content." width="400" /></a> | <a href="docs/assets/story/22-skills.jpg"><img src="docs/assets/devices/22-skills.jpg" alt="Inspect runtime instructions: English skill rules guide analysis without granting payment authority." width="400" /></a> |
| Operators can inspect document versions and reindex source content. | English skill rules guide analysis without granting payment authority. |

| **Inspect integration fixtures** | **State the boundaries** |
|---|---|
| <a href="docs/assets/story/23-integration-checks.jpg"><img src="docs/assets/devices/23-integration-checks.jpg" alt="Inspect integration fixtures: The console labels offline integration checks and their limitations." width="400" /></a> | <a href="docs/assets/story/24-about.jpg"><img src="docs/assets/devices/24-about.jpg" alt="State the boundaries: The demo explains simulated data, model mode and security limitations." width="400" /></a> |
| The console labels offline integration checks and their limitations. | The demo explains simulated data, model mode and security limitations. |

| **Switch the appearance** |  |
|---|---|
| <a href="docs/assets/story/25-rainbow-mode.jpg"><img src="docs/assets/devices/25-rainbow-mode.jpg" alt="Switch the appearance: Rainbow Mode is optional; the default storyline uses black and white." width="400" /></a> |  |
| Rainbow Mode is optional; the default storyline uses black and white. |  |

</details>


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
- Offline claim extraction is conservative and field-based. Arbitrary free-form corrections may remain unstructured; original messages are retained for specialist review.
- Business invariants passed in these runs, but this is not a general safety guarantee. No real payment was executed.
- SQLite and embedded Chroma target one state namespace and one worker; deployment needs managed identity, TLS, upload scanning and offsite recovery.

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
