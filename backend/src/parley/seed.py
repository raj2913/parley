"""Fictional demo accounts. No real people, creditors, or balances."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from parley.db import Account, Database

DEMO_ACCOUNTS = [
    dict(
        reference="AR-10421",
        first_name="Maria",
        last_name="Lopez",
        date_of_birth=date(1985, 3, 14),
        zip_code="94110",
        timezone="America/Los_Angeles",
        phone="415-555-0101",
        original_creditor="Northwind Bank",
        balance=Decimal("1240.00"),
    ),
    dict(
        reference="AR-10422",
        first_name="James",
        last_name="Carter",
        date_of_birth=date(1979, 11, 2),
        zip_code="60614",
        timezone="America/Chicago",
        phone="312-555-0147",
        original_creditor="Bluebird Telecom",
        balance=Decimal("486.50"),
    ),
    dict(
        reference="AR-10423",
        first_name="Priya",
        last_name="Nair",
        date_of_birth=date(1992, 7, 21),
        zip_code="30309",
        timezone="America/New_York",
        phone="404-555-0163",
        original_creditor="Summit Auto Finance",
        balance=Decimal("3875.20"),
    ),
    dict(
        reference="AR-10424",
        first_name="Daniel",
        last_name="Kim",
        date_of_birth=date(1968, 1, 30),
        zip_code="98103",
        timezone="America/Los_Angeles",
        phone="206-555-0188",
        original_creditor="Cedar Medical Group",
        balance=Decimal("212.00"),
    ),
    dict(
        reference="AR-10425",
        first_name="Aisha",
        last_name="Bello",
        date_of_birth=date(1988, 9, 5),
        zip_code="10027",
        timezone="America/New_York",
        phone="212-555-0119",
        original_creditor="Harbor Personal Loans",
        balance=Decimal("9410.75"),
    ),
]


async def seed_demo_accounts(db: Database, *, reset: bool = False) -> int:
    """Insert demo accounts; with reset, restore their status to active."""
    created = 0
    async with db.session() as session, session.begin():
        for spec in DEMO_ACCOUNTS:
            existing = (
                await session.execute(select(Account).where(Account.reference == spec["reference"]))
            ).scalar_one_or_none()
            if existing is None:
                session.add(Account(**spec))
                created += 1
            elif reset:
                existing.status = "active"
                existing.balance = spec["balance"]
                existing.timezone = spec["timezone"]
    return created
