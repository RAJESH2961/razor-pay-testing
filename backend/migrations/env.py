from alembic import context
from sqlalchemy import text

from app.core.database import Base, engine
from app.core.db_config import DB_SCHEMA_PAYMENT
from app.modules.payments.db import ORM as payment_orm  # noqa: F401
from app.modules.users.db import ORM as user_orm  # noqa: F401


def include_name(name, type_, parent_names):
    # Never let autogenerate look at the other schemas in this shared database.
    if type_ == "schema":
        return name == DB_SCHEMA_PAYMENT
    return True


with engine.connect() as connection:
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DB_SCHEMA_PAYMENT}"'))
    connection.commit()

    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_schemas=True,
        include_name=include_name,
        version_table_schema=DB_SCHEMA_PAYMENT,
    )
    with context.begin_transaction():
        context.run_migrations()
