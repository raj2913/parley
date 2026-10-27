from datetime import date
from decimal import Decimal

from conftest import TODAY
from fakes import intent

from parley.intents import Intent
from parley.offers import Offer
from parley.procedure import ConversationState, Stage, decide


def step(state, policy, maria, *args, **kwargs):
    return decide(state, intent(*args, **kwargs), maria, policy, TODAY, max_turns=30)


def verified_state() -> ConversationState:
    return ConversationState(stage=Stage.negotiation, verified=True, identity_confirmed=True)


def test_cease_request_ends_the_call_at_any_stage(policy, maria):
    for state in (ConversationState(), verified_state()):
        d = step(state, policy, maria, Intent.other, flags=["cease_contact"])
        assert d.route == "cease_contact" and d.end_call
        assert [a.type for a in d.crm_actions] == ["cease_contact"]
        assert state.stage == Stage.closed


def test_wrong_party_closes_without_mentioning_the_debt(policy, maria):
    state = ConversationState()
    d = step(state, policy, maria, Intent.wrong_party)
    assert d.end_call and "debt" not in d.scripted_text.lower()


def test_verification_success_prefixes_the_disclosure(policy, maria):
    state = ConversationState()
    d = step(
        state,
        policy,
        maria,
        Intent.confirm_identity,
        date_of_birth="1985-03-14",
        zip_code="94110",
    )
    assert state.verified and state.stage == Stage.negotiation
    assert d.prefix_disclosures == ["mini_miranda"]
    assert d.directive and "$1,240.00" in d.directive.goal


def test_verification_collects_factors_across_turns(policy, maria):
    state = ConversationState()
    step(state, policy, maria, Intent.confirm_identity)
    d = step(state, policy, maria, Intent.provide_verification, date_of_birth="1985-03-14")
    assert not state.verified and "ZIP" in d.directive.goal
    step(state, policy, maria, Intent.provide_verification, zip_code="94110-1234")
    assert state.verified


def test_three_mismatches_end_the_call(policy, maria):
    state = ConversationState(stage=Stage.verification, identity_confirmed=True)
    for _ in range(3):
        d = step(
            state,
            policy,
            maria,
            Intent.provide_verification,
            date_of_birth="1990-01-01",
            zip_code="94110",
        )
    assert d.route == "verification_failed" and d.end_call
    assert not state.verified


def test_dispute_is_not_acted_on_before_verification(policy, maria):
    state = ConversationState(stage=Stage.verification, identity_confirmed=True)
    d = step(state, policy, maria, Intent.other, flags=["dispute"])
    assert not d.end_call
    assert "NOT verified" in " ".join(d.directive.rules)


def test_verify_and_dispute_in_one_breath(policy, maria):
    state = ConversationState(stage=Stage.verification, identity_confirmed=True)
    d = step(
        state,
        policy,
        maria,
        Intent.provide_verification,
        flags=["dispute"],
        date_of_birth="1985-03-14",
        zip_code="94110",
    )
    assert d.route == "dispute" and d.end_call
    assert d.prefix_disclosures == ["mini_miranda"]
    assert [a.type for a in d.crm_actions] == ["dispute_recorded"]


def test_accepting_an_offer_records_it_from_state_not_from_the_model(policy, maria):
    state = verified_state()
    offer = Offer(
        id="offer-1",
        kind="plan",
        monthly_amount=Decimal("155.00"),
        number_of_payments=8,
        final_payment=Decimal("155.00"),
        first_payment_date=date(2026, 11, 1),
        total=Decimal("1240.00"),
    )
    state.offers.append(offer)
    state.active_offer_id = "offer-1"
    d = step(state, policy, maria, Intent.accept_offer)
    assert d.route == "arrangement_recorded" and d.end_call
    assert d.crm_actions[0].payload["offer"]["monthly_amount"] == "155.00"
    assert "8 monthly payments of $155.00" in d.scripted_text


def test_accepting_with_nothing_on_the_table_does_not_record(policy, maria):
    state = verified_state()
    d = step(state, policy, maria, Intent.accept_offer)
    assert not d.end_call and not d.crm_actions


def test_hardship_flag_is_remembered(policy, maria):
    state = verified_state()
    d = step(state, policy, maria, Intent.other, flags=["hardship"])
    assert state.hardship and "hardship" in d.directive.goal


def test_max_turns_closes_politely(policy, maria):
    state = verified_state()
    state.turn_count = 30
    d = step(state, policy, maria, Intent.other)
    assert d.route == "max_turns" and d.end_call


def test_third_party_gets_a_limited_content_message_then_a_callback_is_booked(policy, maria):
    state = ConversationState()
    d = step(state, policy, maria, Intent.party_unavailable, summary="brother says she's out")
    assert not d.end_call and state.awaiting_callback_time
    text = d.scripted_text
    assert policy.callback_number in text and "Jordan" in text
    for leak in ("Acme", "Northwind", "debt", "$"):
        assert leak not in text
    d = step(state, policy, maria, Intent.other, callback_time="after 6pm")
    assert d.end_call and d.crm_actions[0].payload["when"] == "after 6pm"


def test_distress_outranks_everything_and_gives_crisis_line(policy, maria):
    state = verified_state()
    d = step(state, policy, maria, Intent.accept_offer, flags=["distress", "cease_contact"])
    assert d.route == "distress" and d.end_call and "988" in d.scripted_text


def test_deceased_is_handled_before_verification(policy, maria):
    state = ConversationState()
    d = step(state, policy, maria, Intent.wrong_party, flags=["deceased"])
    assert d.route == "deceased" and "debt" not in d.scripted_text.lower()


def test_validation_request_pauses_collection(policy, maria):
    state = verified_state()
    d = step(state, policy, maria, Intent.other, flags=["validation_request"])
    assert d.route == "validation_request" and d.crm_actions[0].type == "validation_requested"


def test_human_request_books_a_callback(policy, maria):
    state = verified_state()
    d = step(state, policy, maria, Intent.request_human)
    assert state.awaiting_callback_time == "request_human" and not d.end_call
    d = step(state, policy, maria, Intent.other, callback_time="tomorrow morning")
    assert d.end_call and d.crm_actions[0].payload["reason"] == "request_human"


def test_legitimacy_question_before_verification_reveals_nothing(policy, maria):
    state = ConversationState()
    d = step(state, policy, maria, Intent.question_legitimacy)
    assert "Acme" not in d.scripted_text and policy.callback_number in d.scripted_text
    assert state.stage == Stage.opening and not d.end_call


def test_language_request_and_unclear_input(policy, maria):
    d = step(ConversationState(), policy, maria, Intent.language_request, language="Spanish")
    assert d.end_call and d.crm_actions[0].payload["language"] == "Spanish"
    state = ConversationState()
    d = step(state, policy, maria, Intent.unclear)
    assert not d.end_call and state.stage == Stage.opening


def test_at_work_asks_for_a_better_time(policy, maria):
    state = verified_state()
    d = step(state, policy, maria, Intent.other, flags=["do_not_call_at_work"])
    assert d.crm_actions[0].type == "work_calls_prohibited" and state.awaiting_callback_time


def test_compliance_flags_outrank_other_intents(policy, maria):
    # Found by the eval gate: "send me proof" was labelled a legitimacy question.
    state = verified_state()
    d = step(state, policy, maria, Intent.question_legitimacy, flags=["validation_request"])
    assert d.route == "validation_request" and d.end_call
