"""Redaction."""

import re


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if re.search(
                "password|secret|api.?key|authorization|access.?token|验证码|密码",
                str(key),
                re.I,
            )
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = re.sub("\\bsk-[A-Za-z0-9_-]{12,}\\b", "[REDACTED]", value)
        return re.sub("(?i)Bearer\\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", text)
    return value
