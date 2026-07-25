# Case consistency and recovery

## Authority and concurrency

SQLite is the authority for case state, messages, source-backed context snapshots, financial proposals and command receipts. Each saved case has an increasing `revision`. The same database transaction commits a mutation, its replay response and any derived-memory outbox entries.

Analysis runs outside the write transaction. A slow model/tool operation therefore does not hold a database write lock. Before applying its result, the command reloads the case and compares the analyzed revision. If another command changed the case, the stale analysis is rejected; its speculative memory effects are discarded. Staff evidence/replies cannot be silently replaced by the older analysis snapshot.

Authenticated mutations of an existing case require a strong conditional header:

```http
If-Match: "12"
X-Request-ID: a-unique-command-id
```

Read the revision from the case JSON or `ETag` header. Missing preconditions return 428, malformed ones 400 and a changed revision 412. An optimistic conflict detected after analysis returns 409. The UI refreshes the case and asks the user to review the new state instead of automatically repeating a financial decision.

An exact replay is looked up before checking a now-old precondition. It returns the committed receipt, with no repeated model execution or financial action. Reusing a command ID for a different payload is still a 409. Staff receipts are scoped to the authenticated specialist. The client retains a command ID across transport failures and refuses to replace a visible case with an older poll or historical receipt.

New cases receive their complete UUID-based ID before analysis, so even the initial memory turn uses the actual case namespace. It does not share a reusable “new case” profile bucket.

## Memory outbox

The existing memory algorithms still prepare context, compress conversations and update profiles. Their vector writes are staged while analyzing a speculative turn. Profile reads see the staged update, but Chroma is not mutated with speculative content.

When the case commits, its vector effects enter `projection_outbox`. A worker applies them in sequence, then records completion. It uses stable document IDs and upserts, so retrying after a crash between vector write and acknowledgement does not duplicate episodic records. The API attempts an immediate bounded flush, and a background worker recovers pending work after restart with capped retry backoff.

A projection failure does not roll back an already committed case or pretend that the command failed before commit. The source case remains authoritative; the derived index may lag. Operations expose pending/retrying work. Readiness degrades on projection failures or an old backlog, while process liveness remains separate.

Diagnostic memory and quality fixtures also use a temporary canonical case store and the transaction/outbox path. They retain uniquely scoped vector records during the run and clean them afterward; they do not create fixture cases in the rider database.

## Schema and process ownership

`PRAGMA user_version` identifies the schema. Startup migration is transactional and preserves legacy case JSON and receipts; legacy cases begin at revision zero. An application refuses to downgrade a newer database. Normal connections open an existing database in `rw` mode, so polling a missing file cannot silently create an empty replacement. Startup also refuses to initialize a missing primary database when its derived index directory remains. Case databases and embedded index directories are owner-only on POSIX systems.

A catalog release must retain every trip referenced by a stored case. Authenticated deployments also require the recorded case owner to remain the trip's rider. Startup refuses an incompatible release before initializing retrieval or tools; it does not delete, reassign or rewrite cases to make a release fit. Retain historical trip records, restore the matching configuration, or perform an explicitly reviewed migration. A fresh isolated dataset should use a fresh database.

An OS-owned lock is acquired **before** initializing storage or embedded indexes. Only one application process may own a data directory. The OS releases the lock when the process dies. Use one Uvicorn worker with the embedded storage profile. This is an enforced single-node design, not distributed locking or horizontal scalability.

## Verified failure paths

The consistency tests execute:

- A real analysis paused while a specialist commits a newer reply: the analysis returns a conflict, commits no receipt and publishes no speculative profile update.
- A child process exiting immediately after the SQLite commit: restart restores the case and pending memory projection; replay returns the original receipt without calling the model.
- Derived-index failure after case commit: the source case remains readable, readiness becomes 503 and retry recovers the projection.
- Legacy schema migration, stale saves, refusal of future schemas, process-level writer exclusion and recovery after lock release.
- Conditional confirmation after new evidence: stale review cannot execute an adjustment; exact request replay remains stable.

These tests establish the specified failure behavior, not complete disaster recovery. Offline backup and attachment limits are described below. Cross-node replication, global storage quotas, retention and a complete derived-index rebuild remain deployment work; this single-node implementation does not establish a production RPO/RTO.

## Offline snapshots and attachment storage

Schema 2 extracts legacy inline attachments transactionally into case-scoped immutable blobs. Case JSON and replay receipts contain metadata, not repeated base64 copies. Identical bytes are deduplicated within a case; downloads verify the stored SHA-256 digest. New uploads are limited to ten files and 15 MiB of unique bytes per case, with twenty total evidence items. Fifty rider messages per case leave human review available. Existing history exceeding these limits is preserved. Receipts are compressed with a bounded decoder; there is no automatic receipt expiry or global disk quota.

