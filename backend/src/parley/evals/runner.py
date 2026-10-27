"""Runs scenarios and replays against the current agent, and records the results.

A run passes only if every gating metric passes on every call. CI runs this before merge.
"""

import asyncio
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from parley.agent import Agent
from parley.config import Settings
from parley.db import Account, Arrangement, Conversation, CrmEvent, Database, EvalResult, EvalRun
from parley.evals.checks import CallRecord
from parley.evals.metrics import ModelJudge, score_call
from parley.evals.scenarios import Scenario
from parley.evals.simulator import SimulatedCaller


@dataclass
class CallResult:
    scenario_id: str
    conversation_id: str | None
    passed: bool
    metrics: list[dict]
    error: str | None
    duration_ms: int


def git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


async def load_record(db: Database, conversation_id: str) -> CallRecord:
    async with db.session() as session:
        conv = (
            await session.execute(
                select(Conversation)
                .where(Conversation.id == conversation_id)
                .options(selectinload(Conversation.turns))
            )
        ).scalar_one()
        account = await session.get(Account, conv.account_id)
        events = (
            (
                await session.execute(
                    select(CrmEvent.type).where(CrmEvent.conversation_id == conversation_id)
                )
            )
            .scalars()
            .all()
        )
        arrangements = (
            (
                await session.execute(
                    select(Arrangement).where(Arrangement.conversation_id == conversation_id)
                )
            )
            .scalars()
            .all()
        )
        return CallRecord(
            conversation_id=conv.id,
            outcome=conv.outcome,
            status=conv.status,
            today=conv.today,
            balance=account.balance,
            creditor=account.original_creditor,
            turns=[
                {
                    "index": t.index,
                    "caller_text": t.caller_text,
                    "agent_text": t.agent_text,
                    "route": t.route,
                    "intent": t.intent,
                    "state_after": t.state_after,
                    "guard": t.guard,
                }
                for t in conv.turns
            ],
            final_state=conv.state,
            crm_events=list(events),
            arrangements=[
                {
                    "kind": a.kind,
                    "monthly_amount": a.monthly_amount,
                    "number_of_payments": a.number_of_payments,
                    "final_payment": a.final_payment,
                    "lump_sum": a.lump_sum,
                    "first_payment_date": a.first_payment_date.isoformat(),
                    "total": a.total,
                    "hardship": a.hardship,
                }
                for a in arrangements
            ],
        )


async def _fixture_account(db: Database, spec: dict, label: str) -> str:
    async with db.session() as session, session.begin():
        account = Account(
            reference=f"EV-{label}-{uuid.uuid4().hex[:6]}"[:32],
            phone="000-000-0000",
            is_fixture=True,
            **spec,
        )
        session.add(account)
        await session.flush()
        return account.id


