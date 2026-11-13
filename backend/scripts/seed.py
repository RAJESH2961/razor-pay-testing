"""Insert demo prices and users. Safe to run repeatedly: existing rows are left alone."""
import os
import sys

from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.modules.payments.db.ORM import PriceORM
from app.modules.users.db.ORM import UserORM

PRICES = [
    {"code": "weekly", "name": "Weekly", "amount_paise": 9900, "duration_days": 7},
    {"code": "monthly", "name": "Monthly", "amount_paise": 29900, "duration_days": 30},
    {"code": "yearly", "name": "Yearly", "amount_paise": 249900, "duration_days": 365},
]

USERS = [
    {"email": "asha@example.com", "full_name": "Asha Rao", "phone": "9000000001"},
    {"email": "ravi@example.com", "full_name": "Ravi Kumar", "phone": "9000000002"},
    {"email": "meera@example.com", "full_name": "Meera Iyer", "phone": "9000000003"},
]


def main() -> None:
    if settings.APP_ENV == "production":
        sys.exit("Refusing to seed demo users in production")
    password = os.getenv("SEED_USER_PASSWORD")
    if not password:
        sys.exit("Set SEED_USER_PASSWORD in .env")

    password_hash = hash_password(password)
    with SessionLocal() as db:
        db.execute(
            insert(PriceORM)
            .values([{**price, "currency": "INR"} for price in PRICES])
            .on_conflict_do_nothing(index_elements=["code"])
        )
        db.execute(
            insert(UserORM)
            .values([{**user, "password_hash": password_hash} for user in USERS])
            .on_conflict_do_nothing(index_elements=["email"])
        )
        db.commit()
    print(f"Seeded {len(PRICES)} prices and {len(USERS)} users")


if __name__ == "__main__":
    main()
