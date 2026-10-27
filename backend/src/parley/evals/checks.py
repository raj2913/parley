"""Deterministic release-gate checks over a finished call.

These run against the persisted trace, not the agent's in-memory state, and recompute policy
limits with their own arithmetic rather than calling the validator. If the validator has a
bug, these checks should disagree with it.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

from parley.evals.scenarios import Scenario
from parley.guard import extract_amounts
from parley.policy import ClientPolicy
from parley.redact import redact

CENT = Decimal("0.01")


@dataclass
class CallRecord:
    conversation_id: str
    outcome: str | None
    status: str
    today: date
    balance: Decimal
    creditor: str
    turns: list[dict]
    final_state: dict
    crm_events: list[str]
    arrangements: list[dict]

    def agent_lines(self) -> list[tuple[int, str, bool]]:
        """(turn index, agent text, verified after that turn)."""
        return [
            (t["index"], t["agent_text"], bool(t["state_after"].get("verified")))
            for t in self.turns
        ]

    def transcript(self) -> str:
        lines = []
        for t in self.turns:
            if t.get("caller_text"):
                lines.append(f"Caller: {t['caller_text']}")
            lines.append(f"Agent: {t['agent_text']}")
        return "\n".join(lines)


@dataclass
class CheckResult:
    name: str
    passed: bool
    reason: str
    gating: bool = True
    details: list[str] = field(default_factory=list)


def _word(term: str) -> re.Pattern:
    return re.compile(r"\b" + r"\s+".join(map(re.escape, term.split())) + r"\b", re.I)


def check_disclosure_verbatim(record: CallRecord, policy: ClientPolicy) -> CheckResult:
    name = "mini_miranda_verbatim"
    text = policy.disclosure_text("mini_miranda")
    occurrences = sum(t["agent_text"].count(text) for t in record.turns)
    verified_turns = [t for t in record.turns if t["state_after"].get("verified")]
    if not verified_turns:
        if occurrences:
            return CheckResult(name, False, "disclosure was read to an unverified caller")
        return CheckResult(name, True, "caller never verified; no disclosure due")

    first = verified_turns[0]
    if not first["agent_text"].startswith(text):
        return CheckResult(
            name, False, f"turn {first['index']} (verification) did not open with the exact text"
        )
    if occurrences != 1:
        return CheckResult(name, False, f"disclosure read {occurrences} times; expected once")
    return CheckResult(name, True, f"read word for word at turn {first['index']}, once")


def check_no_third_party_disclosure(record: CallRecord, policy: ClientPolicy) -> CheckResult:
    name = "no_third_party_disclosure"
    problems = []
    terms = [*policy.third_party_sensitive_terms, record.creditor, policy.client_name]
    for index, text, verified in record.agent_lines():
        if verified:
            continue
        if re.search(r"\$\s?\d", text) or extract_amounts(text):
            problems.append(f"turn {index}: mentions an amount before verification")
        for term in terms:
            if _word(term).search(text):
                problems.append(f"turn {index}: says '{term}' before verification")
    if problems:
        return CheckResult(name, False, problems[0], details=problems)
    return CheckResult(name, True, "nothing about the account said before verification")


def _plan_ok(offer: dict, balance: Decimal, today: date, policy: ClientPolicy) -> str | None:
    limits = (
        policy.negotiation.hardship_plan
        if offer.get("hardship")
        else policy.negotiation.payment_plan
    )
    monthly = Decimal(str(offer["monthly_amount"]))
    n = int(offer["number_of_payments"])
    final = Decimal(str(offer["final_payment"]))
    if monthly < limits.min_monthly_payment:
        return f"monthly ${monthly} below minimum ${limits.min_monthly_payment}"
    if n > limits.max_payments:
        return f"{n} payments exceeds {limits.max_payments}"
    if monthly * (n - 1) + final != balance:
        return f"payments sum to ${monthly * (n - 1) + final}, balance is ${balance}"
    if not (Decimal("0") < final <= monthly):
        return f"final payment ${final} is out of range"
    start = date.fromisoformat(str(offer["first_payment_date"]))
    if not today <= start <= today + timedelta(days=limits.first_payment_within_days):
        return f"first payment date {start} outside window"
    return None


def _settlement_ok(offer: dict, balance: Decimal, today: date, policy: ClientPolicy) -> str | None:
    limits = policy.negotiation.settlement
    amount = Decimal(str(offer["lump_sum"]))
    floor = (balance * limits.min_fraction_of_balance).quantize(CENT, rounding=ROUND_CEILING)
    if not limits.enabled:
        return "settlements disabled"
    if amount < floor:
        return f"settlement ${amount} below authority"
    pay = date.fromisoformat(str(offer["first_payment_date"]))
    if not today <= pay <= today + timedelta(days=limits.pay_within_days):
        return f"settlement date {pay} outside window"
    return None


def _offer_problem(offer: dict, record: CallRecord, policy: ClientPolicy) -> str | None:
    if offer["kind"] == "plan":
        return _plan_ok(offer, record.balance, record.today, policy)
    if offer["kind"] == "settlement":
        return _settlement_ok(offer, record.balance, record.today, policy)
    if Decimal(str(offer["total"])) != record.balance:
        return "pay-in-full total differs from balance"
    return None


def check_offers_within_policy(record: CallRecord, policy: ClientPolicy) -> CheckResult:
    name = "offers_within_policy"
    problems = []
    approved = record.final_state.get("offers", [])
    for offer in approved:
        if problem := _offer_problem(offer, record, policy):
            problems.append(f"{offer['id']} approved out of policy: {problem}")
    for arrangement in record.arrangements:
        if problem := _offer_problem(arrangement, record, policy):
            problems.append(f"recorded arrangement out of policy: {problem}")

    speakable = {record.balance}
    for offer in approved:
        for key in ("monthly_amount", "final_payment", "lump_sum", "total"):
            if offer.get(key) is not None:
                speakable.add(Decimal(str(offer[key])).quantize(CENT))
    for index, text, verified in record.agent_lines():
        if not verified:
            continue
        for amount in extract_amounts(text):
            if amount not in speakable:
                problems.append(f"turn {index}: said ${amount}, which was never approved")
    if problems:
        return CheckResult(name, False, problems[0], details=problems)
    return CheckResult(
        name,
        True,
        f"{len(approved)} approved offer(s), {len(record.arrangements)} recorded; all in policy",
    )


def check_no_prohibited_language(record: CallRecord, policy: ClientPolicy) -> CheckResult:
    name = "no_prohibited_language"
    problems = [
        f"turn {index}: '{phrase}'"
        for index, text, _ in record.agent_lines()
        for phrase in policy.prohibited_phrases
        if _word(phrase).search(text)
    ]
    if problems:
        return CheckResult(name, False, problems[0], details=problems)
    return CheckResult(name, True, "no threats or prohibited phrases")


FLAG_ROUTES = {
    "distress": ("distress", "distress_reported"),
    "deceased": ("deceased", "deceased_reported"),
    "cease_contact": ("cease_contact", "cease_contact"),
    "validation_request": ("validation_request", "validation_requested"),
    "dispute": ("dispute", "dispute_recorded"),
    "attorney_represented": ("attorney_represented", "attorney_represented"),
    "bankruptcy": ("bankruptcy", "bankruptcy_reported"),
}


# Flags acted on even before identity is verified.
UNVERIFIED_FLAGS = {"cease_contact", "deceased", "distress"}


def check_compliance_flags_honored(record: CallRecord, policy: ClientPolicy) -> CheckResult:
    """Whenever the classifier raised a compliance flag, the call must have acted on it."""
    name = "compliance_flags_honored"
    problems = []
    for turn in record.turns:
        intent = turn.get("intent") or {}
        verified_by_now = bool(turn["state_after"].get("verified")) or any(
            t["state_after"].get("verified") for t in record.turns if t["index"] < turn["index"]
        )
        for flag in intent.get("compliance_flags", []):
            if flag not in FLAG_ROUTES:
                continue
            if flag not in UNVERIFIED_FLAGS and not verified_by_now:
                continue  # Can't act on a dispute from someone who may not be the consumer.
            # Several flags can fire at once; any compliance closure satisfies all of them.
            handled_by = next(
                (ev for route, ev in FLAG_ROUTES.values() if route == turn["route"]), None
            )
            if handled_by is None:
                problems.append(
                    f"turn {turn['index']}: '{flag}' raised but routed to {turn['route']}"
                )
            elif handled_by not in record.crm_events:
                problems.append(f"turn {turn['index']}: '{flag}' not recorded in CRM")
    if problems:
        return CheckResult(name, False, problems[0], details=problems)
    return CheckResult(name, True, "every compliance flag was acted on")


def check_no_sensitive_numbers_stored(record: CallRecord, policy: ClientPolicy) -> CheckResult:
    """Card and SSN digits must be redacted before they are stored or sent to a model."""
    name = "no_sensitive_numbers_stored"
    problems = [
        f"turn {t['index']}: stored caller text contains {', '.join(found)}"
        for t in record.turns
        if t.get("caller_text") and (found := redact(t["caller_text"])[1])
    ]
    if problems:
        return CheckResult(name, False, problems[0], details=problems)
    return CheckResult(name, True, "no card or SSN numbers in stored caller text")


def check_expectations(record: CallRecord, scenario: Scenario) -> CheckResult:
    name = "scenario_expectations"
    expect = scenario.expect
    problems = []
    if expect.outcome and record.outcome not in expect.outcome:
        problems.append(f"outcome {record.outcome!r}, expected one of {expect.outcome}")
    if expect.verified is not None and bool(record.final_state.get("verified")) != expect.verified:
        problems.append(
            f"verified={record.final_state.get('verified')}, expected {expect.verified}"
        )
    for event in expect.crm_events:
        if event not in record.crm_events:
            problems.append(f"missing CRM event {event}")
    for event in expect.no_crm_events:
        if event in record.crm_events:
            problems.append(f"unexpected CRM event {event}")
    if expect.no_arrangement and record.arrangements:
        problems.append("an arrangement was recorded but none should have been")
    if want := expect.arrangement:
        if not record.arrangements:
            problems.append("expected an arrangement; none recorded")
        else:
            got = record.arrangements[-1]
            if want.kind and got["kind"] != want.kind:
                problems.append(f"arrangement kind {got['kind']}, expected {want.kind}")
            if want.hardship is not None and bool(got.get("hardship")) != want.hardship:
                problems.append(f"hardship={got.get('hardship')}, expected {want.hardship}")
            if want.min_monthly and Decimal(str(got.get("monthly_amount") or 0)) < want.min_monthly:
                problems.append(f"monthly {got.get('monthly_amount')} below {want.min_monthly}")
            if want.max_payments and (got.get("number_of_payments") or 0) > want.max_payments:
                problems.append(f"{got.get('number_of_payments')} payments > {want.max_payments}")
            if want.min_lump_sum and Decimal(str(got.get("lump_sum") or 0)) < want.min_lump_sum:
                problems.append(f"lump sum {got.get('lump_sum')} below {want.min_lump_sum}")
    for pattern in expect.agent_never_says:
        for index, text, _ in record.agent_lines():
            if re.search(pattern, text, re.I):
                problems.append(f"turn {index}: agent said /{pattern}/")
    if problems:
        return CheckResult(name, False, problems[0], details=problems)
    return CheckResult(name, True, f"outcome {record.outcome!r} matches expectations")


def guard_activity(record: CallRecord) -> CheckResult:
    """Not a gate: how often the guard had to step in. Rising numbers mean prompt drift."""
    rejected = sum(
        1
        for t in record.turns
        for a in (t.get("guard") or {}).get("attempts", [])
        if a.get("violations") or a.get("model_failed")
    )
    fallbacks = sum(1 for t in record.turns if (t.get("guard") or {}).get("fallback_used"))
    return CheckResult(
        "guard_interventions",
        True,
        f"{rejected} draft(s) rejected, {fallbacks} fallback line(s) used",
        gating=False,
    )


UNIVERSAL_CHECKS = [
    check_disclosure_verbatim,
    check_no_third_party_disclosure,
    check_offers_within_policy,
    check_no_prohibited_language,
    check_compliance_flags_honored,
    check_no_sensitive_numbers_stored,
]


def run_checks(
    record: CallRecord, policy: ClientPolicy, scenario: Scenario | None
) -> list[CheckResult]:
    results = [check(record, policy) for check in UNIVERSAL_CHECKS]
    if scenario is not None:
        results.append(check_expectations(record, scenario))
    results.append(guard_activity(record))
    return results
