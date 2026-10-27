import logging
from contextlib import asynccontextmanager
from datetime import date, time

import anthropic
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from parley.agent import Agent, CallBlocked, ConversationClosed, TurnOutcome
from parley.config import Settings, get_settings
from parley.db import (
    Account,
    Arrangement,
    Conversation,
    CrmEvent,
    Database,
    EvalRun,
    PromptVersion,
    Turn,
)
from parley.llm import ModelClient, ModelUnavailable, make_model_client
from parley.policy import load_policy

log = logging.getLogger("parley.api")


class StartConversation(BaseModel):
    account_id: str
    channel: str = "chat"
    today: date | None = None
    # "HH:MM": place a demo call as if it were this time for the consumer.
    simulate_local_time: time | None = None


class CallerMessage(BaseModel):
    text: str


def _turn_json(turn: Turn) -> dict:
    return {
        "index": turn.index,
        "caller_text": turn.caller_text,
        "agent_text": turn.agent_text,
        "route": turn.route,
        "stage_before": turn.stage_before,
        "stage_after": turn.stage_after,
        "intent": turn.intent,
        "directive": turn.directive,
        "state_after": turn.state_after,
        "disclosures": turn.disclosures,
        "tool_calls": turn.tool_calls,
        "guard": turn.guard,
        "llm_calls": turn.llm_calls,
        "crm_actions": turn.crm_actions,
        "redactions": turn.redactions,
        "latency_ms": turn.latency_ms,
        "created_at": turn.created_at.isoformat(),
    }


def _account_json(account: Account) -> dict:
    return {
        "id": account.id,
        "reference": account.reference,
        "first_name": account.first_name,
        "last_name": account.last_name,
        "date_of_birth": account.date_of_birth.isoformat(),
        "zip_code": account.zip_code,
        "phone": account.phone,
        "original_creditor": account.original_creditor,
        "balance": str(account.balance),
        "status": account.status,
        "timezone": account.timezone,
    }


def _conversation_summary(conv: Conversation, account: Account, turn_count: int) -> dict:
    return {
        "id": conv.id,
        "account": {
            "id": account.id,
            "reference": account.reference,
            "name": f"{account.first_name} {account.last_name}",
        },
        "channel": conv.channel,
        "status": conv.status,
        "stage": conv.stage,
        "outcome": conv.outcome,
        "turn_count": turn_count,
        "eval_run_id": conv.eval_run_id,
        "replay_of": conv.replay_of,
        "policy_version": conv.policy_version,
        "agent_prompt_ref": conv.agent_prompt_ref,
        "agent_model": conv.agent_model,
        "started_at": conv.started_at.isoformat(),
        "ended_at": conv.ended_at.isoformat() if conv.ended_at else None,
    }


def _outcome_json(outcome: TurnOutcome) -> dict:
    return {
        "conversation_id": outcome.conversation_id,
        "turn_index": outcome.turn_index,
        "agent_text": outcome.agent_text,
        "stage": outcome.stage,
        "route": outcome.route,
        "end_call": outcome.end_call,
        "outcome": outcome.outcome,
    }


