"""The turn pipeline.

    caller text
      -> classify intent (LLM, structured output)
      -> procedure controller decides: scripted reply, or a directive for the model
      -> response model drafts a reply, proposing terms only through validated tools
      -> output guard checks the draft; rejected drafts are retried, then replaced
      -> scripted disclosures are prepended, CRM actions are executed
      -> the whole turn is persisted as a trace

Nothing the model writes reaches the caller without passing the guard, and nothing it
proposes is recorded without passing the validator.
"""

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from datetime import time as clock_time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from parley.config import Settings
from parley.db import Account, Arrangement, Conversation, CrmEvent, Database, PromptVersion, Turn
from parley.guard import GuardContext, check_output
from parley.intents import IntentResult
from parley.llm import LLMCall, ModelClient, ModelRefusal
from parley.offers import Offer
from parley.policy import ClientPolicy
from parley.procedure import (
    AccountFacts,
    ConversationState,
    CrmAction,
    Decision,
    Directive,
    Stage,
    allowed_amounts,
    allowed_counts,
    allowed_dates,
    decide,
    opening_text,
    select_active_offer,
)
from parley.prompts import Prompt, agent_prompt, classifier_prompt
from parley.redact import redact
from parley.tools import TOOL_DEFINITIONS, run_propose_tool

Emit = Callable[[dict], Awaitable[None]]

CALL_CONNECTED = "[Outbound call connected. The caller has not spoken yet.]"

# Account statuses after which the agent must not place collection calls.
BLOCKING_STATUSES = {
    "cease_contact",
    "attorney_represented",
    "bankruptcy",
    "disputed",
    "validation_pending",
    "deceased",
    "hold",
}

STATUS_FOR_ACTION = {
    "arrangement_recorded": "arrangement",
    "dispute_recorded": "disputed",
    "cease_contact": "cease_contact",
    "attorney_represented": "attorney_represented",
    "bankruptcy_reported": "bankruptcy",
    "validation_requested": "validation_pending",
    "deceased_reported": "deceased",
    "distress_reported": "hold",
}

SECURITY_NOTICE = (
    "For your security, please don't read out payment or ID numbers on this call. "
    "I've removed what you just said from our records."
)


class CallBlocked(Exception):
    def __init__(self, message: str, code: str = "account_status"):
        super().__init__(message)
        self.code = code


class ConversationClosed(Exception):
    pass


async def _noop(_: dict) -> None:
    return None


@dataclass
class TurnOutcome:
    conversation_id: str
    turn_index: int
    agent_text: str
    stage: str
    route: str
    end_call: bool
    outcome: str | None


def _facts(account: Account) -> AccountFacts:
    return AccountFacts(
        id=account.id,
        reference=account.reference,
        first_name=account.first_name,
        last_name=account.last_name,
        date_of_birth=account.date_of_birth,
        zip_code=account.zip_code,
        original_creditor=account.original_creditor,
        balance=account.balance,
    )


def _text_of(content: list[dict]) -> str:
    return " ".join(b["text"].strip() for b in content if b.get("type") == "text" and b["text"])


