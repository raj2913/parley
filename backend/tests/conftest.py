import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from parley.config import BACKEND_ROOT, Settings
from parley.db import Account, Database
from parley.policy import load_policy
from parley.procedure import AccountFacts

TEST_DB_URL = os.environ.get(
    "PARLEY_TEST_DATABASE_URL", "postgresql+asyncpg://parley@localhost:5433/parley_test"
)
TODAY = date(2026, 10, 26)


@pytest.fixture(scope="session")
def policy():
    return load_policy(BACKEND_ROOT / "policies" / "acme_recovery.yaml")


@pytest.fixture(scope="session")
def settings():
    return Settings(database_url=TEST_DB_URL)


@pytest.fixture
def maria() -> AccountFacts:
    return AccountFacts(
        id="acc-1",
        reference="AR-1",
        first_name="Maria",
        last_name="Lopez",
        date_of_birth=date(1985, 3, 14),
        zip_code="94110",
        original_creditor="Northwind Bank",
        balance=Decimal("1240.00"),
    )


@pytest.fixture(scope="session")
async def database():
    db = Database(TEST_DB_URL)
    await db.drop_all()
    await db.create_all()
    yield db
    await db.dispose()


@pytest.fixture
async def db(database: Database):
    async with database.engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE eval_results, eval_runs, turns, arrangements, crm_events, "
                "conversations, prompt_versions, accounts CASCADE"
            )
        )
    return database


@pytest.fixture
async def account_id(db: Database) -> str:
    async with db.session() as session, session.begin():
        account = Account(
            reference="AR-T1",
            first_name="Maria",
            last_name="Lopez",
            date_of_birth=date(1985, 3, 14),
            zip_code="94110",
            phone="000",
            original_creditor="Northwind Bank",
            balance=Decimal("1240.00"),
            timezone="America/Los_Angeles",
        )
        session.add(account)
        await session.flush()
        return account.id
