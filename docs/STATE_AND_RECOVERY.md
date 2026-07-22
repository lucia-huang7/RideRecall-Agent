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
