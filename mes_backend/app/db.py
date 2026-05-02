from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "postgresql://postgres:parabellum@localhost/mes_aps"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def init_db_schema():
    from app.models import Base

    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    statements = []

    if "plan_change_log" in table_names:
        columns = {column["name"] for column in inspector.get_columns("plan_change_log")}

        if "change_set_id" not in columns:
            statements.append("ALTER TABLE plan_change_log ADD COLUMN change_set_id TEXT")
        if "is_rolled_back" not in columns:
            statements.append(
                "ALTER TABLE plan_change_log "
                "ADD COLUMN is_rolled_back BOOLEAN DEFAULT FALSE"
            )
        if "rollback_at" not in columns:
            statements.append("ALTER TABLE plan_change_log ADD COLUMN rollback_at TIMESTAMP")
        if "rollback_reason" not in columns:
            statements.append("ALTER TABLE plan_change_log ADD COLUMN rollback_reason VARCHAR")

    if "plan_operations" in table_names:
        columns = {column["name"] for column in inspector.get_columns("plan_operations")}

        if "is_locked" not in columns:
            statements.append(
                "ALTER TABLE plan_operations "
                "ADD COLUMN is_locked BOOLEAN DEFAULT FALSE"
            )
        if "lock_reason" not in columns:
            statements.append("ALTER TABLE plan_operations ADD COLUMN lock_reason VARCHAR")

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))

        connection.execute(
            text(
                """
                INSERT INTO routing_operation_machine_groups (
                    routing_operation_id,
                    machine_group_id
                )
                SELECT ro.id, allowed.machine_group_id
                FROM routing_operations ro
                JOIN (
                    VALUES
                        ('COILING', 'COIL_A'),
                        ('COILING', 'COIL_B'),
                        ('BENDING', 'BEND'),
                        ('FACING', 'FACE'),
                        ('HEAT', 'HEAT'),
                        ('COATING', 'COAT_A'),
                        ('COATING', 'COAT_B')
                ) AS allowed(operation_type, machine_group_id)
                  ON allowed.operation_type = ro.operation_type
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM routing_operation_machine_groups existing
                    WHERE existing.routing_operation_id = ro.id
                      AND existing.machine_group_id = allowed.machine_group_id
                )
                """
            )
        )
