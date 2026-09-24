"""Validated provider configuration, independent of user-access credentials."""
from dataclasses import dataclass, field
import ipaddress
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ModelSettings:
    mode: str
    model: str
    api_key: str = field(default='',repr=False)
    base_url: str | None = field(default=None,repr=False)

    @classmethod
    def from_environment(cls):
        mode=os.getenv('LLM_AGENT_SYSTEM_RIDE_MODEL_MODE','demo').lower()
        if mode not in ('demo','live'):raise ValueError('LLM_AGENT_SYSTEM_RIDE_MODEL_MODE must be demo or live.')
        if mode=='demo':return cls(mode,'deterministic-messages-adapter')
        model=os.getenv('LLM_AGENT_SYSTEM_RIDE_MODEL','')
        key=os.getenv('LLM_AGENT_SYSTEM_RIDE_API_KEY','')
        if not key or not model:raise ValueError('Live mode requires LLM_AGENT_SYSTEM_RIDE_API_KEY and LLM_AGENT_SYSTEM_RIDE_MODEL.')
        base=os.getenv('LLM_AGENT_SYSTEM_RIDE_BASE_URL') or None
        if base:
            parsed=urlsplit(base)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Provider URL must be HTTP(S) without credentials, query or fragment.')
            if os.getenv('LLM_AGENT_SYSTEM_ENV')=='production' and parsed.scheme!='https':
                try:local=ipaddress.ip_address(parsed.hostname).is_loopback
                except ValueError:local=parsed.hostname=='localhost'
                if not local:raise ValueError('Production provider connections require HTTPS unless loopback.')
        return cls(mode,model,key,base)
