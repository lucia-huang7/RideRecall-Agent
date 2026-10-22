# Agent and memory ablation — full 2,000-dialogue release

## Outcome

**Durable case context improved strict task completion by 33 percentage points. More agents produced no measured task or context benefit in either memory condition.** Both agent comparisons tied on every one of the 2,000 dialogues. The adaptive multi-agent path used about 38% more local engine-client calls and had higher median dialogue time in this run.

These findings describe this deterministic offline implementation and generated workload. They do not establish live-model quality, human-reviewed accuracy or production performance.

## Frozen method

Application, harness, reporter and [protocol](ABLATION_PROTOCOL.md) were frozen at `885dba20304e757992b2ae84dc8c6af9697a65e3` before the four full runs. The harness only changes available memory sources and agent dispatch/capability exposure; application rules, input files, original scorer and oracle remain unchanged. All 314 frozen files and dataset hashes matched at final verification. The release is seed `20261022`, with 1,000 worlds and two dialogue variants per world, previously partly inspected.

Each condition replayed all 2,000 dialogues through FastAPI, actual intent/routing, retrieval, context, guarded tools and independent stdio MCP, without provider credentials. Conditions ran sequentially in the recorded order: single/recent, multi/durable, multi/recent, single/durable. Every condition used isolated storage and the same catalog, steps and scorer-only labels.

The single generalist receives the union of the same read-only tools and uses the same offline role renderers/deduplication. Recent-four exposes four prior rider-started rounds plus the current query, discarding old snapshots, summaries, state and ledgers. Durable uses the unchanged source-history-backed case memory. It rebuilds claims in O(history); the experiment does not isolate episodic retrieval or summary-compression benefit.

## Overall measured results

Strict completion requires every original step check to pass. Final context loss means the last eligible analysis of a dialogue failed active-claim retention or answer coverage. Every dialogue had an eligible checkpoint.

| Configuration | Strict completed | Final context loss | Checkpoint context loss | Median / p95 dialogue | Wall time | Engine-client calls |
|---|---:|---:|---:|---:|---:|---:|
| Single + recent four | 1340/2,000 (67.00%) | 660/2,000 (33.00%) | 4100/12026 (34.09%) | 408.2 / 1513.0 ms | 1296.391 s | 58,594 |
| Adaptive multi + recent four | 1340/2,000 (67.00%) | 660/2,000 (33.00%) | 4100/12026 (34.09%) | 498.9 / 1932.3 ms | 1594.502 s | 81,084 |
| Single + durable context | 2000/2,000 (100.00%) | 0/2,000 (0.00%) | 0/12026 (0.00%) | 382.6 / 1471.6 ms | 1234.528 s | 59,002 |
| Adaptive multi + durable context | 2000/2,000 (100.00%) | 0/2,000 (0.00%) | 0/12026 (0.00%) | 456.3 / 1761.5 ms | 1431.708 s | 81,611 |

Every configuration completed **27,772 HTTP requests**: 25,272 returned 200, 2,000 expected foreign-owner rejections returned 404 and 500 intentionally invalid close actions returned 409. Across all four conditions: **8,000 replays, 111,088 requests, zero episode exceptions**. Measured condition wall times sum to 5,557.129 seconds; initialization between subprocesses and final verification are additional.

Business-state conformance was **2,000/2,000 in all four conditions**, even when memory checks failed. This metric covers observed HTTP/state/proposal/adjustment/evidence/MCP/ownership/replay assertions, not natural-language quality. It demonstrates why successful state transitions alone are insufficient evidence of effective support.

The multi-agent condition was adaptive: 4,498 of 12,522 analyses actually executed multiple roles; the remaining 8,024 used one role. Single-agent conditions executed one role in all 12,522 analyses. Each condition had exactly its expected 12,522 distinct analysis executions, with no per-episode count mismatch.

Engine-client counts include local routing/RAG/agent/composition calls; they exclude the separate deterministic memory summarizer. They are not live provider requests, tokens or dollar costs. Recent-four reconstructs its memory window each time; timing includes this implementation cost. Fixed run order, cache expiry and local machine conditions limit timing comparisons; no speed significance or production SLO is claimed.

## Split results

Agent variants were identical within each memory condition for all task and context metrics. Separate rows are retained for every condition in the [machine-readable report](results/ablation-20261022.json).

| Split | Worlds | Dialogues per condition | Single recent completed / lost | Multi recent completed / lost | Single durable completed / lost | Multi durable completed / lost |
|---|---:|---:|---:|---:|---:|---:|
| Development | 606 | 1212 | 808 / 404 | 808 / 404 | 1212 / 0 | 1212 / 0 |
| Calibration | 219 | 438 | 298 / 140 | 298 / 140 | 438 / 0 | 438 / 0 |
| Test | 175 | 350 | 234 / 116 | 234 / 116 | 350 / 0 | 350 / 0 |

