import sys
from math import ceil
from pathlib import Path

from sqlalchemy import text


APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import engine, init_db_schema  # noqa: E402


ACTIVE_PLAN_VERSION_ID = 1
ROUTE_GAP_MINUTES = 30
MACHINE_GAP_MINUTES = 15
COILING_SETUP_RESOURCE_KEY = "COILING"

SETUP_TEAM_BY_MACHINE_GROUP = {
    "COIL_A": "SETUP_COIL",
    "COIL_B": "SETUP_COIL",
    "BEND": "SETUP_BEND",
    "FACE": "SETUP_FACE",
    "HEAT": "SETUP_HEAT",
    "COAT_A": "SETUP_COAT",
    "COAT_B": "SETUP_COAT",
}

SETUP_MINUTES = {
    "COILING": 90,
    "FACING": 60,
    "BENDING": 60,
    "HEAT": 15,
    "COATING": 10,
}

DEFAULT_UNITS_PER_MINUTE = {
    "COILING": 20,
    "FACING": 25,
    "BENDING": 18,
    "HEAT": 60,
    "COATING": 40,
}

MACHINES_BY_OPERATION = {
    "COILING": ["NW1", "NW2", "NW3", "NW4", "NW5", "NW6"],
    "FACING": ["TC1", "TC2"],
    "BENDING": ["ZG1", "ZG2"],
    "HEAT": ["TM1"],
    "COATING": ["PK1", "PK2"],
}

REQUIRED_GROUP_BY_OPERATION = {
    "COILING": "COIL_A",
    "FACING": "FACE",
    "BENDING": "BEND",
    "HEAT": "HEAT",
    "COATING": "COAT_A",
}

ALLOWED_GROUPS_BY_OPERATION = {
    "COILING": ["COIL_A", "COIL_B"],
    "FACING": ["FACE"],
    "BENDING": ["BEND"],
    "HEAT": ["HEAT"],
    "COATING": ["COAT_A", "COAT_B"],
}

OPERATION_NAMES = {
    "COILING": "Навивка",
    "FACING": "Торцовка",
    "BENDING": "Загиб",
    "HEAT": "Термичка",
    "COATING": "Покрытие",
}

TARGET_PRODUCTS = [
    ("SPR-01-01", ["%SPR-01-01%", "%ДПЗ-01-01%", "%01-01%"]),
    ("SPR-02-01", ["%SPR-02-01%", "%ДПЗ-02-01%", "%02-01%"]),
    ("SPR-01-02", ["%SPR-01-02%", "%ДПЗ-01-02%", "%01-02%"]),
    ("SPR-02-02", ["%SPR-02-02%", "%ДПЗ-02-02%", "%02-02%"]),
    ("SPR-01-03", ["%SPR-01-03%", "%ДПЗ-01-03%", "%01-03%"]),
    ("SPR-02-03", ["%SPR-02-03%", "%ДПЗ-02-03%", "%02-03%"]),
]

