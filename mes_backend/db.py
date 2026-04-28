from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "postgresql://postgres:parabellum@localhost/mes_aps"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def init_db_schema():
    from models import Base

    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    if "plan_change_log" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("plan_change_log")}
    statements = []

    if "is_rolled_back" not in columns:
        statements.append(
            "ALTER TABLE plan_change_log "
            "ADD COLUMN is_rolled_back BOOLEAN DEFAULT FALSE"
        )
    if "rollback_at" not in columns:
        statements.append("ALTER TABLE plan_change_log ADD COLUMN rollback_at TIMESTAMP")
    if "rollback_reason" not in columns:
        statements.append("ALTER TABLE plan_change_log ADD COLUMN rollback_reason VARCHAR")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
