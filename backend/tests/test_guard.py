from decimal import Decimal

import pytest

from parley.guard import GuardContext, check_output, extract_amounts, extract_counts


def ctx(policy, *, verified=True, amounts=(), counts=(1, 12), dates=None):
    return GuardContext(
        verified=verified,
        allowed_amounts={Decimal(a) for a in amounts},
        allowed_counts=set(counts),
        prohibited_phrases=policy.prohibited_phrases,
        third_party_sensitive_terms=policy.third_party_sensitive_terms,
        creditor_name="Northwind Bank",
        allowed_dates=dates,
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("That's $1,240.00 total.", ["1240.00"]),
        ("I can do $103.34 a month", ["103.34"]),
        ("how about 20 bucks", ["20.00"]),
        ("fifty dollars a month", ["50.00"]),
        ("one hundred and three dollars and thirty four cents", ["103.34"]),
        ("twelve hundred dollars", ["1200.00"]),
        ("two thousand five hundred dollars", ["2500.00"]),
        ("Your account ending 4421 is ready", []),
    ],
)
def test_extract_amounts(text, expected):
    assert extract_amounts(text) == [Decimal(e) for e in expected]


def test_extract_counts():
    assert extract_counts("12 monthly payments") == [12]
    assert extract_counts("over eighteen months") == [18]
    assert extract_counts("a 6-month plan") == [6]


def test_unapproved_amount_is_blocked(policy):
    result = check_output("Sure, $20 a month works.", ctx(policy, amounts=["1240.00"]))
    assert [v["rule"] for v in result.violations] == ["unapproved_amount"]


def test_spelled_out_unapproved_amount_is_blocked(policy):
    result = check_output("We could do twenty dollars a month.", ctx(policy, amounts=["1240.00"]))
    assert any(v["rule"] == "unapproved_amount" for v in result.violations)


def test_approved_amounts_pass(policy):
    result = check_output(
        "I can set up 12 payments of $103.34, with a final payment of $103.26.",
        ctx(policy, amounts=["1240.00", "103.34", "103.26"], counts=[1, 11, 12]),
    )
    assert result.ok, result.violations


def test_unapproved_term_is_blocked(policy):
    result = check_output("We can spread it over 24 months.", ctx(policy, amounts=["1240.00"]))
    assert any(v["rule"] == "unapproved_term" for v in result.violations)


def test_nothing_about_the_debt_before_verification(policy):
    leaks = [
        "I'm calling about a debt you owe.",
        "It's regarding your Northwind Bank account.",
        "There's a balance of $1,240.00.",
    ]
    for text in leaks:
        result = check_output(text, ctx(policy, verified=False))
        assert any(v["rule"] == "pre_verification_disclosure" for v in result.violations), text
    assert check_output(
        "Could you confirm your date of birth and ZIP code?", ctx(policy, verified=False)
    ).ok


def test_threats_and_percentages_are_blocked(policy):
    threat = check_output("If you don't pay we may garnish your wages.", ctx(policy))
    assert any(v["rule"] == "prohibited_language" for v in threat.violations)
    pct = check_output("I can take 30% off.", ctx(policy))
    assert any(v["rule"] == "percentage" for v in pct.violations)


def test_markdown_is_stripped_for_speech(policy):
    result = check_output("**Great.** Here's the plan:\n- one\n- two", ctx(policy))
    assert result.text == "Great. Here's the plan: one two"


def test_split_decimals_are_rejoined(policy):
    result = check_output("The balance is $1,240 .00 today.", ctx(policy, amounts=["1240.00"]))
    assert result.ok and "$1,240.00" in result.text


def test_spoken_dates_must_match_an_approved_offer(policy):
    approved = {(11, 26), (10, 26)}
    wrong = check_output(
        "Payments of $68.89 starting on December 10th.",
        ctx(policy, amounts=["68.89"], dates=approved),
    )
    assert any(v["rule"] == "unapproved_date" for v in wrong.violations)
    right = check_output(
        "Payments of $68.89 starting Nov. 26.", ctx(policy, amounts=["68.89"], dates=approved)
    )
    assert right.ok, right.violations
