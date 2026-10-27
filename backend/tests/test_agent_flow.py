"""End-to-end turn pipeline with a scripted model and a real Postgres."""

from datetime import UTC, datetime

from conftest import TODAY
from fakes import FakeModel, intent, text, tool_use
from sqlalchemy import select

from parley.agent import Agent, CallBlocked
from parley.db import Arrangement, CrmEvent
from parley.evals.checks import run_checks
from parley.evals.runner import load_record
from parley.intents import Intent

VERIFY = dict(date_of_birth="1985-03-14", zip_code="94110")
# 11:00 in Los Angeles: inside calling hours.
MIDDAY = datetime(2026, 10, 26, 18, 0, tzinfo=UTC)


async def start(agent: Agent, account_id: str) -> str:
    opening = await agent.start_conversation(account_id, channel="chat", today=TODAY, now=MIDDAY)
    assert "May I please speak with Maria Lopez" in opening.agent_text
    assert "may be recorded" in opening.agent_text
    return opening.conversation_id


async def test_full_call_ends_in_a_recorded_plan(db, account_id, policy, settings):
    model = FakeModel(
        intents=[
            intent(Intent.confirm_identity, **VERIFY),
            intent(Intent.offer_payment_amount, amount=20, amount_frequency="monthly"),
            intent(Intent.accept_offer),
        ],
        responses=[
            text(
                "Thanks, Maria. I'm calling about your Northwind Bank account, which has a balance of $1,240.00. How would you like to handle it?"
            ),
            # The model tries the caller's $20 first; the validator refuses.
            tool_use(
                "propose_payment_plan",
                monthly_amount=20,
                number_of_payments=62,
                first_payment_date="2026-11-01",
            ),
            tool_use(
                "propose_payment_plan",
                monthly_amount=103.34,
                number_of_payments=12,
                first_payment_date="2026-11-01",
            ),
            text(
                "I can't do that amount, but I can set up 12 monthly payments of $103.34 starting November 1. Would that work?"
            ),
        ],
    )
    agent = Agent(db, model, policy, settings)
    conv_id = await start(agent, account_id)

    verified = await agent.handle_turn(conv_id, "Yes, this is Maria. 03/14/1985, 94110.")
    assert verified.agent_text.startswith(policy.disclosure_text("mini_miranda"))

    offered = await agent.handle_turn(conv_id, "I can only do 20 bucks a month.")
    assert "$103.34" in offered.agent_text and "$20" not in offered.agent_text

    closed = await agent.handle_turn(conv_id, "Okay, that works.")
    assert closed.end_call and closed.outcome == "arrangement"
    assert "12 monthly payments" in closed.agent_text

    async with db.session() as session:
        arrangement = (await session.execute(select(Arrangement))).scalar_one()
        assert str(arrangement.monthly_amount) == "103.34"
        assert arrangement.number_of_payments == 12

    record = await load_record(db, conv_id)
    failed = [r for r in run_checks(record, policy, None) if r.gating and not r.passed]
    assert not failed, failed


async def test_guard_replaces_a_draft_that_offers_unapproved_terms(
    db, account_id, policy, settings
):
    bad = text("Sure, $20 a month is fine.")
    model = FakeModel(
        intents=[
            intent(Intent.confirm_identity, **VERIFY),
            intent(Intent.offer_payment_amount, amount=20, amount_frequency="monthly"),
        ],
        responses=[
            text(
                "Thanks. Your Northwind Bank balance is $1,240.00. How would you like to handle it?"
            ),
            bad,
            bad,
            bad,
        ],
    )
    agent = Agent(db, model, policy, settings)
    conv_id = await start(agent, account_id)
    await agent.handle_turn(conv_id, "Speaking. 1985-03-14, 94110.")
    outcome = await agent.handle_turn(conv_id, "Twenty a month.")

    assert "$20" not in outcome.agent_text
    assert outcome.agent_text.startswith("I'm not able to set it up at that amount.")
    # The retry carried the guard's reason back to the model.
    retry_directive = model.respond_requests[2]["messages"][-1]["content"]
    assert "CORRECTION" in retry_directive and "$20.00" in retry_directive

    record = await load_record(db, conv_id)
    last = record.turns[-1]
    assert last["guard"]["fallback_used"] is True
    assert len(last["guard"]["attempts"]) == settings.max_guard_retries + 1


