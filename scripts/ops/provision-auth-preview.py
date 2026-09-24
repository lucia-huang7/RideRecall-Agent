"""Create isolated fictional accounts for exercising the authenticated profile.

Print paths only. Generated credentials are kept in an owner-only ignored file,
not stdout, version control, model context, or the hashed server registry.
"""

import argparse
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from ride_agent.domain.catalog import RideCatalog


def provision(directory):
    directory = Path(directory)
    if directory.exists():
        raise ValueError(
            ("Refusing to overwrite an existing identity release. Choose another directory to rotate credentials.")
        )
    directory.mkdir(parents=True, mode=448)
    expiry = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    roles = {
        "preview-rider": ["rider"],
        "preview-specialist": ["specialist"],
        "preview-operator": ["operator"],
    }
    credentials = {subject: secrets.token_urlsafe(32) for subject in roles}
    records = [
        dict(
            subject=subject,
            roles=assigned,
            token_sha256=hashlib.sha256(credentials[subject].encode()).hexdigest(),
            expires_at=expiry,
        )
        for subject, assigned in roles.items()
    ]
    catalog = RideCatalog()
    for trip in catalog.trips:
        trip["rider_id"] = "preview-rider"
    documents = {
        "identities.json": dict(version=1, identities=records),
        "access-credentials.json": dict(expires_at=expiry, credentials=credentials),
        "catalog.json": dict(
            schema_version=1,
            dataset_id="authenticated-fictional-preview",
            trips=catalog.trips,
            policies=catalog.policies,
        ),
    }
    for name, data in documents.items():
        descriptor = os.open(directory / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 384)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
    return dict(
        directory=str(directory.resolve()),
        identity_file=str((directory / "identities.json").resolve()),
        credential_file=str((directory / "access-credentials.json").resolve()),
        expires_at=expiry,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime" / "auth-preview")
    args = parser.parse_args()
    print(json.dumps(provision(args.output), indent=2))