ORDERS = [
    {
        "id": 1,
        "order_no": "ORD-001",
        "product_key": "SPR-01-01",
        "quantity": 2000,
        "due_time": 7200,
        "routing_id": 101,
        "route": ["COILING", "FACING", "HEAT", "COATING"],
        "plan": [
            ("COILING", "NW1", 0),
            ("FACING", "TC1", 180),
            ("HEAT", "TM1", 318),
            ("COATING", "PK1", 383),
        ],
    },
    {
        "id": 2,
        "order_no": "ORD-002",
        "product_key": "SPR-02-01",
        "quantity": 2100,
        "due_time": 8400,
        "routing_id": 102,
        "route": ["COILING", "FACING", "HEAT", "COATING"],
        "plan": [
            ("COILING", "NW2", 0),
            ("FACING", "TC2", 200),
            ("HEAT", "TM1", 368),
            ("COATING", "PK2", 440),
        ],
    },
    {
        "id": 3,
        "order_no": "ORD-003",
        "product_key": "SPR-01-02",
        "quantity": 2200,
        "due_time": 9600,
        "routing_id": 103,
        "route": ["COILING", "HEAT", "COATING"],
        "plan": [
            ("COILING", "NW3", 0),
            ("HEAT", "TM1", 425),
            ("COATING", "PK1", 489),
        ],
    },
    {
        "id": 4,
        "order_no": "ORD-004",
        "product_key": "SPR-02-02",
        "quantity": 2300,
        "due_time": 10800,
        "routing_id": 104,
        "route": ["COILING", "HEAT", "COATING"],
        "plan": [
            ("COILING", "NW4", 0),
            ("HEAT", "TM1", 474),
            ("COATING", "PK2", 559),
        ],
    },
    {
        "id": 5,
        "order_no": "ORD-005",
        "product_key": "SPR-01-03",
        "quantity": 2200,
        "due_time": 12000,
        "routing_id": 105,
        "route": ["COILING", "BENDING", "HEAT", "COATING"],
        "plan": [
            ("COILING", "NW5", 0),
            ("BENDING", "ZG1", 210),
            ("HEAT", "TM1", 544),
            ("COATING", "PK1", 619),
        ],
    },
    {
        "id": 6,
        "order_no": "ORD-006",
        "product_key": "SPR-02-03",
        "quantity": 2300,
        "due_time": 13200,
        "routing_id": 106,
        "route": ["COILING", "BENDING", "HEAT", "COATING"],
        "plan": [
            ("COILING", "NW6", 0),
            ("BENDING", "ZG2", 230),
            ("HEAT", "TM1", 604),
            ("COATING", "PK2", 686),
        ],
    },
]


def get_columns(connection, table_name):
    rows = connection.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    ).scalars()
    return set(rows)


def insert_row(connection, table_name, values, table_columns):
    filtered = {key: value for key, value in values.items() if key in table_columns}
    if not filtered:
        raise RuntimeError(f"Нет подходящих колонок для вставки в {table_name}")

    columns_sql = ", ".join(filtered.keys())
    params_sql = ", ".join(f":{key}" for key in filtered.keys())
    connection.execute(
        text(f"INSERT INTO {table_name} ({columns_sql}) VALUES ({params_sql})"),
        filtered,
    )


def sync_sequence(connection, table_name, id_column="id"):
    sequence_name = connection.execute(
        text("SELECT pg_get_serial_sequence(:table_name, :id_column)"),
        {"table_name": table_name, "id_column": id_column},
    ).scalar()
    if not sequence_name:
        return

    connection.execute(
        text(
            f"""
            SELECT setval(
                :sequence_name,
                COALESCE((SELECT MAX({id_column}) FROM {table_name}), 1),
                true
            )
            """
        ),
        {"sequence_name": sequence_name},
    )


def find_products(connection):
    products = {}
    used_ids = set()

    for index, (product_key, patterns) in enumerate(TARGET_PRODUCTS):
        product = None
        for pattern in patterns:
            product = connection.execute(
                text(
                    """
                    SELECT id, name
                    FROM products
                    WHERE name ILIKE :pattern
                    ORDER BY id
                    LIMIT 1
                    """
                ),
                {"pattern": pattern},
            ).mappings().first()
            if product and product["id"] not in used_ids:
                break
            product = None

        if not product:
            candidates = connection.execute(
                text(
                    """
                    SELECT id, name
                    FROM products
                    ORDER BY id
                    """
                )
            ).mappings().all()
            product = next(
                (candidate for candidate in candidates if candidate["id"] not in used_ids),
                None,
            )

        if not product:
            raise RuntimeError(
                f"Не найдено изделие для {product_key}. Проверьте таблицу products"
            )

        used_ids.add(product["id"])
        products[product_key] = dict(product)

    return products


def ensure_machines_exist(connection):
    machine_ids = sorted(
        {machine_id for machines in MACHINES_BY_OPERATION.values() for machine_id in machines}
    )
    existing = set(
        connection.execute(
            text("SELECT id FROM machines WHERE id = ANY(:machine_ids)"),
            {"machine_ids": machine_ids},
        ).scalars()
    )
    missing = sorted(set(machine_ids) - existing)
    if missing:
        raise RuntimeError(f"Не найдены станки: {', '.join(missing)}")

    machine_columns = get_columns(connection, "machines")
    if "status" in machine_columns:
        connection.execute(
            text("UPDATE machines SET status = 'active' WHERE id = ANY(:machine_ids)"),
            {"machine_ids": machine_ids},
        )