async def test_tools_cannot_be_used_before_verification(db, account_id, policy, settings):
    model = FakeModel(
        intents=[intent(Intent.ask_purpose)],
        responses=[
            tool_use("propose_settlement", lump_sum_amount=100, payment_date="2026-11-01"),
            text("It's a personal business matter for Maria Lopez. Am I speaking with her?"),
        ],
    )
    agent = Agent(db, model, policy, settings)
    conv_id = await start(agent, account_id)
    outcome = await agent.handle_turn(conv_id, "Who is this? What's it about?")
    assert "personal business matter" in outcome.agent_text
    record = await load_record(db, conv_id)
    assert record.final_state["offers"] == []


async def test_cease_request_blocks_future_calls(db, account_id, policy, settings):
    model = FakeModel(intents=[intent(Intent.other, flags=["cease_contact"])], responses=[])
    agent = Agent(db, model, policy, settings)
    conv_id = await start(agent, account_id)
    outcome = await agent.handle_turn(conv_id, "Stop calling this number.")
    assert outcome.end_call and outcome.outcome == "cease_contact"

    async with db.session() as session:
        events = (await session.execute(select(CrmEvent.type))).scalars().all()
    assert events == ["cease_contact"]

    try:
        await agent.start_conversation(account_id, channel="chat", today=TODAY, now=MIDDAY)
    except CallBlocked:
        pass
    else:
        raise AssertionError("a cease-contact account must not be called again")


async def test_history_is_append_only_across_turns(db, account_id, policy, settings):
    model = FakeModel(
        intents=[intent(Intent.confirm_identity), intent(Intent.provide_verification, **VERIFY)],
        responses=[
            text("Thanks. Could you confirm your date of birth and ZIP code?"),
            text(
                "Thank you. This is about your Northwind Bank account with a balance of $1,240.00."
            ),
        ],
    )
    agent = Agent(db, model, policy, settings)
    conv_id = await start(agent, account_id)
    await agent.handle_turn(conv_id, "Yes, speaking.")
    await agent.handle_turn(conv_id, "March 14 1985, 94110")
    first, second = (r["messages"] for r in model.respond_requests)
    assert second[: len(first)] == first


async def test_checks_catch_a_leak_the_guard_would_have_stopped(db, account_id, policy, settings):
    model = FakeModel(intents=[intent(Intent.ask_purpose)], responses=[text("Fine.")])
    agent = Agent(db, model, policy, settings)
    conv_id = await start(agent, account_id)
    await agent.handle_turn(conv_id, "What is this about?")
    record = await load_record(db, conv_id)
    record.turns[-1]["agent_text"] = "It's about the $1,240.00 debt you owe Northwind Bank."
    results = {r.name: r for r in run_checks(record, policy, None)}
    assert not results["no_third_party_disclosure"].passed


async def test_calling_hours_are_enforced_with_a_demo_override(db, account_id, policy, settings):
    from datetime import time

    agent = Agent(db, FakeModel([], []), policy, settings)
    late = datetime(2026, 10, 27, 7, 30, tzinfo=UTC)  # 00:30 in Los Angeles
    try:
        await agent.start_conversation(account_id, channel="chat", today=TODAY, now=late)
    except CallBlocked as exc:
        assert exc.code == "outside_calling_hours" and "America/Los_Angeles" in str(exc)
    else:
        raise AssertionError("a call at 00:30 local must be refused")
    ok = await agent.start_conversation(
        account_id, channel="chat", today=TODAY, now=late, simulate_local_time=time(10, 0)
    )
    assert ok.turn_index == 0


async def test_card_numbers_never_reach_the_model_or_the_database(db, account_id, policy, settings):
    model = FakeModel(
        intents=[intent(Intent.confirm_identity)],
        responses=[text("Thanks. Could you confirm your date of birth and ZIP code?")],
    )
    agent = Agent(db, model, policy, settings)
    conv_id = await start(agent, account_id)
    outcome = await agent.handle_turn(conv_id, "Yes it's me, take my card 4111 1111 1111 1111")
    assert outcome.agent_text.startswith("For your security")
    sent = str(model.respond_requests)
    assert "4111" not in sent
    record = await load_record(db, conv_id)
    assert "4111" not in record.turns[-1]["caller_text"]