class EvalRunner:
    def __init__(
        self,
        db: Database,
        agent: Agent,
        settings: Settings,
        *,
        judges: bool = True,
        concurrency: int = 4,
    ):
        self.db = db
        self.agent = agent
        self.settings = settings
        self.judge = ModelJudge(settings, agent.model) if judges else None
        self.semaphore = asyncio.Semaphore(concurrency)

    async def _create_run(self, kind: str, label: str | None) -> str:
        async with self.db.session() as session, session.begin():
            run = EvalRun(
                kind=kind,
                label=label,
                git_sha=git_sha(),
                policy_version=self.agent.policy.fingerprint,
                agent_prompt_ref=self.agent.agent_prompt.ref,
                classifier_prompt_ref=self.agent.classifier_prompt.ref,
                agent_model=self.settings.agent_model,
            )
            session.add(run)
            await session.flush()
            return run.id

    async def _finish_run(self, run_id: str, results: list[CallResult]) -> EvalRun:
        failed = [r for r in results if not r.passed]
        summary = {
            "total": len(results),
            "passed": len(results) - len(failed),
            "failed": len(failed),
            "failed_scenarios": [r.scenario_id for r in failed],
            "metric_failures": _metric_failure_counts(results),
        }
        async with self.db.session() as session, session.begin():
            run = await session.get(EvalRun, run_id)
            for r in results:
                session.add(
                    EvalResult(
                        run_id=run_id,
                        scenario_id=r.scenario_id,
                        conversation_id=r.conversation_id,
                        passed=r.passed,
                        error=r.error,
                        metrics=r.metrics,
                        duration_ms=r.duration_ms,
                    )
                )
            run.status = "finished"
            run.passed = not failed
            run.summary = summary
            run.finished_at = datetime.now(UTC)
            return run

    async def _drive(self, conversation_id: str, opening: str, lines, max_turns: int) -> None:
        agent_text = opening
        for _ in range(max_turns):
            caller_text = await lines(agent_text)
            if caller_text is None:
                break
            outcome = await self.agent.handle_turn(conversation_id, caller_text)
            agent_text = outcome.agent_text
            if outcome.end_call:
                return
        # The caller ran out of things to say (or hung up) before the agent closed the call.
        await self.agent.end_by_caller(conversation_id)

    async def _score(
        self,
        scenario_id: str,
        conversation_id: str | None,
        scenario: Scenario | None,
        started: float,
        error: str | None,
    ) -> CallResult:
        metrics: list[dict] = []
        if conversation_id and error is None:
            record = await load_record(self.db, conversation_id)
            metrics = await score_call(record, scenario, self.agent.policy, self.judge)
        passed = error is None and all(m["success"] for m in metrics if m["gating"])
        return CallResult(
            scenario_id,
            conversation_id,
            passed,
            metrics,
            error,
            int((time.perf_counter() - started) * 1000),
        )

    async def run_scenario(self, scenario: Scenario, run_id: str) -> CallResult:
        async with self.semaphore:
            started = time.perf_counter()
            conversation_id = None
            try:
                account_id = await _fixture_account(
                    self.db, scenario.account.model_dump(), scenario.id[:16]
                )
                opening = await self.agent.start_conversation(
                    account_id, channel="eval", today=scenario.today, eval_run_id=run_id
                )
                conversation_id = opening.conversation_id
                if scenario.caller.mode == "scripted":
                    script = iter(scenario.caller.turns)

                    async def lines(_: str) -> str | None:
                        return next(script, None)

                    max_turns = len(scenario.caller.turns)
                else:
                    lines = SimulatedCaller(scenario, self.settings, self.agent.model).next_line
                    max_turns = scenario.caller.max_turns
                await self._drive(conversation_id, opening.agent_text, lines, max_turns)
                return await self._score(scenario.id, conversation_id, scenario, started, None)
            except Exception as exc:
                return await self._score(
                    scenario.id,
                    conversation_id,
                    scenario,
                    started,
                    f"{exc.__class__.__name__}: {exc}",
                )

    async def run_suite(
        self, scenarios: list[Scenario], label: str | None = None
    ) -> tuple[EvalRun, list[CallResult]]:
        run_id = await self._create_run("suite", label)
        results = list(await asyncio.gather(*(self.run_scenario(s, run_id) for s in scenarios)))
        return await self._finish_run(run_id, results), results

    async def replay_one(self, source_id: str, run_id: str) -> CallResult:
        """Re-run a recorded call's caller lines against the current agent."""
        async with self.semaphore:
            started = time.perf_counter()
            conversation_id = None
            try:
                async with self.db.session() as session:
                    source = (
                        await session.execute(
                            select(Conversation)
                            .where(Conversation.id == source_id)
                            .options(selectinload(Conversation.turns))
                        )
                    ).scalar_one()
                    original = await session.get(Account, source.account_id)
                    spec = {
                        "first_name": original.first_name,
                        "last_name": original.last_name,
                        "date_of_birth": original.date_of_birth,
                        "zip_code": original.zip_code,
                        "original_creditor": original.original_creditor,
                        "balance": original.balance,
                    }
                    caller_lines = [t.caller_text for t in source.turns if t.caller_text]
                account_id = await _fixture_account(self.db, spec, "replay")
                opening = await self.agent.start_conversation(
                    account_id,
                    channel="replay",
                    today=source.today,
                    eval_run_id=run_id,
                    replay_of=source_id,
                )
                conversation_id = opening.conversation_id
                script = iter(caller_lines)

                async def lines(_: str) -> str | None:
                    return next(script, None)

                await self._drive(conversation_id, opening.agent_text, lines, len(caller_lines))
                result = await self._score(source_id, conversation_id, None, started, None)
            except Exception as exc:
                result = await self._score(
                    source_id, conversation_id, None, started, f"{exc.__class__.__name__}: {exc}"
                )
            if conversation_id:
                async with self.db.session() as session:
                    replayed = await session.get(Conversation, conversation_id)
                    same = replayed.outcome == source.outcome
                # Informational: a scripted replay can legitimately diverge once the agent
                # says something different from the original call.
                result.metrics.append(
                    {
                        "name": "outcome_matches_original",
                        "kind": "check",
                        "gating": False,
                        "score": 1.0 if same else 0.0,
                        "threshold": None,
                        "success": same,
                        "reason": f"original {source.outcome!r}, replay {replayed.outcome!r}",
                        "details": [],
                    }
                )
            return result

    async def run_replay(
        self, source_ids: list[str], label: str | None = None
    ) -> tuple[EvalRun, list[CallResult]]:
        run_id = await self._create_run("replay", label)
        results = list(await asyncio.gather(*(self.replay_one(s, run_id) for s in source_ids)))
        return await self._finish_run(run_id, results), results


