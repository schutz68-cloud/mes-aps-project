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

    if "plan_versions" in table_names:
        columns = {column["name"] for column in inspector.get_columns("plan_versions")}

        if "name" not in columns:
            statements.append("ALTER TABLE plan_versions ADD COLUMN name TEXT")
        if "status" not in columns:
            statements.append("ALTER TABLE plan_versions ADD COLUMN status TEXT")
        if "created_at" not in columns:
            statements.append(
                "ALTER TABLE plan_versions ADD COLUMN created_at TIMESTAMP DEFAULT now()"
            )
        if "created_by" not in columns:
            statements.append("ALTER TABLE plan_versions ADD COLUMN created_by TEXT")
        if "approved_at" not in columns:
            statements.append("ALTER TABLE plan_versions ADD COLUMN approved_at TIMESTAMP")
        if "approved_by" not in columns:
            statements.append("ALTER TABLE plan_versions ADD COLUMN approved_by TEXT")
        if "description" not in columns:
            statements.append("ALTER TABLE plan_versions ADD COLUMN description TEXT")

    if "plan_change_log" in table_names:
        columns = {column["name"] for column in inspector.get_columns("plan_change_log")}

        if "plan_version_id" not in columns:
            statements.append(
                "ALTER TABLE plan_change_log ADD COLUMN plan_version_id INTEGER"
            )
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

        if "plan_version_id" not in columns:
            statements.append("ALTER TABLE plan_operations ADD COLUMN plan_version_id INTEGER")
        if "is_locked" not in columns:
            statements.append(
                "ALTER TABLE plan_operations "
                "ADD COLUMN is_locked BOOLEAN DEFAULT FALSE"
            )
        if "lock_reason" not in columns:
            statements.append("ALTER TABLE plan_operations ADD COLUMN lock_reason VARCHAR")

    if "mes_schedule_runs" in table_names:
        columns = {column["name"] for column in inspector.get_columns("mes_schedule_runs")}
        expected_columns = {
            "source_plan_version_id": "INTEGER",
            "start_minute": "INTEGER",
            "end_minute": "INTEGER",
            "status": "TEXT",
            "created_at": "TIMESTAMP DEFAULT now()",
            "created_by": "TEXT",
            "released_at": "TIMESTAMP",
            "released_by": "TEXT",
            "cancelled_at": "TIMESTAMP",
            "cancelled_by": "TEXT",
            "description": "TEXT",
            "is_hidden": "BOOLEAN DEFAULT FALSE",
        }

        for column_name, column_type in expected_columns.items():
            if column_name not in columns:
                statements.append(
                    f"ALTER TABLE mes_schedule_runs ADD COLUMN {column_name} {column_type}"
                )

    if "mes_schedule_operations" in table_names:
        columns = {
            column["name"]
            for column in inspector.get_columns("mes_schedule_operations")
        }
        expected_columns = {
            "schedule_run_id": "INTEGER",
            "source_plan_operation_id": "INTEGER",
            "operation_id": "INTEGER",
            "order_id": "INTEGER",
            "order_item_id": "INTEGER",
            "product_id": "TEXT",
            "product_name": "TEXT",
            "order_no": "TEXT",
            "machine_id": "TEXT",
            "machine_name": "TEXT",
            "machine_group_id": "TEXT",
            "operation_type": "TEXT",
            "operation_name": "TEXT",
            "quantity": "INTEGER",
            "setup_minutes": "INTEGER",
            "planned_start_time": "INTEGER",
            "planned_end_time": "INTEGER",
            "status": "TEXT",
        }

        for column_name, column_type in expected_columns.items():
            if column_name not in columns:
                statements.append(
                    f"ALTER TABLE mes_schedule_operations ADD COLUMN {column_name} {column_type}"
                )

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))

        connection.execute(
            text(
                """
                INSERT INTO plan_versions (
                    id,
                    name,
                    status,
                    created_by,
                    description
                )
                VALUES (
                    1,
                    'Основной план',
                    'active',
                    'system',
                    'Текущая активная версия плана'
                )
                ON CONFLICT (id) DO NOTHING
                """
            )
        )

        connection.execute(text("""
                SELECT setval(
                    pg_get_serial_sequence('plan_versions', 'id'),
                    COALESCE((SELECT MAX(id) FROM plan_versions), 1),
                    true
                )
                """))

        connection.execute(
            text(
                """
                UPDATE plan_operations
                SET plan_version_id = 1
                WHERE plan_version_id IS NULL
                """
            )
        )

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

        connection.execute(
            text(
                """
                INSERT INTO shift_templates (
                    id,
                    name,
                    start_minute_of_day,
                    end_minute_of_day,
                    prep_minutes,
                    finish_minutes,
                    is_active
                )
                VALUES
                    (1, 'Смена 1', 360, 840, 20, 20, true),
                    (2, 'Смена 2', 840, 1320, 20, 20, true)
                ON CONFLICT (id) DO UPDATE
                SET
                    name = EXCLUDED.name,
                    start_minute_of_day = EXCLUDED.start_minute_of_day,
                    end_minute_of_day = EXCLUDED.end_minute_of_day,
                    prep_minutes = EXCLUDED.prep_minutes,
                    finish_minutes = EXCLUDED.finish_minutes,
                    is_active = EXCLUDED.is_active
                """
            )
        )

        connection.execute(
            text(
                """
                INSERT INTO shift_template_breaks (
                    id,
                    shift_template_id,
                    name,
                    start_minute_of_shift,
                    end_minute_of_shift
                )
                VALUES
                    (1, 1, 'Обед', 240, 270),
                    (2, 2, 'Обед', 240, 270)
                ON CONFLICT (id) DO UPDATE
                SET
                    shift_template_id = EXCLUDED.shift_template_id,
                    name = EXCLUDED.name,
                    start_minute_of_shift = EXCLUDED.start_minute_of_shift,
                    end_minute_of_shift = EXCLUDED.end_minute_of_shift
                """
            )
        )

        connection.execute(
            text(
                """
                INSERT INTO setup_teams (id, name, capacity, is_active)
                VALUES
                    ('SETUP_COIL', 'Бригада наладчиков навивки', 1, true),
                    ('SETUP_BEND', 'Бригада наладчиков загиба', 1, true),
                    ('SETUP_FACE', 'Бригада наладчиков торцовки', 1, true),
                    ('SETUP_HEAT', 'Бригада наладчиков термички', 1, true),
                    ('SETUP_COAT', 'Бригада наладчиков покрытия', 1, true)
                ON CONFLICT (id) DO UPDATE
                SET
                    name = EXCLUDED.name,
                    capacity = EXCLUDED.capacity,
                    is_active = EXCLUDED.is_active
                """
            )
        )

        connection.execute(
            text(
                """
                INSERT INTO machine_group_setup_teams (
                    machine_group_id,
                    setup_team_id
                )
                SELECT mapping.machine_group_id, mapping.setup_team_id
                FROM (
                    VALUES
                        ('COIL_A', 'SETUP_COIL'),
                        ('COIL_B', 'SETUP_COIL'),
                        ('BEND', 'SETUP_BEND'),
                        ('FACE', 'SETUP_FACE'),
                        ('HEAT', 'SETUP_HEAT'),
                        ('COAT_A', 'SETUP_COAT'),
                        ('COAT_B', 'SETUP_COAT')
                ) AS mapping(machine_group_id, setup_team_id)
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM machine_group_setup_teams existing
                    WHERE existing.machine_group_id = mapping.machine_group_id
                      AND existing.setup_team_id = mapping.setup_team_id
                )
                """
            )
        )
