"""Explicit demo isolation and scoped opaque-token identities for the ride API.

Production credentials are SHA-256 digests of random bearer tokens, loaded from
an operator-owned file at startup. Model provider keys are unrelated credentials.
No authentication fallback is allowed in production.
"""
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import os
from pathlib import Path
import re
import stat
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator


Role = Literal['rider', 'specialist', 'operator']


class IdentityRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    subject: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    token_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    roles: frozenset[Role] = Field(min_length=1)
    expires_at: datetime
    enabled: bool = True

    @field_validator('expires_at')
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError('Identity expiration must include a timezone')
        return value


class IdentityDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1]
    identities: list[IdentityRecord] = Field(min_length=1, max_length=128)


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str]


principal_context: ContextVar[Principal | None] = ContextVar('ride_principal', default=None)


class Security:
    def __init__(self, environment=None, identity_file=None):
        self.environment = environment or os.getenv('KINDER_UBER_ENV', 'development')
        if self.environment not in ('development', 'test', 'production'):
            raise ValueError('KINDER_UBER_ENV must be development, test or production')
        self.production = self.environment == 'production'
        self.identities = []
        if self.production:
            filename = identity_file or os.getenv('KINDER_UBER_IDENTITY_FILE')
            if not filename:
                raise ValueError('Production requires KINDER_UBER_IDENTITY_FILE')
            path = Path(filename)
            if os.name == 'posix' and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ValueError('Identity file must not be accessible by group or other users')
            if path.stat().st_size > 128 * 1024:
                raise ValueError('Identity file exceeds the configuration limit')
            try:
                document = IdentityDocument.model_validate_json(path.read_text())
            except Exception:
                # Do not echo malformed secret-bearing input in startup errors.
                raise ValueError('Invalid identity configuration') from None
            identities = document.identities
            if len({i.token_sha256 for i in identities}) != len(identities):
                raise ValueError('Identity token digests must be unique')
            self.identities = identities

    def authenticate(self, authorization):
        scheme, _, token = authorization.partition(' ')
        if scheme.lower() != 'bearer' or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
            raise HTTPException(401, 'A valid access token is required.', headers={'WWW-Authenticate': 'Bearer'})
        digest = hashlib.sha256(token.encode()).hexdigest()
        found = None
        for entry in self.identities:
            if hmac.compare_digest(digest, entry.token_sha256):
                found = entry
        if found is None or not found.enabled or found.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(401, 'A valid access token is required.', headers={'WWW-Authenticate': 'Bearer'})
        return Principal(found.subject, found.roles)

    def authorize(self, path, method, principal):
        if path == '/api/session':
            return
        if path.startswith('/api/agent/cases'):
            required = {'specialist'}
        elif path.startswith('/api/agent/'):
            required = {'operator'}
        elif method == 'GET' and re.fullmatch(r'/api/cases/[^/]+/attachments/[^/]+', path):
            required = {'rider', 'specialist'}
        else:
            required = {'rider'}
        if not required.intersection(principal.roles):
            raise HTTPException(403, 'This account cannot perform that operation.')

    def check_demo_peer(self, host):
        # TestClient uses a non-network peer. Forwarded headers never choose identity.
        if self.environment == 'test' or host == 'testclient':
            return
        try:
            allowed = ipaddress.ip_address(host).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            raise HTTPException(403, 'Demo mode accepts loopback connections only.')

    def check_demo_authority(self, host, origin=None, *, test_client=False):
        from core.http_boundary import loopback_authority, loopback_origin
        allowed_host=loopback_authority(host) or (test_client and host=='testserver')
        if not allowed_host or not loopback_origin(origin):
            raise HTTPException(403, 'Demo requests require a loopback host and origin.')

    def validate_catalog(self, catalog):
        if not self.production:
            return
        riders = {i.subject for i in self.identities if 'rider' in i.roles}
        if not catalog.path or any(t.get('rider_id') not in riders for t in catalog.trips):
            raise ValueError('Production requires an explicit catalog with registered rider ownership for every trip')

    def validate_stored_cases(self, store, catalog):
        """A catalog release cannot orphan cases or silently transfer their trip."""
        trips = {trip['id']: trip for trip in catalog.trips}
        with store.connect() as db:
            for owner, trip_id in db.execute('SELECT DISTINCT owner,trip_id FROM case_index'):
                trip = trips.get(trip_id)
                if trip is None:
                    raise ValueError('Catalog release omits a stored case trip. Retain historical records or use a fresh database.')
                if self.production and trip.get('rider_id') != owner:
                    raise ValueError('Catalog release changes stored case ownership. An explicitly reviewed data migration is required.')

    def require_trip(self, catalog, trip_id):
        if not self.production:
            return
        principal = principal_context.get()
        try:
            trip = catalog.trip(trip_id)
        except KeyError:
            raise HTTPException(404, 'Trip not found.') from None
        if principal is None or trip.get('rider_id') != principal.subject:
            raise HTTPException(404, 'Trip not found.')


def staff_receipt_owner():
    principal = principal_context.get()
    return 'staff:' + principal.subject if principal else 'agent'
