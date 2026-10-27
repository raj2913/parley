"""Eval scenarios: a fictional account, a caller (scripted lines or a simulated persona), and
what must be true when the call ends."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

DEFAULT_TODAY = date(2026, 10, 26)


class ScenarioAccount(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date
    zip_code: str
    original_creditor: str
    balance: Decimal


class Caller(BaseModel):
    mode: Literal["scripted", "simulated"]
    turns: list[str] = Field(default_factory=list)
    persona: str | None = None
    goal: str | None = None
    # What the simulated caller believes their details are (defaults to the account's).
    knows_dob: str | None = None
    knows_zip: str | None = None
    max_turns: int = 12

    @model_validator(mode="after")
    def _check(self) -> "Caller":
        if self.mode == "scripted" and not self.turns:
            raise ValueError("scripted callers need turns")
        if self.mode == "simulated" and not (self.persona and self.goal):
            raise ValueError("simulated callers need a persona and a goal")
        return self


class ArrangementExpectation(BaseModel):
    kind: Literal["plan", "settlement", "pay_in_full"] | None = None
    hardship: bool | None = None
    min_monthly: Decimal | None = None
    max_payments: int | None = None
    min_lump_sum: Decimal | None = None


class Expectations(BaseModel):
    outcome: list[str] | None = None
    verified: bool | None = None
    crm_events: list[str] = Field(default_factory=list)
    no_crm_events: list[str] = Field(default_factory=list)
    arrangement: ArrangementExpectation | None = None
    no_arrangement: bool = False
    agent_never_says: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _outcome_list(cls, data: dict) -> dict:
        if isinstance(data, dict) and isinstance(data.get("outcome"), str):
            data = {**data, "outcome": [data["outcome"]]}
        return data


class Scenario(BaseModel):
    id: str
    description: str
    tags: list[str] = Field(default_factory=list)
    today: date = DEFAULT_TODAY
    account: ScenarioAccount
    caller: Caller
    expect: Expectations = Field(default_factory=Expectations)
    judges: list[str] = Field(default_factory=lambda: ["professional_conduct", "responsiveness"])
    source: str | None = None


def load_scenarios(directory: Path, tags: list[str] | None = None) -> list[Scenario]:
    scenarios = []
    for path in sorted(directory.glob("*.yaml")):
        scenario = Scenario.model_validate(yaml.safe_load(path.read_text()))
        scenario.source = path.name
        if tags and not set(tags) & set(scenario.tags):
            continue
        scenarios.append(scenario)
    ids = [s.id for s in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate scenario ids")
    return scenarios
