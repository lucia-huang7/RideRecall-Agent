"""Validated runtime data shared by HTTP tools, business rules and the MCP process.

Evaluation labels are not part of this contract. A catalog is selected explicitly
at startup and must remain immutable while the application is running.
"""

import re
from copy import deepcopy
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from ride_agent.domain.seed import POLICIES, TRIPS


class PaymentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    status: Literal["settled", "pending", "released"]
    amount: StrictInt = Field(ge=0, le=10000000)


class PolicyRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_auto_adjustment_cents: StrictInt = Field(default=10000, ge=0, le=10000000)


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    version: str
    region: str
    text: str
    rules: PolicyRules = Field(default_factory=PolicyRules)


class Trip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern="^trip-[a-z0-9-]+$", max_length=100)
    date: str
    time: str
    destination: str
    pickup: str
    vehicle: str
    status: Literal["Canceled", "Completed"]
    total: StrictInt = Field(ge=0, le=10000000)
    estimated: StrictInt = Field(ge=0, le=10000000)
    duration: str
    distance: str
    issue: Literal["cancellation", "fare", "payment"]
    arrival_disputed: bool
    eligibility: Literal["review", "adjust", "explain"] = "review"
    caption: str
    color: str
    rider_id: str | None = Field(default=None, pattern="^[A-Za-z0-9_-]{1,80}$")
    policy_scope: str | None = None
    payments: list[PaymentRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_payments(self):
        if self.payments and sum((p.amount for p in self.payments if p.status == "settled")) != self.total:
            raise ValueError("Settled records must add up to the trip total")
        if len({p.id for p in self.payments}) != len(self.payments):
            raise ValueError("Payment record IDs must be unique within a trip")
        return self


class CatalogDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    dataset_id: str
    trips: list[Trip] = Field(min_length=1)
    policies: dict[str, Policy]

    @model_validator(mode="after")
    def consistent_scopes(self):
        if len({t.id for t in self.trips}) != len(self.trips):
            raise ValueError("Trip IDs must be unique")
        for scope in self.policies:
            if not re.fullmatch("[a-z][a-z0-9_-]{2,40}", scope):
                raise ValueError("Policy scopes must be safe index names")
        for trip in self.trips:
            scope = trip.policy_scope or trip.issue
            if scope not in self.policies:
                raise ValueError("Every trip must reference an available policy scope")
        return self


def charge_breakdown(trip):
    records = trip.get("payments", [])
    settled = [p["amount"] for p in records if p["status"] == "settled"] if records else [trip["total"]]
    pending = (
        sum((p["amount"] for p in records if p["status"] == "pending"))
        if records
        else trip["total"]
        if trip["issue"] == "payment"
        else 0
    )
    return dict(
        settled=sum(settled),
        estimated=trip["estimated"],
        pending_authorization=pending,
        settled_count=len(settled),
    )


class RideCatalog:
    def __init__(self, path=None):
        self.path = str(Path(path).resolve()) if path else None
        if self.path:
            document = CatalogDocument.model_validate_json(Path(self.path).read_text())
            self.dataset_id = document.dataset_id
            self.trips = [t.model_dump() for t in document.trips]
            self.policies = {scope: p.model_dump() for scope, p in document.policies.items()}
        else:
            self.dataset_id = "builtin-demo"
            self.trips = deepcopy(TRIPS)
            self.policies = {k: Policy.model_validate(v).model_dump() for k, v in POLICIES.items()}
        self._trips = {trip["id"]: trip for trip in self.trips}

    def scope(self, trip):
        return trip.get("policy_scope") or trip["issue"]

    def policy(self, trip):
        return deepcopy(self.policies[self.scope(trip)])

    def trip(self, trip_id):
        if trip_id not in self._trips:
            raise KeyError(trip_id)
        trip = deepcopy(self._trips[trip_id])
        charges = charge_breakdown(trip)
        delta = charges["settled"] - trip["estimated"]
        cap = self.policy(trip)["rules"]["max_auto_adjustment_cents"]
        if trip["issue"] == "cancellation" or (trip["issue"] == "payment" and charges["settled_count"] > 1):
            trip["eligibility"] = "review"
        elif trip["issue"] == "fare":
            trip["eligibility"] = "adjust" if 0 < delta <= cap else "review" if delta > cap else "explain"
        else:
            trip["eligibility"] = "explain"
        return trip

    def list(self):
        return [self.trip(t["id"]) for t in self.trips]