def reset_plan_versions(connection):
    connection.execute(text("DELETE FROM plan_versions WHERE id <> 1"))
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
            ON CONFLICT (id) DO UPDATE
            SET
                name = EXCLUDED.name,
                status = EXCLUDED.status,
                created_by = EXCLUDED.created_by,
                description = EXCLUDED.description
            """
        )
    )


def reset_rates(connection, products):
    operation_types = list(SETUP_MINUTES.keys())
    connection.execute(
        text(
            """
            UPDATE machine_product_rates
            SET
                setup_minutes = CASE operation_type
                    WHEN 'COILING' THEN 90
                    WHEN 'FACING' THEN 60
                    WHEN 'BENDING' THEN 60
                    WHEN 'HEAT' THEN 15
                    WHEN 'COATING' THEN 10
                    ELSE setup_minutes
                END,
                units_per_minute = CASE
                    WHEN units_per_minute IS NULL OR units_per_minute <= 0 THEN
                        CASE operation_type
                            WHEN 'COILING' THEN 20
                            WHEN 'FACING' THEN 25
                            WHEN 'BENDING' THEN 18
                            WHEN 'HEAT' THEN 60
                            WHEN 'COATING' THEN 40
                            ELSE 1
                        END
                    ELSE units_per_minute
                END
            WHERE operation_type = ANY(:operation_types)
            """
        ),
        {"operation_types": operation_types},
    )
    connection.execute(
        text(
            """
            UPDATE machine_product_rates
            SET units_per_minute = 1
            WHERE units_per_minute IS NULL
               OR units_per_minute <= 0
            """
        )
    )

    selected_product_ids = [product["id"] for product in products.values()]
    all_machine_ids = sorted(
        {machine_id for machines in MACHINES_BY_OPERATION.values() for machine_id in machines}
    )
    connection.execute(
        text(
            """
            DELETE FROM machine_product_rates
            WHERE product_id = ANY(:product_ids)
              AND machine_id = ANY(:machine_ids)
              AND operation_type = ANY(:operation_types)
            """
        ),
        {
            "product_ids": selected_product_ids,
            "machine_ids": all_machine_ids,
            "operation_types": operation_types,
        },
    )

    rate_columns = get_columns(connection, "machine_product_rates")
    for order in ORDERS:
        product_id = products[order["product_key"]]["id"]
        for operation_type in order["route"]:
            for machine_id in MACHINES_BY_OPERATION[operation_type]:
                insert_row(
                    connection,
                    "machine_product_rates",
                    {
                        "product_id": product_id,
                        "machine_id": machine_id,
                        "operation_type": operation_type,
                        "units_per_minute": DEFAULT_UNITS_PER_MINUTE[operation_type],
                        "setup_minutes": SETUP_MINUTES[operation_type],
                    },
                    rate_columns,
                )


def create_orders(connection, columns, products):
    order_columns = columns["orders"]
    order_item_columns = columns["order_items"]

    for order in ORDERS:
        due_values = {
            "due_date": order["due_time"],
            "due_time": order["due_time"],
        }
        insert_row(
            connection,
            "orders",
            {
                "id": order["id"],
                "order_no": order["order_no"],
                "customer": "Тестовый заказчик",
                "status": "created",
                **due_values,
            },
            order_columns,
        )

        insert_row(
            connection,
            "order_items",
            {
                "id": order["id"],
                "order_id": order["id"],
                "product_id": products[order["product_key"]]["id"],
                "quantity": order["quantity"],
                "priority": 1,
                **due_values,
            },
            order_item_columns,
        )


def create_routings(connection, columns, products):
    routing_columns = columns["routings"]
    routing_operation_columns = columns["routing_operations"]
    allowed_columns = columns["routing_operation_machine_groups"]

    routing_operation_by_key = {}
    next_routing_operation_id = 1001

    for order in ORDERS:
        product_id = products[order["product_key"]]["id"]
        insert_row(
            connection,
            "routings",
            {
                "id": order["routing_id"],
                "product_id": product_id,
                "name": f"Маршрут {order['order_no']}",
                "status": "active",
                "is_active": True,
            },
            routing_columns,
        )

        for index, operation_type in enumerate(order["route"], start=1):
            routing_operation_id = next_routing_operation_id
            next_routing_operation_id += 1
            step_no = index * 10

            insert_row(
                connection,
                "routing_operations",
                {
                    "id": routing_operation_id,
                    "routing_id": order["routing_id"],
                    "step_no": step_no,
                    "sequence_no": step_no,
                    "operation_type": operation_type,
                    "operation_name": OPERATION_NAMES[operation_type],
                    "required_machine_group_id": REQUIRED_GROUP_BY_OPERATION[
                        operation_type
                    ],
                    "is_mandatory": True,
                    "transfer_batch_allowed": False,
                    "buffer_minutes": 30,
                },
                routing_operation_columns,
            )
            routing_operation_by_key[(order["id"], operation_type)] = {
                "id": routing_operation_id,
                "sequence_no": step_no,
            }

            for machine_group_id in ALLOWED_GROUPS_BY_OPERATION[operation_type]:
                insert_row(
                    connection,
                    "routing_operation_machine_groups",
                    {
                        "routing_operation_id": routing_operation_id,
                        "machine_group_id": machine_group_id,
                    },
                    allowed_columns,
                )

    return routing_operation_by_key


def create_order_operations(connection, columns, routing_operation_by_key):
    order_operation_columns = columns["order_operations"]
    order_operation_by_key = {}
    next_order_operation_id = 1

    for order in ORDERS:
        for operation_type in order["route"]:
            routing_operation = routing_operation_by_key[(order["id"], operation_type)]
            order_operation_id = next_order_operation_id
            next_order_operation_id += 1

            insert_row(
                connection,
                "order_operations",
                {
                    "id": order_operation_id,
                    "order_item_id": order["id"],
                    "routing_operation_id": routing_operation["id"],
                    "operation_type": operation_type,
                    "sequence_no": routing_operation["sequence_no"],
                    "quantity": order["quantity"],
                    "status": "created",
                },
                order_operation_columns,
            )
            order_operation_by_key[(order["id"], operation_type)] = order_operation_id

    return order_operation_by_key


def get_rate(connection, product_id, operation_type, machine_id):
    rate = connection.execute(
        text(
            """
            SELECT units_per_minute, setup_minutes
            FROM machine_product_rates
            WHERE product_id = :product_id
              AND operation_type = :operation_type
              AND machine_id = :machine_id
            LIMIT 1
            """
        ),
        {
            "product_id": product_id,
            "operation_type": operation_type,
            "machine_id": machine_id,
        },
    ).mappings().first()

    if not rate:
        raise RuntimeError(
            "Не найдена норма для "
            f"product_id={product_id}, machine_id={machine_id}, "
            f"operation_type={operation_type}"
        )

    units_per_minute = float(rate["units_per_minute"] or 0)
    if units_per_minute <= 0:
        raise RuntimeError(
            "Некорректная норма для "
            f"product_id={product_id}, machine_id={machine_id}, "
            f"operation_type={operation_type}"
        )

    return units_per_minute, int(rate["setup_minutes"] or 0)


def get_machine_group(connection, machine_id):
    machine_group_id = connection.execute(
        text("SELECT group_id FROM machines WHERE id = :machine_id"),
        {"machine_id": machine_id},
    ).scalar()

    if not machine_group_id:
        raise RuntimeError(f"Не найдена группа оборудования для станка {machine_id}")

    return machine_group_id


def build_work_intervals(days: int):
    intervals = []

    for day in range(days):
        day_offset = day * 1440
        intervals.extend(
            [
                (day_offset + 20, day_offset + 240),
                (day_offset + 270, day_offset + 460),
                (day_offset + 500, day_offset + 720),
                (day_offset + 750, day_offset + 940),
            ]
        )

    return intervals


def overlaps(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def has_overlap(intervals, start, end, gap_after=0):
    return any(
        overlaps(start, end, busy_start, busy_end + gap_after)
        for busy_start, busy_end in intervals
    )


def find_conflict_end(intervals, start, end, gap_after=0):
    conflicting_ends = [
        busy_end + gap_after
        for busy_start, busy_end in intervals
        if overlaps(start, end, busy_start, busy_end + gap_after)
    ]

    return max(conflicting_ends) if conflicting_ends else None


def find_earliest_start(
    earliest_start,
    duration,
    setup_minutes,
    machine_id,
    setup_team_id,
    work_intervals,
    machine_busy,
    setup_team_busy,
):
    for work_start, work_end in work_intervals:
        candidate_start = max(earliest_start, work_start)

        while True:
            candidate_end = candidate_start + duration

            if candidate_end > work_end:
                break

            machine_conflict_end = find_conflict_end(
                machine_busy.get(machine_id, []),
                candidate_start,
                candidate_end,
                MACHINE_GAP_MINUTES,
            )
            if machine_conflict_end is not None:
                candidate_start = machine_conflict_end
                if candidate_start + duration > work_end:
                    break
                continue

            if setup_minutes > 0 and setup_team_id:
                setup_conflict_end = find_conflict_end(
                    setup_team_busy.get(setup_team_id, []),
                    candidate_start,
                    candidate_start + setup_minutes,
                    MACHINE_GAP_MINUTES,
                )
                if setup_conflict_end is not None:
                    candidate_start = setup_conflict_end
                    if candidate_start + duration > work_end:
                        break
                    continue

            return candidate_start

    raise RuntimeError(
        "Не удалось найти рабочий интервал для операции "
        f"machine_id={machine_id}, earliest_start={earliest_start}, duration={duration}"
    )


def create_plan_operations(connection, columns, products, order_operation_by_key):
    plan_columns = columns["plan_operations"]
    order_item_ready_time = {}
    machine_busy = {}
    setup_team_busy = {}
    work_intervals = build_work_intervals(days=10)
    max_work_interval_duration = max(
        work_end - work_start for work_start, work_end in work_intervals
    )

    for order in ORDERS:
        product_id = products[order["product_key"]]["id"]
        for operation_index, (operation_type, machine_id, _planned_start_time) in enumerate(
            order["plan"]
        ):
            units_per_minute, setup_minutes = get_rate(
                connection,
                product_id,
                operation_type,
                machine_id,
            )
            duration = ceil(order["quantity"] / units_per_minute) + setup_minutes
            operation_id = order_operation_by_key[(order["id"], operation_type)]

            if duration > max_work_interval_duration:
                raise RuntimeError(
                    f"Операция {operation_id} длительностью {duration} мин. "
                    "не помещается ни в один рабочий интервал смены. "
                    "Уменьшите количество или увеличьте норму."
                )

            order_item_id = order["id"]
            route_ready = order_item_ready_time.get(order_item_id, 0)
            machine_group_id = get_machine_group(connection, machine_id)
            setup_team_id = SETUP_TEAM_BY_MACHINE_GROUP.get(machine_group_id)

            if order_item_id in order_item_ready_time:
                route_ready += ROUTE_GAP_MINUTES

            start_time = find_earliest_start(
                route_ready,
                duration,
                setup_minutes,
                machine_id,
                setup_team_id,
                work_intervals,
                machine_busy,
                setup_team_busy,
            )
            end_time = start_time + duration

            insert_row(
                connection,
                "plan_operations",
                {
                    "plan_version_id": ACTIVE_PLAN_VERSION_ID,
                    "operation_id": operation_id,
                    "machine_id": machine_id,
                    "start_time": start_time,
                    "end_time": end_time,
                    "is_locked": False,
                    "lock_reason": None,
                },
                plan_columns,
            )
            order_item_ready_time[order_item_id] = end_time
            machine_busy.setdefault(machine_id, []).append((start_time, end_time))
            if setup_minutes > 0 and setup_team_id:
                setup_team_busy.setdefault(setup_team_id, []).append(
                    (start_time, start_time + setup_minutes)
                )


def assert_counts(connection):
    expected = {
        "orders": 6,
        "order_items": 6,
        "order_operations": 22,
        "plan_operations": 22,
        "plan_change_log": 0,
    }

    for table_name, expected_count in expected.items():
        actual_count = connection.execute(
            text(f"SELECT COUNT(*) FROM {table_name}")
        ).scalar()
        if actual_count != expected_count:
            raise RuntimeError(
                f"Ожидалось {expected_count} строк в {table_name}, "
                f"получено {actual_count}"
            )

    draft_count = connection.execute(
        text("SELECT COUNT(*) FROM plan_versions WHERE status <> 'active'")
    ).scalar()
    if draft_count != 0:
        raise RuntimeError(f"Ожидалось 0 draft-версий, получено {draft_count}")

    active_versions = connection.execute(
        text("SELECT id, status, name FROM plan_versions ORDER BY id")
    ).mappings().all()
    if len(active_versions) != 1 or active_versions[0]["id"] != 1:
        raise RuntimeError("Ожидалась только активная версия плана id=1")

    plan_versions = connection.execute(
        text(
            """
            SELECT plan_version_id, COUNT(*) AS operation_count
            FROM plan_operations
            GROUP BY plan_version_id
            ORDER BY plan_version_id
            """
        )
    ).mappings().all()
    if len(plan_versions) != 1 or plan_versions[0]["plan_version_id"] != 1:
        raise RuntimeError("Плановые операции должны быть только в версии плана id=1")

    operation_counts = {
        row["order_id"]: row["operation_count"]
        for row in connection.execute(
            text(
                """
                SELECT oi.order_id, COUNT(*) AS operation_count
                FROM order_items oi
                JOIN order_operations oo ON oo.order_item_id = oi.id
                GROUP BY oi.order_id
                ORDER BY oi.order_id
                """
            )
        ).mappings()
    }
    expected_operation_counts = {1: 4, 2: 4, 3: 3, 4: 3, 5: 4, 6: 4}
    if operation_counts != expected_operation_counts:
        raise RuntimeError(
            "Неверное количество операций по заказам: "
            f"{operation_counts}, ожидалось {expected_operation_counts}"
        )

    setup_rows = connection.execute(
        text(
            """
            SELECT operation_type, setup_minutes
            FROM machine_product_rates
            WHERE operation_type IN ('COILING', 'FACING', 'BENDING', 'HEAT', 'COATING')
            GROUP BY operation_type, setup_minutes
            """
        )
    ).mappings().all()
    setups = {}
    for row in setup_rows:
        setups.setdefault(row["operation_type"], set()).add(row["setup_minutes"])

    for operation_type, setup_minutes in SETUP_MINUTES.items():
        if setups.get(operation_type) != {setup_minutes}:
            raise RuntimeError(
                f"Для {operation_type} ожидалась наладка {setup_minutes}, "
                f"получено {setups.get(operation_type)}"
            )

    bad_rates = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM machine_product_rates
            WHERE units_per_minute IS NULL
               OR units_per_minute <= 0
            """
        )
    ).scalar()
    if bad_rates != 0:
        raise RuntimeError(f"Найдены нормы с units_per_minute <= 0: {bad_rates}")

    route_buffer_violations = connection.execute(
        text(
            """
            WITH ordered AS (
                SELECT
                    po.plan_version_id,
                    oi.id AS order_item_id,
                    oo.id AS operation_id,
                    oo.sequence_no,
                    po.start_time,
                    po.end_time,
                    LAG(po.end_time) OVER (
                        PARTITION BY po.plan_version_id, oi.id
                        ORDER BY oo.sequence_no, oo.id
                    ) AS previous_end_time
                FROM plan_operations po
                JOIN order_operations oo ON oo.id = po.operation_id
                JOIN order_items oi ON oi.id = oo.order_item_id
            )
            SELECT COUNT(*)
            FROM ordered
            WHERE previous_end_time IS NOT NULL
              AND start_time < previous_end_time + :route_gap
            """
        ),
        {"route_gap": ROUTE_GAP_MINUTES},
    ).scalar()
    if route_buffer_violations != 0:
        raise RuntimeError(
            f"Найдены нарушения маршрутного буфера: {route_buffer_violations}"
        )

    machine_buffer_violations = connection.execute(
        text(
            """
            WITH ordered AS (
                SELECT
                    po.plan_version_id,
                    po.machine_id,
                    po.operation_id,
                    po.start_time,
                    po.end_time,
                    mpr.setup_minutes,
                    oo.order_item_id,
                    oo.sequence_no,
                    MIN(oo.sequence_no) OVER (
                        PARTITION BY po.plan_version_id, oo.order_item_id
                    ) AS first_sequence_no,
                    LAG(po.end_time) OVER (
                        PARTITION BY po.plan_version_id, po.machine_id
                        ORDER BY po.start_time, po.end_time, po.operation_id
                    ) AS previous_end_time
                    ,
                    LAG(po.start_time) OVER (
                        PARTITION BY po.plan_version_id, po.machine_id
                        ORDER BY po.start_time, po.end_time, po.operation_id
                    ) AS previous_start_time,
                    LAG(mpr.setup_minutes) OVER (
                        PARTITION BY po.plan_version_id, po.machine_id
                        ORDER BY po.start_time, po.end_time, po.operation_id
                    ) AS previous_setup_minutes
                FROM plan_operations po
                JOIN order_operations oo ON oo.id = po.operation_id
                JOIN order_items oi ON oi.id = oo.order_item_id
                JOIN machine_product_rates mpr
                  ON mpr.product_id = oi.product_id
                 AND mpr.machine_id = po.machine_id
                 AND mpr.operation_type = oo.operation_type
            )
            SELECT COUNT(*)
            FROM ordered
            WHERE previous_start_time IS NOT NULL
              AND (
                  (
                      sequence_no = first_sequence_no
                      AND start_time < previous_start_time + previous_setup_minutes + :machine_gap
                  )
                  OR
                  (
                      sequence_no <> first_sequence_no
                      AND start_time < previous_end_time + :machine_gap
                  )
              )
            """
        ),
        {"machine_gap": MACHINE_GAP_MINUTES},
    ).scalar()
    if machine_buffer_violations != 0:
        raise RuntimeError(
            f"Найдены нарушения станочного буфера: {machine_buffer_violations}"
        )