def create_app(settings: Settings | None = None, model: ModelClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    policy = load_policy(settings.policy_path)
    db = Database(settings.database_url)
    agent = Agent(db, model or make_model_client(settings), policy, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await db.create_all()
        yield
        await db.dispose()

    app = FastAPI(title="Parley", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.agent = agent
    app.state.db = db

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/config")
    async def config() -> dict:
        return {
            "policy_version": policy.fingerprint,
            "agent_prompt_ref": agent.agent_prompt.ref,
            "classifier_prompt_ref": agent.classifier_prompt.ref,
            "provider": settings.provider,
            "agent_model": settings.agent_model,
            "agent_effort": settings.agent_effort,
            "classifier_model": settings.classifier_model,
            "agent_name": policy.agent_name,
            "client_name": policy.client_name,
        }

    @app.get("/api/policy")
    async def get_policy() -> dict:
        return {
            "fingerprint": policy.fingerprint,
            "policy": policy.model_dump(mode="json"),
            "yaml": settings.policy_path.read_text(),
        }

    @app.get("/api/prompts")
    async def list_prompts() -> list[dict]:
        async with db.session() as session:
            rows = (
                await session.execute(select(PromptVersion).order_by(PromptVersion.first_seen_at))
            ).scalars()
            current = {agent.agent_prompt.ref, agent.classifier_prompt.ref}
            return [
                {
                    "ref": p.ref,
                    "name": p.name,
                    "version": p.version,
                    "text": p.text,
                    "current": p.ref in current,
                    "first_seen_at": p.first_seen_at.isoformat(),
                }
                for p in rows
            ]

    @app.get("/api/accounts")
    async def list_accounts() -> list[dict]:
        async with db.session() as session:
            rows = (
                await session.execute(
                    select(Account).where(Account.is_fixture.is_(False)).order_by(Account.reference)
                )
            ).scalars()
            return [_account_json(a) for a in rows]

    @app.get("/api/accounts/{account_id}")
    async def account_detail(account_id: str) -> dict:
        async with db.session() as session:
            account = await session.get(Account, account_id)
            if account is None:
                raise HTTPException(404, "account not found")
            events = (
                await session.execute(
                    select(CrmEvent)
                    .where(CrmEvent.account_id == account_id)
                    .order_by(CrmEvent.created_at.desc())
                )
            ).scalars()
            arrangements = (
                await session.execute(
                    select(Arrangement)
                    .where(Arrangement.account_id == account_id)
                    .order_by(Arrangement.created_at.desc())
                )
            ).scalars()
            return {
                **_account_json(account),
                "events": [
                    {
                        "id": e.id,
                        "type": e.type,
                        "payload": e.payload,
                        "conversation_id": e.conversation_id,
                        "created_at": e.created_at.isoformat(),
                    }
                    for e in events
                ],
                "arrangements": [
                    {
                        "id": a.id,
                        "kind": a.kind,
                        "monthly_amount": str(a.monthly_amount) if a.monthly_amount else None,
                        "number_of_payments": a.number_of_payments,
                        "final_payment": str(a.final_payment) if a.final_payment else None,
                        "lump_sum": str(a.lump_sum) if a.lump_sum else None,
                        "first_payment_date": a.first_payment_date.isoformat(),
                        "total": str(a.total),
                        "hardship": a.hardship,
                        "conversation_id": a.conversation_id,
                        "created_at": a.created_at.isoformat(),
                    }
                    for a in arrangements
                ],
            }

    @app.post("/api/accounts/{account_id}/reset")
    async def reset_account(account_id: str) -> dict:
        """Demo convenience: clear a status that would otherwise block new calls."""
        async with db.session() as session, session.begin():
            account = await session.get(Account, account_id)
            if account is None:
                raise HTTPException(404, "account not found")
            account.status = "active"
            return _account_json(account)

    @app.post("/api/conversations")
    async def start_conversation(body: StartConversation) -> dict:
        try:
            outcome = await agent.start_conversation(
                body.account_id,
                channel=body.channel,
                today=body.today,
                simulate_local_time=body.simulate_local_time,
            )
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except CallBlocked as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc
        return _outcome_json(outcome)

    @app.get("/api/conversations")
    async def list_conversations(
        channel: str | None = None, eval_run_id: str | None = None, limit: int = 100
    ) -> list[dict]:
        async with db.session() as session:
            turn_counts = (
                select(Turn.conversation_id, func.count().label("n"))
                .group_by(Turn.conversation_id)
                .subquery()
            )
            query = (
                select(Conversation, Account, func.coalesce(turn_counts.c.n, 0))
                .join(Account, Account.id == Conversation.account_id)
                .outerjoin(turn_counts, turn_counts.c.conversation_id == Conversation.id)
                .order_by(Conversation.started_at.desc())
                .limit(min(limit, 500))
            )
            if channel:
                query = query.where(Conversation.channel == channel)
            if eval_run_id:
                query = query.where(Conversation.eval_run_id == eval_run_id)
            rows = (await session.execute(query)).all()
            # Opening turn is the agent's greeting, not a caller exchange.
            return [_conversation_summary(c, a, max(n - 1, 0)) for c, a, n in rows]

    @app.get("/api/conversations/{conversation_id}")
    async def conversation_detail(conversation_id: str) -> dict:
        async with db.session() as session:
            conv = (
                await session.execute(
                    select(Conversation)
                    .where(Conversation.id == conversation_id)
                    .options(selectinload(Conversation.turns))
                )
            ).scalar_one_or_none()
            if conv is None:
                raise HTTPException(404, "conversation not found")
            account = await session.get(Account, conv.account_id)
            return {
                **_conversation_summary(conv, account, max(len(conv.turns) - 1, 0)),
                "account": _account_json(account),
                "today": conv.today.isoformat(),
                "classifier_prompt_ref": conv.classifier_prompt_ref,
                "state": conv.state,
                "turns": [_turn_json(t) for t in conv.turns],
            }

    @app.post("/api/conversations/{conversation_id}/turns")
    async def post_turn(conversation_id: str, body: CallerMessage) -> dict:
        try:
            outcome = await agent.handle_turn(conversation_id, body.text)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ConversationClosed as exc:
            raise HTTPException(409, str(exc)) from exc
        except ModelUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        return _outcome_json(outcome)

    @app.post("/api/conversations/{conversation_id}/hangup")
    async def hang_up(conversation_id: str) -> dict:
        try:
            outcome = await agent.end_by_caller(conversation_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"conversation_id": conversation_id, "outcome": outcome}

    @app.websocket("/ws/conversations/{conversation_id}")
    async def conversation_socket(ws: WebSocket, conversation_id: str) -> None:
        await ws.accept()

        async def emit(event: dict) -> None:
            await ws.send_json(event)

        try:
            while True:
                message = await ws.receive_json()
                if (
                    message.get("type") != "caller_message"
                    or not str(message.get("text", "")).strip()
                ):
                    await emit({"type": "error", "message": "expected a non-empty caller_message"})
                    continue
                try:
                    await agent.handle_turn(conversation_id, message["text"], emit)
                except ConversationClosed as exc:
                    await emit({"type": "error", "message": str(exc), "closed": True})
                except LookupError as exc:
                    await emit({"type": "error", "message": str(exc)})
                except ModelUnavailable as exc:
                    log.warning("model unavailable: %s", exc)
                    await emit({"type": "error", "message": str(exc)})
                except anthropic.APIError as exc:
                    log.exception("model API error")
                    await emit(
                        {"type": "error", "message": f"model error: {exc.__class__.__name__}"}
                    )
        except WebSocketDisconnect:
            return

    @app.get("/api/eval-runs")
    async def list_eval_runs(limit: int = 50) -> list[dict]:
        async with db.session() as session:
            runs = (
                await session.execute(
                    select(EvalRun).order_by(EvalRun.started_at.desc()).limit(min(limit, 200))
                )
            ).scalars()
            return [_eval_run_json(r) for r in runs]

    @app.get("/api/eval-runs/{run_id}")
    async def eval_run_detail(run_id: str) -> dict:
        async with db.session() as session:
            run = (
                await session.execute(
                    select(EvalRun)
                    .where(EvalRun.id == run_id)
                    .options(selectinload(EvalRun.results))
                )
            ).scalar_one_or_none()
            if run is None:
                raise HTTPException(404, "eval run not found")
            return {
                **_eval_run_json(run),
                "results": [
                    {
                        "id": r.id,
                        "scenario_id": r.scenario_id,
                        "conversation_id": r.conversation_id,
                        "passed": r.passed,
                        "error": r.error,
                        "metrics": r.metrics,
                        "duration_ms": r.duration_ms,
                    }
                    for r in run.results
                ],
            }

    @app.get("/api/scenarios")
    async def list_scenarios() -> list[dict]:
        from parley.evals.scenarios import load_scenarios

        return [
            {"id": s.id, "description": s.description, "tags": s.tags, "caller_mode": s.caller.mode}
            for s in load_scenarios(settings.scenarios_dir)
        ]

    return app


def _eval_run_json(run: EvalRun) -> dict:
    return {
        "id": run.id,
        "kind": run.kind,
        "label": run.label,
        "git_sha": run.git_sha,
        "policy_version": run.policy_version,
        "agent_prompt_ref": run.agent_prompt_ref,
        "classifier_prompt_ref": run.classifier_prompt_ref,
        "agent_model": run.agent_model,
        "status": run.status,
        "passed": run.passed,
        "summary": run.summary,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