class Agent:
    def __init__(
        self,
        db: Database,
        model: ModelClient,
        policy: ClientPolicy,
        settings: Settings,
    ):
        self.db = db
        self.model = model
        self.policy = policy
        self.settings = settings
        self.agent_prompt: Prompt = agent_prompt(policy)
        self.classifier_prompt: Prompt = classifier_prompt()
        self.tools = [TOOL_DEFINITIONS[name] for name in sorted(TOOL_DEFINITIONS)]

    # --- conversation lifecycle -----------------------------------------------------------

    async def start_conversation(
        self,
        account_id: str,
        *,
        channel: str,
        today: date | None = None,
        eval_run_id: str | None = None,
        replay_of: str | None = None,
        now: datetime | None = None,
        simulate_local_time: clock_time | None = None,
    ) -> TurnOutcome:
        async with self.db.session() as session, session.begin():
            account = await session.get(Account, account_id)
            if account is None:
                raise LookupError(f"account {account_id} not found")
            if account.status in BLOCKING_STATUSES:
                raise CallBlocked(f"account status is '{account.status}'; calls are not allowed")
            if channel not in ("eval", "replay"):
                self._check_calling_hours(account, now, simulate_local_time)

            # Concurrent calls may both see a new prompt version first; let one insert win.
            await session.execute(
                pg_insert(PromptVersion)
                .values(
                    [
                        {"ref": p.ref, "name": p.name, "version": p.version, "text": p.text}
                        for p in (self.agent_prompt, self.classifier_prompt)
                    ]
                )
                .on_conflict_do_nothing(index_elements=["ref"])
            )

            facts = _facts(account)
            state = ConversationState(disclosures_delivered=["recording"])
            text = opening_text(self.policy, facts)
            conversation = Conversation(
                account_id=account.id,
                channel=channel,
                stage=state.stage.value,
                state=state.model_dump(mode="json"),
                model_history=[
                    {"role": "user", "content": CALL_CONNECTED},
                    {"role": "assistant", "content": [{"type": "text", "text": text}]},
                ],
                today=today or date.today(),
                policy_version=self.policy.fingerprint,
                agent_prompt_ref=self.agent_prompt.ref,
                classifier_prompt_ref=self.classifier_prompt.ref,
                agent_model=self.settings.agent_model,
                eval_run_id=eval_run_id,
                replay_of=replay_of,
            )
            session.add(conversation)
            await session.flush()
            session.add(
                Turn(
                    conversation_id=conversation.id,
                    index=0,
                    caller_text=None,
                    agent_text=text,
                    route="opening",
                    stage_before=Stage.opening.value,
                    stage_after=Stage.opening.value,
                    state_after=state.model_dump(mode="json"),
                    disclosures=["recording"],
                )
            )
            return TurnOutcome(conversation.id, 0, text, state.stage.value, "opening", False, None)

    async def end_by_caller(self, conversation_id: str) -> str | None:
        """The caller hung up. Closes the call if the agent hadn't already ended it."""
        async with self.db.session() as session, session.begin():
            conv = await session.get(Conversation, conversation_id, with_for_update=True)
            if conv is None:
                raise LookupError(f"conversation {conversation_id} not found")
            if conv.status != "active":
                return conv.outcome
            state = ConversationState.model_validate(conv.state)
            state.stage = Stage.closed
            state.outcome = state.outcome or "caller_hung_up"
            conv.state = state.model_dump(mode="json")
            conv.stage = state.stage.value
            conv.outcome = state.outcome
            conv.status = "ended"
            conv.ended_at = datetime.now(UTC)
            session.add(
                CrmEvent(
                    account_id=conv.account_id,
                    conversation_id=conv.id,
                    type="caller_hung_up",
                    payload={"verified": state.verified},
                )
            )
            return conv.outcome

    def _check_calling_hours(
        self, account: Account, now: datetime | None, simulated: clock_time | None
    ) -> None:
        if simulated is not None:
            if not self.settings.allow_simulated_local_time:
                raise CallBlocked("simulated call times are disabled", code="simulation_disabled")
            local = simulated
        else:
            local = (now or datetime.now(UTC)).astimezone(ZoneInfo(account.timezone)).time()
        hours = self.policy.calling_hours
        if not hours.allows(local):
            raise CallBlocked(
                f"it's {local.strftime('%H:%M')} for this consumer ({account.timezone}); calls "
                f"are only allowed {hours.start.strftime('%H:%M')}–{hours.end.strftime('%H:%M')} "
                f"local time",
                code="outside_calling_hours",
            )

    async def handle_turn(
        self, conversation_id: str, caller_text: str, emit: Emit = _noop
    ) -> TurnOutcome:
        started = time.perf_counter()
        # Card and SSN digits are removed before the model, the database, or a log sees them.
        caller_text, redactions = redact(caller_text.strip())
        async with self.db.session() as session, session.begin():
            conv = await session.get(Conversation, conversation_id, with_for_update=True)
            if conv is None:
                raise LookupError(f"conversation {conversation_id} not found")
            if conv.status != "active":
                raise ConversationClosed("this call has ended")
            account = await session.get(Account, conv.account_id)
            facts = _facts(account)
            state = ConversationState.model_validate(conv.state)
            stage_before = state.stage
            today = conv.today
            turn_index = await self._next_turn_index(session, conv.id)
            llm_calls: list[LLMCall] = []

            await emit({"type": "turn_started", "turn_index": turn_index})

            intent = await self._classify(session, conv, state, facts, caller_text, llm_calls)
            if intent is None:
                decision = Decision(
                    route="classifier_unavailable",
                    scripted_text="I'm sorry, I didn't catch that. Could you say that again?",
                )
            else:
                await emit({"type": "intent", "intent": intent.model_dump(mode="json")})
                decision = decide(state, intent, facts, self.policy, today, self.settings.max_turns)
            await emit({"type": "route", "route": decision.route, "stage": state.stage.value})

            offers_before = len(state.offers)
            tool_trace: list[dict] = []
            guard_trace: dict = {}
            if decision.scripted_text is not None:
                spoken = decision.scripted_text
                new_messages = [
                    {"role": "user", "content": caller_text},
                    {"role": "assistant", "content": [{"type": "text", "text": spoken}]},
                ]
            else:
                spoken, new_messages, tool_trace, guard_trace = await self._generate(
                    conv,
                    state,
                    facts,
                    intent,
                    decision.directive,
                    caller_text,
                    today,
                    llm_calls,
                    emit,
                )

            approved_this_turn = state.offers[offers_before:]
            if active := select_active_offer(state, spoken, approved_this_turn):
                state.active_offer_id = active.id

            disclosures = [self.policy.disclosure_text(d) for d in decision.prefix_disclosures]
            state.disclosures_delivered.extend(decision.prefix_disclosures)
            notices = [SECURITY_NOTICE] if redactions else []
            agent_text = " ".join([*disclosures, *notices, spoken])

            crm_log = await self._apply_crm_actions(
                session, conv, account, state, decision.crm_actions
            )

            conv.state = state.model_dump(mode="json")
            conv.stage = state.stage.value
            conv.outcome = state.outcome
            conv.model_history = [*conv.model_history, *new_messages]
            if decision.end_call:
                conv.status = "ended"
                conv.ended_at = datetime.now(UTC)

            latency = int((time.perf_counter() - started) * 1000)
            session.add(
                Turn(
                    conversation_id=conv.id,
                    index=turn_index,
                    caller_text=caller_text,
                    agent_text=agent_text,
                    route=decision.route,
                    stage_before=stage_before.value,
                    stage_after=state.stage.value,
                    intent=intent.model_dump(mode="json") if intent else None,
                    directive=decision.directive.model_dump(mode="json")
                    if decision.directive
                    else None,
                    state_after=state.model_dump(mode="json"),
                    disclosures=decision.prefix_disclosures,
                    redactions=redactions,
                    tool_calls=tool_trace,
                    guard=guard_trace,
                    llm_calls=[c.as_trace() for c in llm_calls],
                    crm_actions=crm_log,
                    latency_ms=latency,
                )
            )

        outcome = TurnOutcome(
            conversation_id=conversation_id,
            turn_index=turn_index,
            agent_text=agent_text,
            stage=state.stage.value,
            route=decision.route,
            end_call=decision.end_call,
            outcome=state.outcome,
        )
        await emit(
            {
                "type": "agent_message",
                "turn_index": turn_index,
                "text": agent_text,
                "stage": outcome.stage,
                "route": outcome.route,
                "end_call": outcome.end_call,
                "outcome": outcome.outcome,
                "latency_ms": latency,
            }
        )
        return outcome

    # --- steps -------------------------------------------------------------------------------

    async def _next_turn_index(self, session: AsyncSession, conversation_id: str) -> int:
        result = await session.execute(
            select(Turn.index)
            .where(Turn.conversation_id == conversation_id)
            .order_by(Turn.index.desc())
            .limit(1)
        )
        return (result.scalar_one_or_none() or 0) + 1

    async def _classify(
        self,
        session: AsyncSession,
        conv: Conversation,
        state: ConversationState,
        facts: AccountFacts,
        caller_text: str,
        llm_calls: list[LLMCall],
    ) -> IntentResult | None:
        recent = (
            (
                await session.execute(
                    select(Turn)
                    .where(Turn.conversation_id == conv.id)
                    .order_by(Turn.index.desc())
                    .limit(4)
                )
            )
            .scalars()
            .all()
        )
        lines = []
        for turn in reversed(recent):
            if turn.caller_text:
                lines.append(f"Caller: {turn.caller_text}")
            lines.append(f"Agent: {turn.agent_text}")
        offer = state.active_offer
        user = "\n".join(
            [
                f"Today's date: {conv.today.isoformat()}",
                f"Call stage: {state.stage.value}",
                f"Offer on the table: {offer.describe() if offer else 'none'}",
                "",
                "Recent transcript:",
                *lines,
                "",
                "Label this new caller message:",
                f"<caller_message>{caller_text}</caller_message>",
            ]
        )
        try:
            result, call = await self.model.classify(
                system=self.classifier_prompt.text,
                system_ref=self.classifier_prompt.ref,
                user=user,
            )
        except ModelRefusal as refusal:
            llm_calls.append(refusal.call)
            return None
        llm_calls.append(call)
        return result

    def _render_directive(
        self,
        state: ConversationState,
        facts: AccountFacts,
        intent: IntentResult | None,
        directive: Directive,
        today: date,
        correction: str | None,
    ) -> str:
        lines = [
            "TURN DIRECTIVE",
            f"Stage: {directive.stage.value}. Identity verified: "
            f"{'yes' if state.verified else 'NO'}. Today: {today.isoformat()}.",
        ]
        if intent:
            flags = (
                f" (flags: {', '.join(intent.compliance_flags)})" if intent.compliance_flags else ""
            )
            lines.append(f"Caller intent: {intent.intent.value}{flags}. {intent.summary}")
        if state.verified:
            lines.append(
                f"Account facts: {facts.full_name}; original creditor "
                f"{facts.original_creditor}; balance ${facts.balance:,}; "
                f"reference {facts.reference}."
            )
            offer = state.active_offer
            lines.append(f"Offer on the table: {offer.describe() if offer else 'none'}.")
            if state.hardship:
                lines.append("Hardship: the caller has described financial hardship.")
        else:
            lines.append(f"You are trying to reach: {facts.full_name}.")
        lines.append(f"Goal: {directive.goal}")
        for rule in directive.rules:
            lines.append(f"Rule: {rule}")
        if directive.tools:
            lines.append(f"Tools you may use this turn: {', '.join(directive.tools)}.")
        else:
            lines.append("Do not call any tools this turn.")
        if correction:
            lines.append(correction)
        return "\n".join(lines)

    async def _generate(
        self,
        conv: Conversation,
        state: ConversationState,
        facts: AccountFacts,
        intent: IntentResult | None,
        directive: Directive,
        caller_text: str,
        today: date,
        llm_calls: list[LLMCall],
        emit: Emit,
    ) -> tuple[str, list[dict], list[dict], dict]:
        attempts: list[dict] = []
        tool_trace: list[dict] = []
        correction: str | None = None
        rejected: set[str] = set()

        for attempt in range(self.settings.max_guard_retries + 1):
            turn_messages: list[dict] = [
                {"role": "user", "content": caller_text},
                {
                    "role": "system",
                    "content": self._render_directive(
                        state, facts, intent, directive, today, correction
                    ),
                },
            ]
            drafted: list[str] = []
            failed = False
            for _ in range(self.settings.max_tool_iterations):
                try:
                    completion, call = await self.model.respond(
                        system=self.agent_prompt.text,
                        system_ref=self.agent_prompt.ref,
                        messages=[*conv.model_history, *turn_messages],
                        tools=self.tools,
                    )
                except ModelRefusal as refusal:
                    llm_calls.append(refusal.call)
                    failed = True
                    break
                llm_calls.append(call)
                turn_messages.append({"role": "assistant", "content": completion.content})
                if text := _text_of(completion.content):
                    drafted.append(text)

                tool_uses = [b for b in completion.content if b.get("type") == "tool_use"]
                if completion.stop_reason != "tool_use" or not tool_uses:
                    break
                results = []
                for block in tool_uses:
                    entry = await self._run_tool(
                        block, directive, state, facts, today, attempt, rejected
                    )
                    tool_trace.append(entry)
                    await emit({"type": "tool_call", **entry})
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block["id"],
                            "content": json.dumps(entry["result"]),
                            **({"is_error": True} if entry.get("error") else {}),
                        }
                    )
                turn_messages.append({"role": "user", "content": results})

            draft = " ".join(drafted)
            guard = check_output(draft, self._guard_context(state, facts, today))
            attempts.append(
                {
                    "attempt": attempt,
                    "draft": draft,
                    "violations": guard.violations,
                    "model_failed": failed,
                }
            )
            await emit(
                {
                    "type": "guard",
                    "attempt": attempt,
                    "ok": guard.ok and not failed,
                    "violations": guard.violations,
                }
            )
            if guard.ok and not failed:
                return (
                    guard.text,
                    turn_messages,
                    tool_trace,
                    {
                        "attempts": attempts,
                        "fallback_used": False,
                    },
                )
            correction = (
                "CORRECTION: your previous draft was rejected and the caller did not hear it. "
                "Problems: "
                + "; ".join(v["detail"] for v in guard.violations or [{"detail": "no reply"}])
                + ". Write a new reply that follows every rule."
            )

        # Every draft failed: say the controller's safe line instead. It is checked too, so a
        # badly written fallback shows up in the trace rather than silently reaching a caller.
        fallback = check_output(directive.fallback_text, self._guard_context(state, facts, today))
        messages = [
            {"role": "user", "content": caller_text},
            {
                "role": "system",
                "content": self._render_directive(state, facts, intent, directive, today, None),
            },
            {"role": "assistant", "content": [{"type": "text", "text": fallback.text}]},
        ]
        return (
            fallback.text,
            messages,
            tool_trace,
            {
                "attempts": attempts,
                "fallback_used": True,
                "fallback_violations": fallback.violations,
            },
        )

    async def _run_tool(
        self,
        block: dict,
        directive: Directive,
        state: ConversationState,
        facts: AccountFacts,
        today: date,
        attempt: int,
        rejected: set[str],
    ) -> dict:
        name, args = block["name"], block.get("input") or {}
        entry: dict = {"attempt": attempt, "name": name, "input": args}
        if name not in directive.tools:
            entry["result"] = {"approved": False, "reason": f"{name} is not available this turn"}
            entry["error"] = True
            return entry
        key = json.dumps([name, args], sort_keys=True)
        if key in rejected:
            # Small models loop on a rejected proposal; break the loop explicitly.
            entry["result"] = {
                "approved": False,
                "reason": "you already proposed exactly this and it was rejected; follow the "
                "next_step from that result instead",
            }
            entry["error"] = True
            return entry
        verdict = run_propose_tool(
            name, args, policy=self.policy, state=state, account=facts, today=today
        )
        if not verdict.approved:
            rejected.add(key)
        entry["result"] = verdict.as_tool_result()
        entry["approved"] = verdict.approved
        return entry

    def _guard_context(
        self, state: ConversationState, facts: AccountFacts, today: date
    ) -> GuardContext:
        return GuardContext(
            verified=state.verified,
            allowed_amounts=allowed_amounts(state, facts),
            allowed_counts=allowed_counts(state, self.policy),
            prohibited_phrases=self.policy.prohibited_phrases,
            third_party_sensitive_terms=self.policy.third_party_sensitive_terms,
            creditor_name=facts.original_creditor,
            agency_name=self.policy.client_name,
            allowed_dates=allowed_dates(state, today),
        )

    async def _apply_crm_actions(
        self,
        session: AsyncSession,
        conv: Conversation,
        account: Account,
        state: ConversationState,
        actions: list[CrmAction],
    ) -> list[dict]:
        log = []
        for action in actions:
            if action.type == "arrangement_recorded":
                offer = Offer.model_validate(action.payload["offer"])
                session.add(
                    Arrangement(
                        account_id=account.id,
                        conversation_id=conv.id,
                        kind=offer.kind,
                        monthly_amount=offer.monthly_amount,
                        number_of_payments=offer.number_of_payments,
                        final_payment=offer.final_payment,
                        lump_sum=offer.lump_sum,
                        first_payment_date=offer.first_payment_date,
                        total=offer.total,
                        hardship=offer.hardship,
                        policy_version=self.policy.fingerprint,
                    )
                )
            session.add(
                CrmEvent(
                    account_id=account.id,
                    conversation_id=conv.id,
                    type=action.type,
                    payload=action.payload,
                )
            )
            if status := STATUS_FOR_ACTION.get(action.type):
                account.status = status
            log.append({"type": action.type, "payload": action.payload})
        return log
