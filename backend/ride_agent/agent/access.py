"""Access."""

import hashlib
import hmac
import json
import os


def identity(authorization):
    raw = os.getenv("LLM_AGENT_SYSTEM_API_TOKENS", "").strip()
    if not raw:
        if os.getenv("LLM_AGENT_SYSTEM_ENV", "development") == "production":
            raise ValueError("Production requires LLM_AGENT_SYSTEM_API_TOKENS")
        return None
    entries = json.loads(raw)
    if not isinstance(entries, dict) or not entries:
        raise ValueError("LLM_AGENT_SYSTEM_API_TOKENS must be a nonempty object")
    for token, account in entries.items():
        if (
            len(token) < 24
            or not isinstance(account, dict)
            or (not isinstance(account.get("user_id"), str))
            or (not account.get("user_id"))
            or (account.get("role") not in ("user", "admin"))
        ):
            raise ValueError("Invalid API token configuration")
    supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
    digest = hashlib.sha256(supplied.encode()).digest()
    for token, account in entries.items():
        if hmac.compare_digest(hashlib.sha256(token.encode()).digest(), digest):
            return account
    return {}
