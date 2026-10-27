"""Tools the response model may call. They propose; the validator in offers.py disposes."""

from datetime import date

from parley.offers import Verdict, validate_pay_in_full, validate_plan, validate_settlement
from parley.policy import ClientPolicy
from parley.procedure import AccountFacts, ConversationState

_DATE = {"type": "string", "description": "YYYY-MM-DD"}

TOOL_DEFINITIONS: dict[str, dict] = {
    "propose_payment_plan": {
        "name": "propose_payment_plan",
        "description": (
            "Check a monthly payment plan against the client's policy. Returns approved terms "
            "you may present, or a rejection with guidance. Plans pay the full balance: "
            "number_of_payments must equal the balance divided by monthly_amount, rounded up."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "monthly_amount": {"type": "number", "description": "Dollars per month."},
                "number_of_payments": {"type": "integer"},
                "first_payment_date": _DATE,
            },
            "required": ["monthly_amount", "number_of_payments", "first_payment_date"],
            "additionalProperties": False,
        },
    },
    "propose_settlement": {
        "name": "propose_settlement",
        "description": (
            "Check a one-time settlement for less than the full balance. Returns approved terms, "
            "or a rejection with a suggested counter-offer you may propose instead."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "lump_sum_amount": {"type": "number", "description": "Dollars, paid once."},
                "payment_date": _DATE,
            },
            "required": ["lump_sum_amount", "payment_date"],
            "additionalProperties": False,
        },
    },
    "propose_pay_in_full": {
        "name": "propose_pay_in_full",
        "description": "Check a one-time payment of the full balance on a given date.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"payment_date": _DATE},
            "required": ["payment_date"],
            "additionalProperties": False,
        },
    },
}


def _parse_date(value: object) -> date:
    return date.fromisoformat(str(value))


def run_propose_tool(
    name: str,
    args: dict,
    *,
    policy: ClientPolicy,
    state: ConversationState,
    account: AccountFacts,
    today: date,
) -> Verdict:
    """Validate a proposal. Approved offers are added to state; nothing is recorded in the CRM."""
    if not state.verified:
        return Verdict(False, reason="identity is not verified; no offers can be made")
    offer_id = state.next_offer_id()
    try:
        if name == "propose_payment_plan":
            verdict = validate_plan(
                policy,
                offer_id=offer_id,
                balance=account.balance,
                hardship=state.hardship,
                today=today,
                monthly_amount=args["monthly_amount"],
                number_of_payments=int(args["number_of_payments"]),
                first_payment_date=_parse_date(args["first_payment_date"]),
            )
        elif name == "propose_settlement":
            verdict = validate_settlement(
                policy,
                offer_id=offer_id,
                balance=account.balance,
                today=today,
                lump_sum=args["lump_sum_amount"],
                payment_date=_parse_date(args["payment_date"]),
            )
        elif name == "propose_pay_in_full":
            verdict = validate_pay_in_full(
                policy,
                offer_id=offer_id,
                balance=account.balance,
                today=today,
                payment_date=_parse_date(args["payment_date"]),
            )
        else:
            return Verdict(False, reason=f"unknown tool {name}")
    except (KeyError, TypeError, ValueError) as exc:
        return Verdict(False, reason=f"invalid arguments: {exc}")

    if verdict.approved and verdict.offer:
        state.offers.append(verdict.offer)
    return verdict
