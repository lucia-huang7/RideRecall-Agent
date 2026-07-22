# Identity and request boundaries

## Two explicit profiles

The default development profile is a loopback-only, no-key demonstration. Its `X-Demo-Session` and public specialist header are role-play controls. A remote network peer is rejected even if it knows those headers. The macOS supervisor explicitly starts this profile on loopback.

`KINDER_UBER_ENV=production` enables the authenticated profile. Startup fails unless an owner-only identity registry and an explicitly owned runtime catalog are valid. Model-provider configuration is separate: this profile can still run the deterministic model without a model API key. Financial actions remain simulations.

## Authenticated identity

Set `KINDER_UBER_IDENTITY_FILE` to a versioned JSON file containing up to 128 records. Each record has:

- `subject`: stable account ID, 1–80 ASCII letters, digits, underscores or hyphens.
- `token_sha256`: lowercase SHA-256 digest of a cryptographically random opaque credential.
- `roles`: one or more of rider, specialist and operator.
- `expires_at`: timezone-aware expiration timestamp.
- `enabled`: optional boolean, default true.

The file must not be readable by group or other users on POSIX systems. The server loads it at startup; disable a record or rotate credentials in a new release and restart to revoke access. Duplicate token digests, unknown fields and malformed records are rejected without echoing submitted configuration. Clients send `Authorization: Bearer <credential>`; invalid, expired and disabled credentials get the same 401 response and a Bearer challenge. Token comparisons use constant-time digest comparison.

Production ignores demo headers when choosing identity. Client-supplied rider IDs cannot override the authenticated subject. Every runtime trip must name a registered `rider_id`. The API filters trip lists and rejects foreign trip/case operations before invoking the engine.

## Roles

| Operation | Required role |
|---|---|
| Own trips, cases, evidence, rider confirmation | rider |
| Handoff queue and specialist case actions | specialist |
| Metrics, knowledge/skill controls, MCP controls and evaluation jobs | operator |
| Attachment download | owning rider, or specialist after handoff |

Roles do not imply each other. An operator cannot act as a specialist without that explicit additional role. A specialist must accept a case before updating it; only the assigned specialist can reply, propose an adjustment or resolve it. Actual specialist identity is recorded in assignment and decision events. Staff idempotency receipts are scoped to that identity rather than one shared account.

## Browser session

The authenticated UI asks for a workspace credential. It remains in page memory, never local/session storage or a URL. Sign-out clears the credential and retry state; refreshing requires signing in again. Navigation is role-aware, and server authorization remains authoritative. Browser refresh is not server-side token revocation: an unexpired opaque token remains valid until registry revocation/restart or expiry.

No cookies are used for these credentials. Deployment assumes a trusted same-origin HTTPS gateway. The direct HTTP authenticated preview is for loopback development only. This is not an OIDC identity provider, self-service account system, or multi-tenant authorization service.

## Request and response controls

- Global body limit: 4 MiB, checked before JSON/multipart parsing, including chunked bodies and misleading lengths.
- Body acquisition timeout: 15 seconds. Invalid/duplicate lengths are rejected; oversized requests close the connection.
- Attachments: existing 3 MiB limit, accepted PNG/JPEG/PDF signatures, attachment disposition and no-sniff responses. These checks do not prove a file is harmless; use scanning/quarantine before accepting untrusted uploads in a real deployment.
- Request models reject unknown fields. Validation responses omit submitted values and cap the error list.
- Authenticated API responses use `Cache-Control: no-store`. Responses disable framing and sensitive browser permissions; production applies a same-origin Content Security Policy.
- Interactive API documentation is disabled in the authenticated profile.

## Health endpoints

`/livez` reports process responsiveness only. `/readyz` separately checks completed startup, an existing readable case schema, populated policy indexes and an actual independent MCP trip-record response. A dependency failure returns 503 without internal paths, credentials or customer data. `/health` retains demo metadata for the local interface; the authenticated profile returns a minimal status.

Readiness is not an end-to-end availability guarantee. A readable SQLite schema does not prove disk capacity or distributed write availability. Production deployment still needs TLS, persistent-volume permissions, backups, external identity lifecycle integration, abuse controls and measured concurrency/fault testing.

## Exercise the profile locally

Using an environment with the backend dependencies installed:

```bash
python scripts/provision-auth-preview.py
bash scripts/run-authenticated-demo.sh
```

Open `http://127.0.0.1:8012/`. Generated fictional account credentials are in the owner-only ignored `.runtime/auth-preview/access-credentials.json`; the server receives only their digests. Do not paste credentials into issues or logs. The helper prints file paths, not credentials, and refuses to overwrite an existing release. Choose a new `--output` directory to rotate, then pass that directory to the run script. Preview credentials expire after 24 hours.