def _metric_failure_counts(results: list[CallResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        for m in r.metrics:
            if m["gating"] and not m["success"]:
                counts[m["name"]] = counts.get(m["name"], 0) + 1
    return counts


async def replayable_conversations(db: Database, limit: int) -> list[str]:
    async with db.session() as session:
        rows = await session.execute(
            select(Conversation.id)
            .where(Conversation.channel.in_(["chat", "voice"]), Conversation.status == "ended")
            .order_by(Conversation.started_at.desc())
            .limit(limit)
        )
        return list(rows.scalars())


async def capture_scenario(
    db: Database, conversation_id: str, scenario_id: str, directory: Path, tags: list[str]
) -> Path:
    """Turn a recorded call into a scripted regression scenario."""
    async with db.session() as session:
        conv = (
            await session.execute(
                select(Conversation)
                .where(Conversation.id == conversation_id)
                .options(selectinload(Conversation.turns))
            )
        ).scalar_one()
        account = await session.get(Account, conv.account_id)
        arrangements = (
            (
                await session.execute(
                    select(Arrangement).where(Arrangement.conversation_id == conversation_id)
                )
            )
            .scalars()
            .all()
        )

    expect: dict = {"outcome": conv.outcome, "verified": bool(conv.state.get("verified"))}
    if arrangements:
        expect["arrangement"] = {"kind": arrangements[-1].kind}
    data = {
        "id": scenario_id,
        "description": f"Captured from conversation {conversation_id}. Edit to describe the "
        "behaviour this protects.",
        "tags": ["captured", *tags],
        "today": conv.today.isoformat(),
        "account": {
            "first_name": account.first_name,
            "last_name": account.last_name,
            "date_of_birth": account.date_of_birth.isoformat(),
            "zip_code": account.zip_code,
            "original_creditor": account.original_creditor,
            "balance": str(account.balance),
        },
        "caller": {
            "mode": "scripted",
            "turns": [t.caller_text for t in conv.turns if t.caller_text],
        },
        "expect": expect,
    }
    path = directory / f"{scenario_id}.yaml"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100))
    return path
