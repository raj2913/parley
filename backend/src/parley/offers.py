"""Deterministic validation of payment arrangements against client policy.

The model never decides whether an offer is acceptable. It proposes terms through a tool;
this module approves or rejects them. Only approved offers may be spoken (enforced by the
output guard) or recorded (enforced by the controller, which records offers itself).
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel

from parley.policy import ClientPolicy

CENT = Decimal("0.01")

OfferKind = Literal["plan", "settlement", "pay_in_full"]


def to_money(value: object) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a valid amount: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"not a valid amount: {value!r}")
    return amount


def ceil_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_CEILING)


def ceil_to(value: Decimal, step: Decimal) -> Decimal:
    return ((value / step).to_integral_value(rounding=ROUND_CEILING) * step).quantize(CENT)


class Offer(BaseModel):
    id: str
    kind: OfferKind
    hardship: bool = False
    monthly_amount: Decimal | None = None
    number_of_payments: int | None = None
    final_payment: Decimal | None = None
    lump_sum: Decimal | None = None
    first_payment_date: date
    total: Decimal

    def speakable_amounts(self) -> set[Decimal]:
        amounts = {self.total}
        for value in (self.monthly_amount, self.final_payment, self.lump_sum):
            if value is not None:
                amounts.add(value)
        return amounts

    def speakable_counts(self) -> set[int]:
        if self.number_of_payments is None:
            return {1}
        # "12 payments" and "11 payments of $X plus a final payment of $Y" are both accurate.
        return {self.number_of_payments, self.number_of_payments - 1}

    def describe(self) -> str:
        when = self.first_payment_date.strftime("%B %-d, %Y")
        if self.kind == "plan":
            n = self.number_of_payments
            if self.final_payment == self.monthly_amount:
                return f"{n} monthly payments of ${self.monthly_amount:,}, starting {when}"
            return (
                f"{n} monthly payments starting {when}: {n - 1} payments of "
                f"${self.monthly_amount:,} and a final payment of ${self.final_payment:,}"
            )
        if self.kind == "settlement":
            return f"a one-time settlement payment of ${self.lump_sum:,} on or before {when}"
        return f"a one-time payment of the full ${self.total:,} on or before {when}"


@dataclass
class Verdict:
    approved: bool
    offer: Offer | None = None
    reason: str | None = None
    guidance: dict[str, str] = field(default_factory=dict)

    def as_tool_result(self) -> dict:
        if self.approved and self.offer:
            return {
                "approved": True,
                "offer_id": self.offer.id,
                "terms": self.offer.describe(),
                "amounts_you_may_say": sorted(f"${a:,}" for a in self.offer.speakable_amounts()),
            }
        return {"approved": False, "reason": self.reason, **self.guidance}


def _check_date(on: date, today: date, within_days: int) -> str | None:
    latest = today + timedelta(days=within_days)
    if on < today:
        return f"date {on.isoformat()} is in the past"
    if on > latest:
        return f"date must be on or before {latest.isoformat()}"
    return None


def validate_plan(
    policy: ClientPolicy,
    *,
    offer_id: str,
    balance: Decimal,
    hardship: bool,
    today: date,
    monthly_amount: object,
    number_of_payments: int,
    first_payment_date: date,
) -> Verdict:
    limits = policy.plan_limits(hardship)
    floor = max(limits.min_monthly_payment, ceil_cents(balance / limits.max_payments))
    guidance = {
        "next_step": (
            "Do not offer or repeat the rejected terms. Call propose_payment_plan again with an "
            f"amount of at least ${floor:,} (number_of_payments = balance / amount, rounded up), "
            "and only present it once approved."
        ),
        "lowest_monthly_payment_allowed": f"${floor:,}",
        "max_payments_allowed": str(limits.max_payments),
        "latest_first_payment_date": (
            today + timedelta(days=limits.first_payment_within_days)
        ).isoformat(),
    }

    try:
        monthly = to_money(monthly_amount)
    except ValueError as exc:
        return Verdict(False, reason=str(exc), guidance=guidance)

    if monthly <= 0:
        return Verdict(False, reason="monthly amount must be positive", guidance=guidance)
    if monthly >= balance:
        return Verdict(
            False,
            reason="that covers the whole balance; use propose_pay_in_full",
            guidance=guidance,
        )
    if monthly < floor:
        return Verdict(
            False,
            reason=f"${monthly:,}/month is below what this account allows",
            guidance=guidance,
        )

    needed = int((balance / monthly).to_integral_value(rounding=ROUND_CEILING))
    if number_of_payments != needed:
        guidance["number_of_payments_for_this_amount"] = str(needed)
        guidance["next_step"] = (
            f"Call propose_payment_plan again with the same amount and number_of_payments={needed}."
        )
        return Verdict(
            False,
            reason=f"${monthly:,}/month pays off the balance in {needed} payments, "
            f"not {number_of_payments}",
            guidance=guidance,
        )
    if needed > limits.max_payments:
        return Verdict(False, reason="too many payments", guidance=guidance)

    if problem := _check_date(first_payment_date, today, limits.first_payment_within_days):
        return Verdict(False, reason=problem, guidance=guidance)

    final = balance - monthly * (needed - 1)
    offer = Offer(
        id=offer_id,
        kind="plan",
        hardship=hardship,
        monthly_amount=monthly,
        number_of_payments=needed,
        final_payment=final,
        first_payment_date=first_payment_date,
        total=balance,
    )
    return Verdict(True, offer=offer)


def validate_settlement(
    policy: ClientPolicy,
    *,
    offer_id: str,
    balance: Decimal,
    today: date,
    lump_sum: object,
    payment_date: date,
) -> Verdict:
    limits = policy.negotiation.settlement
    if not limits.enabled:
        return Verdict(False, reason="settlements are not available for this client")

    floor = ceil_cents(balance * limits.min_fraction_of_balance)
    counter = max(floor, ceil_to(balance * limits.counter_fraction_of_balance, Decimal("5")))
    counter = min(counter, balance)
    # The floor itself is never returned: a model that knows it will anchor on it.
    guidance = {
        "next_step": (
            "Do not mention or repeat the rejected amount. Call propose_settlement with "
            f"lump_sum_amount={counter} and present it once approved."
        ),
        "suggested_counter_offer": f"${counter:,}",
        "latest_payment_date": (today + timedelta(days=limits.pay_within_days)).isoformat(),
    }

    try:
        amount = to_money(lump_sum)
    except ValueError as exc:
        return Verdict(False, reason=str(exc), guidance=guidance)

    if amount >= balance:
        return Verdict(False, reason="that is the full balance; use propose_pay_in_full")
    if amount < floor:
        return Verdict(
            False,
            reason=f"${amount:,} is below the client's settlement authority",
            guidance=guidance,
        )
    if problem := _check_date(payment_date, today, limits.pay_within_days):
        return Verdict(False, reason=problem, guidance=guidance)

    offer = Offer(
        id=offer_id,
        kind="settlement",
        lump_sum=amount,
        first_payment_date=payment_date,
        total=amount,
    )
    return Verdict(True, offer=offer)


def validate_pay_in_full(
    policy: ClientPolicy,
    *,
    offer_id: str,
    balance: Decimal,
    today: date,
    payment_date: date,
) -> Verdict:
    within = policy.negotiation.pay_in_full.pay_within_days
    if problem := _check_date(payment_date, today, within):
        return Verdict(
            False,
            reason=problem,
            guidance={"latest_payment_date": (today + timedelta(days=within)).isoformat()},
        )
    offer = Offer(
        id=offer_id,
        kind="pay_in_full",
        lump_sum=balance,
        first_payment_date=payment_date,
        total=balance,
    )
    return Verdict(True, offer=offer)