Stop the application before taking a complete database/index/Skill snapshot. The snapshot tool acquires the same process lock as the server and refuses an active writer. Run from `backend/` with the installed Python environment:

```sh
python -m mobility.backup create /absolute/path/rides.sqlite3 /private/backups/release-1
python -m mobility.backup verify /private/backups/release-1
python -m mobility.backup restore /private/backups/release-1 /private/recovered-instance
```

Restoration requires a fresh directory and never overwrites existing state. It returns the restored database path (`state.sqlite3`), with correspondingly named index/Skill/baseline sidecars. The snapshot includes SQLite cases, receipts, attachment blobs, memory outbox, embedded indexes, runtime Skills and the evaluation baseline. A checksummed manifest and SQLite integrity/foreign-key checks detect incomplete or altered snapshots. These hashes detect corruption; they do not authenticate a backup from an untrusted party. Store backups privately and encrypt them offsite according to your deployment policy.

Identity credentials, the selected catalog, environment configuration and application code are deliberately separate: preserve their reviewed versions and credentials through your secret/configuration management. The manifest is completed only after all state has been copied; a failed operation removes its partial output. A restored older schema migrates on application startup, so keep the original snapshot to recover with the corresponding application release. Never point an older application at an already migrated database.

## Durable configuration releases

Schema 3 makes policy imports and Skill changes follow the same commit-before-projection rule as case memory. A command transaction stores its canonical requested content, ordered configuration outbox entry and exact receipt together. The response explicitly reports `status: accepted` and a `configuration_sequence`; acceptance does not silently imply that a failed runtime projection succeeded.

A separate worker applies policy index replacements and atomic, fsynced Skill files in sequence. Failed work remains pending with capped retry backoff and resumes after restart. Repeated delivery replaces the same document/file rather than duplicating it. `/api/agent/configuration` and the operator monitor expose pending/retrying work; readiness fails while a committed configuration release is unapplied. The UI distinguishes committed content from a pending runtime update.

Concurrent analyses may share a read gate. A waiting configuration writer prevents new analyses from entering, waits for active analyses to finish, then replaces the runtime projection exclusively. Pending configuration prevents new analysis until recovery; this avoids combining a new Skill with half-replaced retrieval chunks. Cancellation releases the gate and preserves committed work. Reloading a saved Skill is validated and receipted too, and is refused while another release is pending.

Production release policy anchors remain immutable through management routes. Supplemental retrieval content and Skills cannot change the authoritative financial catalog or bypass confirmation checks. Changing the financial release requires a reviewed catalog deployment. This is durable single-node configuration delivery, not a distributed configuration service or a full approval/rollback workflow.

## Activity index and pagination

Schema 4 adds a small relational `case_index`, backfilled without rewriting source case JSON or existing receipts. Each later source save updates its summary in the same transaction. Foreign-key deletion cascades remove derived summaries with their source case. Index queries read summary columns rather than deserializing conversations, attachments or execution traces.

Rider `GET /api/cases/index` and specialist `GET /api/agent/cases/index` return `items`, `next_cursor`, `total`, `active_total` and the most recent active summary. Page size defaults to 25 and is bounded at 100. Cursors use a persistent per-database HMAC secret and bind the account and filter, so they cannot be forged or reused across account/queue scopes. The optional rider `trip_id` filter is ownership checked. A specialist obtains a selected full case from `GET /api/agent/cases/{id}` only after handoff; the response separates the case from server-derived available actions.

The older full-list routes remain compatible arrays, now bounded and carrying `Link: ...; rel="next"`, `X-Next-Cursor` and `X-Total-Count`. Callers must follow pagination instead of assuming that one response is an entire database. The mobile UI uses summaries, explicit Load more controls and selected-case detail; it does not silently truncate history. Polling merges revisions and rejects stale detail/action metadata. Trip lookup uses the same indexed latest-case selection before and during a command transaction, including when historical duplicates exist.

This is a live activity cursor, not an export snapshot. Concurrent updates can move a row earlier in the ordering; clients deduplicate by ID/revision and refresh to discover new head entries. A separate snapshot export would need its own consistency contract.

The no-key browser demo also preserves already-saved same-origin workspace IDs during storage-key migration. Recovery tries at most four locally saved IDs after an empty initial index or an inaccessible case deep link; it never guesses another account and never runs for authenticated requests. The current and previous demo IDs remain recoverable in browser storage. Production credentials remain memory-only and do not use this mechanism.
