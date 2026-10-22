# Paired 2,000-dialogue ablation protocol

## Fixed inputs and interventions

Use the unchanged seed-20261022 release (1,000 worlds, 2,000 dialogues), with four conditions: `single_recent4`, `multi_recent4`, `single_durable`, `multi_durable`. Each condition receives exactly the same steps, catalog, oracle and scoring rules. This release was inspected previously; this is a paired diagnostic study, not a new blind holdout. Freeze application, harness and this protocol before full execution. Retain failed runs. Do not tune application/scorer/input rules after observing condition scores.

Multi-agent means the existing adaptive routing and specialist execution, not a forced number of roles. Single-agent forces one generalist with the union of the same read-only tools. Its offline response renderer combines the same technical, billing and handoff rendering functions with the existing composer's sentence deduplication. This explicit capability adapter avoids handicapping it with fewer permissions or a generic response template. It is not a learned generalist or a live-model comparison.

Recent-four retains the last four rider-started rounds and intervening replies, from the case snapshot, plus the current query. Creation already contains its first rider message; subsequent message analyses receive the current query separately. The current query is passed identically in every condition. Old memory snapshots, summaries, state and claim ledgers are removed before the same memory/claim algorithms run. The canonical case remains intact for audit, state transitions and evidence; verified catalog records and attached evidence remain available equally to all conditions. Recent assistant replies are permitted in the window, but the production claim resolver adopts rider sources only. Durable uses the unchanged case-history-backed memory implementation, which rebuilds claims in O(history); do not describe it as independently verified episodic-retrieval benefit.

Each condition runs the full original HTTP replay with an isolated database, actual orchestration, Chroma and independent stdio MCP. New instrumentation observes counters and scores without changing their return values. The application stays in deterministic no-key mode. Runs are sequential, with a fixed recorded condition order; single local runs cannot establish robust latency differences or statistical production performance.

## Metrics fixed before execution

- **Strict task completion:** fraction of dialogues passing all original per-step checks. Report the existing bounded-call-log assertion limitation rather than silently rewriting the scorer.
- **Business-state conformance:** fraction of dialogues passing all observed state/proposal/adjustment/evidence/HTTP/ownership/MCP checks, excluding analysis-content checks. Report exceptions/incomplete steps as failures. This metric distinguishes state-machine outcomes from answer/context quality.
- **Context loss:** among scored analysis checkpoints with nonempty expected claims and no expected clarification, failure of either `active_claims` or `claim_answer_coverage`. Report lost/eligible checkpoints, dialogue-any-loss/eligible dialogues, and final eligible checkpoint loss separately. Do not count expected ambiguity as forgetting.
- **Stale claims:** checkpoints failing the original forbidden-withdrawn/unadopted assertion, reported separately from omissions.
- **Clarification:** expected-clarification checkpoints passing both context and answer checks.
- **Timing/cost proxies:** full wall time, median/p95 dialogue and HTTP times; actual analysis latency, monotonic lifetime model-call deltas and executed role counts. These exclude external model inference and measure no real token/dollar cost.

Report each condition by development/calibration/test and family. Pair exact episode IDs across conditions; report wins/losses/ties for memory and agent contrasts, including final-context loss. Variants are paired within a world; do not treat 2,000 dialogues as 2,000 independent worlds or manufacture significance from repeated templates. Present descriptive differences, not causal real-world quality claims.

## Integrity and limitations

Verify all source/harness/protocol and dataset hashes after execution, exact 2,000-ID coverage per condition, unique IDs, grouped split isolation, raw request counts, score/response/observation hashes, and actual single-role versus adaptive execution. Keep raw logs locally in ignored artifacts. Publish honest failure summaries, including no benefit or regressions.

The synthetic language/oracle is finite and not independently human-reviewed. State rules can complete a financial workflow even when an answer forgets a rider claim; that is why both metrics are required. The deterministic adapter gives no evidence of live-model multi-agent reasoning advantage. The original `no_second_model_run` assertion compares bounded call-log length; monotonic total calls are additionally observed, without changing the frozen score. Dependency and deployment limitations remain as documented elsewhere.