def seed_compact_test_data():
    init_db_schema()

    table_names = [
        "orders",
        "order_items",
        "order_operations",
        "plan_operations",
        "plan_change_log",
        "routings",
        "routing_operations",
        "routing_operation_machine_groups",
        "machine_product_rates",
    ]

    with engine.begin() as connection:
        columns = {table_name: get_columns(connection, table_name) for table_name in table_names}

        connection.execute(text("DELETE FROM plan_change_log"))
        connection.execute(text("DELETE FROM plan_operations"))
        connection.execute(text("DELETE FROM order_operations"))
        connection.execute(text("DELETE FROM order_items"))
        connection.execute(text("DELETE FROM orders"))
        connection.execute(text("DELETE FROM routing_operation_machine_groups"))
        connection.execute(text("DELETE FROM routing_operations"))
        connection.execute(text("DELETE FROM routings"))

        reset_plan_versions(connection)
        ensure_machines_exist(connection)
        products = find_products(connection)
        reset_rates(connection, products)

        create_orders(connection, columns, products)
        routing_operation_by_key = create_routings(connection, columns, products)
        order_operation_by_key = create_order_operations(
            connection,
            columns,
            routing_operation_by_key,
        )
        create_plan_operations(connection, columns, products, order_operation_by_key)

        for table_name in [
            "plan_versions",
            "orders",
            "order_items",
            "routings",
            "routing_operations",
            "routing_operation_machine_groups",
            "order_operations",
            "plan_operations",
            "machine_product_rates",
        ]:
            sync_sequence(connection, table_name)

        assert_counts(connection)

    print("Компактная тестовая модель APS-MES загружена")
    print("orders=6, order_items=6, order_operations=22, plan_operations=22")
    print("plan_versions: #1 Основной план active, draft=0")
    print("plan_change_log=0")


if __name__ == "__main__":
    seed_compact_test_data()