## Family results

Each family contains 125 worlds / 250 dialogues. Values are strict-completed / final-context-lost.

| Family | Single recent | Multi recent | Single durable | Multi durable |
|---|---:|---:|---:|---:|
| authorization | 170 / 80 | 170 / 80 | 250 / 0 | 250 / 0 |
| duplicate_settlement | 170 / 80 | 170 / 80 | 250 / 0 | 250 / 0 |
| evidence_mismatch | 170 / 80 | 170 / 80 | 250 / 0 | 250 / 0 |
| fare_over_cap | 170 / 80 | 170 / 80 | 250 / 0 | 250 / 0 |
| fare_supported | 170 / 80 | 170 / 80 | 250 / 0 | 250 / 0 |
| mcp_recovery | 150 / 100 | 150 / 100 | 250 / 0 | 250 / 0 |
| no_excess | 170 / 80 | 170 / 80 | 250 / 0 | 250 / 0 |
| pickup_conflict | 170 / 80 | 170 / 80 | 250 / 0 | 250 / 0 |

## Paired changes and failure analysis

- Durable versus recent-four, fixing either agent configuration: **660 wins, zero losses, 1,340 ties** for strict completion and final context. These are the same dialogue IDs in both agent comparisons, not 1,320 distinct improvements.
- Multi versus single, fixing either memory configuration: **zero wins, zero losses, 2,000 ties** for strict completion and final context. No agent-count quality gain was measured.
- Both recent-four runs failed on the same 660 dialogues: 640 with eight filler rounds and 20 MCP-recovery dialogues with one filler round plus additional retry/correction turns. Those extra turns can displace older facts from the four-round window.
- Each recent-four run recorded 4,100 active-claim retention failures, 3,677 answer-coverage failures and 247 unexpected clarification-state failures. These overlap and must not be added as independent errors.
- All four conditions passed 496/496 expected-clarification checkpoints, and none restated forbidden withdrawn/unadopted claims. The observed window problem was omission/unnecessary clarification, not stale-claim repetition.

The 33-point improvement measures retaining source facts beyond the selected window under these synthetic patterns. It is not evidence that a model learned better reasoning, that summaries became lossless, or that real refund success increased. The finite language generator and shared development process limit external validity; independent human review remains pending.

## Integrity, retained failures and reproduction

Every condition contains all 2,000 expected IDs exactly once. Related world variants do not cross splits. Source/data, score, raw-response and observation fingerprints matched, and independently counted raw requests matched the summaries. [The JSON report](results/ablation-20261022.json) includes every split/family metric, failure examples, paired counts, source/dataset fingerprints, HTTP/analysis timings and counters.

The initial small preflight exposed a harness window-index error on fewer than four rounds; it was corrected before freezing. A later preflight had an MCP startup failure and was retained as an infrastructure failure. Corrected 16-dialogue preflights verified all four interventions. These preflights are excluded from the 8,000 formal runs; no formal run was discarded or replaced.

Raw responses, step scores, observations, execution events, freeze records and failed preflights remain locally under ignored `artifacts/ablation-2000-*` directories. The installed application and customer-facing demo data were not modified.

From a clean checkout of the frozen commit, install README runtime/test dependencies, generate the seed-20261022 dataset as documented in automated checks, then run from the root with provider credentials absent:

```bash
PYTHONPATH=backend KINDER_UBER_ENV=test KINDER_UBER_RIDE_MODEL_MODE=demo python scripts/check-ablation.py \
  --dataset artifacts/reproduced-20261022 --output artifacts/my-ablation/single_recent4 \
  --configuration single_recent4
```

Repeat with `multi_durable`, `multi_recent4` and `single_durable`, each using a new output path. Preserve the frozen source/dataset fingerprints and protocol before execution. The reporter requires a `freeze.json` containing `dataset`, `frozen_root`, `source_sha256` and `dataset_sha256`, as used by this run; then run `python scripts/report-ablation.py --run artifacts/my-ablation`. Compare generated payload hashes with the published dataset manifest. Do not reuse the original result paths or relabel this inspected release as new holdout evidence.

## Engineering interpretation

Prioritize durable, source-backed context and guarded state transitions. Keep multi-agent execution as an architectural option; the measured workload provides no quality justification for its additional local calls and latency. A later live-model study would need matched tool/budget controls, independently reviewed examples and repeated randomized timing runs before making stronger claims.
