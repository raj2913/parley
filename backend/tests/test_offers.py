from datetime import timedelta
from decimal import Decimal

from conftest import TODAY

from parley.offers import validate_pay_in_full, validate_plan, validate_settlement

BALANCE = Decimal("1240.00")


def plan(policy, monthly, n, *, hardship=False, start=TODAY, balance=BALANCE):
    return validate_plan(
        policy,
        offer_id="offer-1",
        balance=balance,
        hardship=hardship,
        today=TODAY,
        monthly_amount=monthly,
        number_of_payments=n,
        first_payment_date=start,
    )


def test_plan_at_the_floor_is_approved_with_exact_final_payment(policy):
    # 1240 / 12 = 103.333.. -> floor 103.34; 11 x 103.34 = 1136.74; final = 103.26
    verdict = plan(policy, "103.34", 12)
    assert verdict.approved
    offer = verdict.offer
    assert offer.final_payment == Decimal("103.26")
    assert offer.monthly_amount * 11 + offer.final_payment == BALANCE


def test_plan_one_cent_under_the_floor_is_rejected(policy):
    verdict = plan(policy, "103.33", 13)
    assert not verdict.approved
    assert verdict.guidance["lowest_monthly_payment_allowed"] == "$103.34"


def test_plan_below_absolute_minimum_is_rejected_even_for_small_balances(policy):
    verdict = plan(policy, "40", 6, balance=Decimal("212.00"))
    assert not verdict.approved
    assert verdict.guidance["lowest_monthly_payment_allowed"] == "$50.00"


def test_plan_payment_count_must_match_the_amount(policy):
    verdict = plan(policy, "150", 6)
    assert not verdict.approved
    assert verdict.guidance["number_of_payments_for_this_amount"] == "9"


def test_hardship_unlocks_longer_plans_but_still_has_a_floor(policy):
    balance = Decimal("9410.75")
    assert not plan(policy, "550", 18, balance=balance).approved
    approved = plan(policy, "550", 18, balance=balance, hardship=True)
    assert approved.approved and approved.offer.hardship
    assert not plan(policy, "500", 19, balance=balance, hardship=True).approved


def test_plan_start_date_window(policy):
    assert not plan(policy, "155", 8, start=TODAY - timedelta(days=1)).approved
    assert plan(policy, "155", 8, start=TODAY + timedelta(days=30)).approved
    assert not plan(policy, "155", 8, start=TODAY + timedelta(days=31)).approved


def test_plan_rejects_garbage_amounts(policy):
    assert not plan(policy, "NaN", 12).approved
    assert not plan(policy, "-50", 12).approved
    assert not plan(policy, "abc", 12).approved


def test_settlement_floor_is_enforced_but_never_revealed(policy):
    low = validate_settlement(
        policy,
        offer_id="o",
        balance=BALANCE,
        today=TODAY,
        lump_sum="300",
        payment_date=TODAY,
    )
    assert not low.approved
    as_seen_by_model = str(low.as_tool_result())
    assert "868" not in as_seen_by_model  # 70% floor
    assert low.guidance["suggested_counter_offer"] == "$1,055.00"

    at_floor = validate_settlement(
        policy,
        offer_id="o",
        balance=BALANCE,
        today=TODAY,
        lump_sum="868.00",
        payment_date=TODAY,
    )
    assert at_floor.approved


def test_pay_in_full_must_be_within_window(policy):
    ok = validate_pay_in_full(
        policy, offer_id="o", balance=BALANCE, today=TODAY, payment_date=TODAY + timedelta(days=5)
    )
    assert ok.approved and ok.offer.total == BALANCE
    late = validate_pay_in_full(
        policy, offer_id="o", balance=BALANCE, today=TODAY, payment_date=TODAY + timedelta(days=60)
    )
    assert not late.approved
