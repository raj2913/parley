import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Json = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# --- CRM -----------------------------------------------------------------------------------


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    reference: Mapped[str] = mapped_column(String(32), unique=True)
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str] = mapped_column(String(80))
    date_of_birth: Mapped[date] = mapped_column(Date)
    zip_code: Mapped[str] = mapped_column(String(10))
    phone: Mapped[str] = mapped_column(String(20))
    original_creditor: Mapped[str] = mapped_column(String(120))
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[str] = mapped_column(String(32), default="active")
    timezone: Mapped[str] = mapped_column(String(40), default="America/New_York")
    is_fixture: Mapped[bool] = mapped_column(Boolean, default=False)


class CrmEvent(Base):
    """Every action the agent takes in the client's system of record."""

    __tablename__ = "crm_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"))
    type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict] = mapped_column(Json, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Arrangement(Base):
    __tablename__ = "arrangements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    kind: Mapped[str] = mapped_column(String(16))
    monthly_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    number_of_payments: Mapped[int | None] = mapped_column(Integer)
    final_payment: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    lump_sum: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    first_payment_date: Mapped[date] = mapped_column(Date)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    hardship: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --- Conversations and traces --------------------------------------------------------------


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16))  # chat | voice | eval | replay
    status: Mapped[str] = mapped_column(String(16), default="active")
    stage: Mapped[str] = mapped_column(String(24))
    outcome: Mapped[str | None] = mapped_column(String(48))
    state: Mapped[dict] = mapped_column(Json)
    # Append-only history sent to the response model (includes thinking and tool blocks).
    model_history: Mapped[list] = mapped_column(Json, default=list)
    today: Mapped[date] = mapped_column(Date)
    policy_version: Mapped[str] = mapped_column(String(64))
    agent_prompt_ref: Mapped[str] = mapped_column(String(64))
    classifier_prompt_ref: Mapped[str] = mapped_column(String(64))
    agent_model: Mapped[str] = mapped_column(String(64))
    eval_run_id: Mapped[str | None] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    replay_of: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    turns: Mapped[list["Turn"]] = relationship(
        back_populates="conversation", order_by="Turn.index", cascade="all, delete-orphan"
    )


class Turn(Base):
    """One caller utterance and everything the system did about it."""

    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("conversation_id", "index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    caller_text: Mapped[str | None] = mapped_column(Text)  # null for the agent's opening
    agent_text: Mapped[str] = mapped_column(Text)
    route: Mapped[str] = mapped_column(String(48))
    stage_before: Mapped[str] = mapped_column(String(24))
    stage_after: Mapped[str] = mapped_column(String(24))
    intent: Mapped[dict | None] = mapped_column(Json)
    directive: Mapped[dict | None] = mapped_column(Json)
    state_after: Mapped[dict] = mapped_column(Json)
    disclosures: Mapped[list] = mapped_column(Json, default=list)
    tool_calls: Mapped[list] = mapped_column(Json, default=list)
    guard: Mapped[dict] = mapped_column(Json, default=dict)
    llm_calls: Mapped[list] = mapped_column(Json, default=list)
    crm_actions: Mapped[list] = mapped_column(Json, default=list)
    redactions: Mapped[list] = mapped_column(Json, default=list)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="turns")


class PromptVersion(Base):
    """Snapshot of every instruction text that has been sent to a model."""

    __tablename__ = "prompt_versions"

    ref: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# --- Evals ---------------------------------------------------------------------------------


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(16))  # suite | replay
    label: Mapped[str | None] = mapped_column(String(120))
    git_sha: Mapped[str | None] = mapped_column(String(40))
    policy_version: Mapped[str] = mapped_column(String(64))
    agent_prompt_ref: Mapped[str] = mapped_column(String(64))
    classifier_prompt_ref: Mapped[str] = mapped_column(String(64))
    agent_model: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="running")
    passed: Mapped[bool | None] = mapped_column(Boolean)
    summary: Mapped[dict] = mapped_column(Json, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    results: Mapped[list["EvalResult"]] = relationship(
        back_populates="run", order_by="EvalResult.scenario_id", cascade="all, delete-orphan"
    )


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    scenario_id: Mapped[str] = mapped_column(String(80))
    conversation_id: Mapped[str | None] = mapped_column(String(36))
    passed: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[list] = mapped_column(Json, default=list)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped[EvalRun] = relationship(back_populates="results")


_ADDITIVE_COLUMNS = [
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS timezone VARCHAR(40) "
    "NOT NULL DEFAULT 'America/New_York'",
    "ALTER TABLE turns ADD COLUMN IF NOT EXISTS redactions JSONB NOT NULL DEFAULT '[]'",
]


# --- Engine --------------------------------------------------------------------------------


class Database:
    def __init__(self, url: str):
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Additive columns for databases created before they existed. (A real deployment
            # would use migrations; this keeps the demo's local data intact.)
            for statement in _ADDITIVE_COLUMNS:
                await conn.execute(text(statement))

    async def drop_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
