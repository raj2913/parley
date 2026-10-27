"""Client policy: the single source of truth for what the agent may offer and must say."""

import hashlib
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr


class CallingHours(BaseModel):
    start: time
    end: time

    def allows(self, local: time) -> bool:
        return self.start <= local < self.end


class Verification(BaseModel):
    factors: list[Literal["dob", "zip"]]
    max_attempts: int = Field(ge=1)


class Disclosure(BaseModel):
    when: Literal["opening", "after_verification"]
    text: str


class PlanLimits(BaseModel):
    min_monthly_payment: Decimal
    max_payments: int = Field(ge=1)
    first_payment_within_days: int = Field(ge=0)


class SettlementLimits(BaseModel):
    enabled: bool
    min_fraction_of_balance: Decimal
    counter_fraction_of_balance: Decimal
    pay_within_days: int


class PayInFullLimits(BaseModel):
    pay_within_days: int


class Negotiation(BaseModel):
    payment_plan: PlanLimits
    hardship_plan: PlanLimits
    settlement: SettlementLimits
    pay_in_full: PayInFullLimits


class ClientPolicy(BaseModel):
    client_id: str
    version: str
    client_name: str
    agent_name: str
    callback_number: str
    calling_hours: CallingHours
    verification: Verification
    disclosures: dict[str, Disclosure]
    negotiation: Negotiation
    prohibited_phrases: list[str]
    third_party_sensitive_terms: list[str]
    _digest: str = PrivateAttr(default="")

    def disclosure_text(self, disclosure_id: str) -> str:
        return " ".join(self.disclosures[disclosure_id].text.split())

    def plan_limits(self, hardship: bool) -> PlanLimits:
        return self.negotiation.hardship_plan if hardship else self.negotiation.payment_plan

    @property
    def fingerprint(self) -> str:
        return f"{self.client_id}@{self.version}#{self._digest}"


def load_policy(path: Path) -> ClientPolicy:
    raw = path.read_text()
    policy = ClientPolicy.model_validate(yaml.safe_load(raw))
    # Catch edits that forgot to bump the version: the digest travels with every trace.
    policy._digest = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return policy
