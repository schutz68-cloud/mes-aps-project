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

SETUP_TEAM_BY_MACHINE_GROUP = {
    "COIL_A": "SETUP_COIL",
    "BEND": "SETUP_BEND",
    "FACE": "SETUP_FACE",
    "HEAT": "SETUP_HEAT",
    "COAT_A": "SETUP_COAT",
}

SETUP_MINUTES = {
    "COILING": 90,
    "FACING": 60,
    "BENDING": 60,
    "HEAT": 15,
    "COATING": 10,
}

OPERATION_NAMES = {
    "COILING": "Навивка",
    "FACING": "Торцовка",
    "BENDING": "Загиб",
    "HEAT": "Термичка",
    "COATING": "Покрытие",
}

REQUIRED_GROUP_BY_OPERATION = {
    "COILING": "COIL_A",
    "FACING": "FACE",
    "BENDING": "BEND",
    "HEAT": "HEAT",
    "COATING": "COAT_A",
}

MACHINES_BY_OPERATION = {
    "COILING": ["NW1", "NW2", "NW3"],
    "FACING": ["TC1"],
    "BENDING": ["ZG1"],
    "HEAT": ["TM1"],
    "COATING": ["PK1"],
}

MACHINE_ROWS = [
    ("NW1", "COIL_A", "Навивка NW1"),
    ("NW2", "COIL_A", "Навивка NW2"),
    ("NW3", "COIL_A", "Навивка NW3"),
    ("TC1", "FACE", "Торцовка TC1"),
    ("ZG1", "BEND", "Загиб ZG1"),
    ("TM1", "HEAT", "Термичка TM1"),
    ("PK1", "COAT_A", "Покрытие PK1"),
]

REMOVED_TEST_MACHINES = ["NW4", "NW5", "NW6", "TC2", "ZG2", "PK2"]

ORDER_ITEMS = [
    {
        "order_id": 1,
        "order_no": "ORD-001",
        "item_id": 1,
        "product_key": "SMALL-1",
        "product_name": "Пружина малая 1",
        "quantity": 1000,
        "route": ["COILING", "HEAT", "COATING"],
        "coiling_rate": 10,
    },
    {
        "order_id": 2,
        "order_no": "ORD-002",
        "item_id": 2,
        "product_key": "SMALL-2",
        "product_name": "Пружина малая 2",
        "quantity": 1200,
        "route": ["COILING", "FACING", "HEAT", "COATING"],
        "coiling_rate": 10,
    },
    {
        "order_id": 3,
        "order_no": "ORD-003",
        "item_id": 3,
        "product_key": "SMALL-3",
        "product_name": "Пружина малая 3",
        "quantity": 1500,
        "route": ["COILING", "HEAT", "COATING"],
        "coiling_rate": 10,
    },
    {
        "order_id": 4,
        "order_no": "ORD-004",
        "item_id": 4,
        "product_key": "MEDIUM-1",
        "product_name": "Пружина средняя 1",
        "quantity": 4000,
        "route": ["COILING", "HEAT", "COATING"],
        "coiling_rate": 22,
    },
    {
        "order_id": 5,
        "order_no": "ORD-005",
        "item_id": 5,
        "product_key": "MEDIUM-2",
        "product_name": "Пружина средняя 2",
        "quantity": 4500,
        "route": ["COILING", "HEAT", "COATING"],
        "coiling_rate": 24,
    },
    {
        "order_id": 6,
        "order_no": "ORD-006",
        "item_id": 6,
        "product_key": "MEDIUM-3",
        "product_name": "Пружина средняя 3",
        "quantity": 5000,
        "route": ["COILING", "BENDING", "HEAT", "COATING"],
        "coiling_rate": 25,
    },
    {
        "order_id": 7,
        "order_no": "ORD-007",
        "item_id": 7,
        "product_key": "MEDIUM-4",
        "product_name": "Пружина средняя 4",
        "quantity": 5500,
        "route": ["COILING", "HEAT", "COATING"],
        "coiling_rate": 27,
    },
    {
        "order_id": 8,
        "order_no": "ORD-008",
        "item_id": 8,
        "product_key": "BIG",
        "product_name": "Пружина большая",
        "quantity": 3000,
        "route": ["COILING", "HEAT", "COATING"],
        "coiling_rate": 12,
    },
    {
        "order_id": 8,
        "order_no": "ORD-008",
        "item_id": 9,
        "product_key": "BIG",
        "product_name": "РџСЂСѓР¶РёРЅР° Р±РѕР»СЊС€Р°СЏ",
        "quantity": 3000,
        "route": ["COILING", "HEAT", "COATING"],
        "coiling_rate": 12,
    },
    {
        "order_id": 8,
        "order_no": "ORD-008",
        "item_id": 10,
        "product_key": "BIG",
        "product_name": "РџСЂСѓР¶РёРЅР° Р±РѕР»СЊС€Р°СЏ",
        "quantity": 3000,
        "route": ["COILING", "HEAT", "COATING"],
        "coiling_rate": 12,
    },
    {
        "order_id": 8,
        "order_no": "ORD-008",
        "item_id": 11,
        "product_key": "BIG",
        "product_name": "РџСЂСѓР¶РёРЅР° Р±РѕР»СЊС€Р°СЏ",
        "quantity": 1000,
        "route": ["COILING", "HEAT", "COATING"],
        "coiling_rate": 12,
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


def get_column_type(connection, table_name, column_name):
    return connection.execute(
        text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table_name
              AND column_name = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).scalar()


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


def product_id_for(index, product_key, product_id_type):
    if product_id_type in {"integer", "bigint", "smallint"}:
        return 1000 + index
    return f"TEST-{product_key}"


def build_product_map(connection):
    product_id_type = get_column_type(connection, "products", "id")
    products = {}
    next_index = 1
    for item in ORDER_ITEMS:
        if item["product_key"] in products:
            continue

        products[item["product_key"]] = {
            "id": product_id_for(next_index, item["product_key"], product_id_type),
            "name": item["product_name"],
        }
        next_index += 1

    return products


def reset_calendar(connection):
    connection.execute(text("DELETE FROM shift_template_breaks"))
    connection.execute(text("DELETE FROM shift_templates"))
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
                (1, 'Смена 1', 480, 1140, 0, 0, true),
                (2, 'Смена 2', 1200, 420, 0, 0, true)
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
                (1, 1, 'Обед', 240, 300),
                (2, 2, 'Обед', 240, 300)
            """
        )
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
                'Тестовая модель: смены 08:00–19:00 и 20:00–07:00'
            )
            ON CONFLICT (id) DO UPDATE
            SET
                name = EXCLUDED.name,
                status = EXCLUDED.status,
                created_by = EXCLUDED.created_by,
                description = EXCLUDED.description,
                approved_at = NULL,
                approved_by = NULL
            """
        )
    )


def reset_machines(connection, columns):
    machine_columns = columns["machines"]
    all_test_machine_ids = [row[0] for row in MACHINE_ROWS] + REMOVED_TEST_MACHINES
    connection.execute(
        text("DELETE FROM machine_product_rates WHERE machine_id = ANY(:machine_ids)"),
        {"machine_ids": all_test_machine_ids},
    )
    connection.execute(
        text("DELETE FROM machines WHERE id = ANY(:machine_ids)"),
        {"machine_ids": all_test_machine_ids},
    )

    for machine_id, group_id, name in MACHINE_ROWS:
        insert_row(
            connection,
            "machines",
            {
                "id": machine_id,
                "group_id": group_id,
                "name": name,
                "status": "active",
                "is_active": True,
            },
            machine_columns,
        )


def reset_products(connection, columns, products):
    product_columns = columns["products"]
    product_ids = [product["id"] for product in products.values()]
    connection.execute(
        text("DELETE FROM machine_product_rates WHERE product_id = ANY(:product_ids)"),
        {"product_ids": product_ids},
    )
    connection.execute(
        text("DELETE FROM products WHERE id = ANY(:product_ids)"),
        {"product_ids": product_ids},
    )

    for product in products.values():
        insert_row(
            connection,
            "products",
            {
                "id": product["id"],
                "name": product["name"],
                "description": product["name"],
                "status": "active",
                "is_active": True,
            },
            product_columns,
        )


def rate_for(item, operation_type):
    if operation_type == "COILING":
        return item["coiling_rate"]
    if operation_type == "HEAT":
        return 50
    if operation_type == "COATING":
        return 40
    if operation_type == "FACING":
        return 25
    if operation_type == "BENDING":
        return 25
    raise RuntimeError(f"Неизвестный тип операции: {operation_type}")


def reset_rates(connection, columns, products):
    rate_columns = columns["machine_product_rates"]
    created_rates = set()
    for item in ORDER_ITEMS:
        product_id = products[item["product_key"]]["id"]
        for operation_type in item["route"]:
            for machine_id in MACHINES_BY_OPERATION[operation_type]:
                rate_key = (product_id, machine_id, operation_type)
                if rate_key in created_rates:
                    continue

                insert_row(
                    connection,
                    "machine_product_rates",
                    {
                        "product_id": product_id,
                        "machine_id": machine_id,
                        "operation_type": operation_type,
                        "units_per_minute": rate_for(item, operation_type),
                        "setup_minutes": SETUP_MINUTES[operation_type],
                    },
                    rate_columns,
                )
                created_rates.add(rate_key)


def create_orders(connection, columns, products):
    order_columns = columns["orders"]
    order_item_columns = columns["order_items"]
    created_orders = set()

    for item in ORDER_ITEMS:
        due_values = {
            "due_date": 10080,
            "due_time": 10080,
        }
        if item["order_id"] not in created_orders:
            insert_row(
                connection,
                "orders",
                {
                    "id": item["order_id"],
                    "order_no": item["order_no"],
                    "customer": "Тестовый заказчик",
                    "status": "created",
                    **due_values,
                },
                order_columns,
            )
            created_orders.add(item["order_id"])

        insert_row(
            connection,
            "order_items",
            {
                "id": item["item_id"],
                "order_id": item["order_id"],
                "product_id": products[item["product_key"]]["id"],
                "quantity": item["quantity"],
                "priority": item["item_id"],
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

    for item in ORDER_ITEMS:
        routing_id = 1000 + item["item_id"]
        insert_row(
            connection,
            "routings",
            {
                "id": routing_id,
                "product_id": products[item["product_key"]]["id"],
                "name": f"Маршрут {item['order_no']} / {item['product_name']}",
                "status": "active",
                "is_active": True,
            },
            routing_columns,
        )

        for index, operation_type in enumerate(item["route"], start=1):
            routing_operation_id = next_routing_operation_id
            next_routing_operation_id += 1
            sequence_no = index * 10
            machine_group_id = REQUIRED_GROUP_BY_OPERATION[operation_type]

            insert_row(
                connection,
                "routing_operations",
                {
                    "id": routing_operation_id,
                    "routing_id": routing_id,
                    "step_no": sequence_no,
                    "sequence_no": sequence_no,
                    "operation_type": operation_type,
                    "operation_name": OPERATION_NAMES[operation_type],
                    "required_machine_group_id": machine_group_id,
                    "is_mandatory": True,
                    "transfer_batch_allowed": False,
                    "buffer_minutes": ROUTE_GAP_MINUTES,
                },
                routing_operation_columns,
            )
            routing_operation_by_key[(item["item_id"], operation_type)] = {
                "id": routing_operation_id,
                "sequence_no": sequence_no,
            }

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

    for item in ORDER_ITEMS:
        for operation_type in item["route"]:
            routing_operation = routing_operation_by_key[
                (item["item_id"], operation_type)
            ]
            order_operation_id = next_order_operation_id
            next_order_operation_id += 1

            insert_row(
                connection,
                "order_operations",
                {
                    "id": order_operation_id,
                    "order_item_id": item["item_id"],
                    "routing_operation_id": routing_operation["id"],
                    "operation_type": operation_type,
                    "sequence_no": routing_operation["sequence_no"],
                    "quantity": item["quantity"],
                    "status": "created",
                },
                order_operation_columns,
            )
            order_operation_by_key[(item["item_id"], operation_type)] = order_operation_id

    return order_operation_by_key


def build_work_intervals(days):
    intervals = []
    for day in range(days):
        day_offset = day * 1440
        intervals.extend(
            [
                (day_offset + 0, day_offset + 240),
                (day_offset + 300, day_offset + 660),
                (day_offset + 720, day_offset + 960),
                (day_offset + 1020, day_offset + 1380),
            ]
        )
    return intervals


def overlaps(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def find_conflict_end(intervals, start, end, gap_after=0):
    conflicting_ends = [
        busy_end + gap_after
        for busy_start, busy_end in intervals
        if overlaps(start, end, busy_start, busy_end + gap_after)
    ]
    return max(conflicting_ends) if conflicting_ends else None


def is_working_minute_local(minute, work_intervals):
    return any(start <= minute < end for start, end in work_intervals)


def next_working_start(start, work_intervals):
    candidate = int(start)
    for _ in range(14 * 1440):
        if is_working_minute_local(candidate, work_intervals):
            return candidate
        candidate += 1

    raise RuntimeError("Не удалось найти рабочее время смены")


def add_working_minutes_local(start, duration, work_intervals):
    cursor = int(start)
    remaining = int(duration)

    if remaining <= 0:
        return cursor

    for interval_start, interval_end in work_intervals:
        if interval_end <= cursor:
            continue

        if cursor < interval_start:
            cursor = interval_start

        if interval_start <= cursor < interval_end:
            available = interval_end - cursor
            if remaining <= available:
                return cursor + remaining
            remaining -= available
            cursor = interval_end

    raise RuntimeError(
        f"Не удалось добавить {duration} рабочих минут от старта {start}"
    )


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
        candidate_start = max(int(earliest_start), int(work_start))

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
                    continue

            return candidate_start

    raise RuntimeError(
        "Не удалось найти рабочий интервал смены для операции "
        f"machine_id={machine_id}, earliest_start={earliest_start}, duration={duration}"
    )


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
            f"Не найдена норма: product_id={product_id}, "
            f"machine_id={machine_id}, operation_type={operation_type}"
        )

    units_per_minute = float(rate["units_per_minute"] or 0)
    if units_per_minute <= 0:
        raise RuntimeError(
            f"Некорректная норма: product_id={product_id}, "
            f"machine_id={machine_id}, operation_type={operation_type}"
        )

    return units_per_minute, int(rate["setup_minutes"] or 0)


def get_machine_group(connection, machine_id):
    return connection.execute(
        text("SELECT group_id FROM machines WHERE id = :machine_id"),
        {"machine_id": machine_id},
    ).scalar()


def choose_machine_and_start(
    connection,
    product_id,
    operation_type,
    earliest_start,
    work_intervals,
    machine_busy,
    setup_team_busy,
    fixed_machine_id=None,
):
    for machine_id in MACHINES_BY_OPERATION[operation_type]:
        if fixed_machine_id is not None and machine_id != fixed_machine_id:
            continue

        units_per_minute, setup_minutes = get_rate(
            connection,
            product_id,
            operation_type,
            machine_id,
        )
        yield machine_id, units_per_minute, setup_minutes


def create_plan_operations(connection, columns, products, order_operation_by_key):
    plan_columns = columns["plan_operations"]
    order_item_ready_time = {}
    machine_busy = {}
    setup_team_busy = {}
    setup_seen = set()
    setup_ready_time = {}
    setup_machine_by_key = {}
    work_intervals = build_work_intervals(days=14)

    for item in ORDER_ITEMS:
        product_id = products[item["product_key"]]["id"]
        for operation_type in item["route"]:
            operation_id = order_operation_by_key[(item["item_id"], operation_type)]
            route_ready = order_item_ready_time.get(item["item_id"], 0)
            if item["item_id"] in order_item_ready_time:
                route_ready += ROUTE_GAP_MINUTES

            best = None
            setup_key = (item["order_id"], operation_type, product_id)
            for machine_id, units_per_minute, setup_minutes in choose_machine_and_start(
                connection,
                product_id,
                operation_type,
                route_ready,
                work_intervals,
                machine_busy,
                setup_team_busy,
                setup_machine_by_key.get(setup_key),
            ):
                is_first_setup_for_key = setup_key not in setup_seen
                planned_setup_minutes = (
                    setup_minutes if is_first_setup_for_key else 0
                )
                duration = ceil(item["quantity"] / units_per_minute) + planned_setup_minutes
                candidate_ready = max(
                    route_ready,
                    setup_ready_time.get(setup_key, route_ready),
                )

                machine_group_id = get_machine_group(connection, machine_id)
                setup_team_id = SETUP_TEAM_BY_MACHINE_GROUP.get(machine_group_id)
                start_time = find_earliest_start(
                    candidate_ready,
                    duration,
                    planned_setup_minutes,
                    machine_id,
                    setup_team_id,
                    work_intervals,
                    machine_busy,
                    setup_team_busy,
                )
                machine_load = len(machine_busy.get(machine_id, []))
                candidate = (
                    start_time,
                    machine_load,
                    machine_id,
                    duration,
                    planned_setup_minutes,
                    setup_team_id,
                    setup_key,
                    is_first_setup_for_key,
                )
                if best is None or candidate[:3] < best[:3]:
                    best = candidate

            (
                start_time,
                _machine_load,
                machine_id,
                duration,
                setup_minutes,
                setup_team_id,
                setup_key,
                is_first_setup_for_key,
            ) = best
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
                    "setup_minutes": setup_minutes,
                    "is_locked": False,
                    "lock_reason": None,
                },
                plan_columns,
            )
            order_item_ready_time[item["item_id"]] = end_time
            machine_busy.setdefault(machine_id, []).append((start_time, end_time))
            if setup_minutes > 0 and setup_team_id:
                setup_team_busy.setdefault(setup_team_id, []).append(
                    (start_time, start_time + setup_minutes)
                )
            if is_first_setup_for_key:
                setup_seen.add(setup_key)
                setup_machine_by_key[setup_key] = machine_id
                setup_ready_time[setup_key] = (
                    start_time + setup_minutes + MACHINE_GAP_MINUTES
                )


def reset_test_data(connection):
    for table_name in [
        "mes_operation_reports",
        "mes_schedule_operations",
        "mes_schedule_runs",
        "plan_change_log",
        "plan_operations",
        "order_operations",
        "order_items",
        "orders",
        "routing_operation_machine_groups",
        "routing_operations",
        "routings",
    ]:
        connection.execute(text(f"DELETE FROM {table_name}"))

    reset_plan_versions(connection)


def assert_counts(connection):
    expected = {
        "orders": 8,
        "order_items": 11,
        "order_operations": 35,
        "plan_operations": 35,
        "plan_change_log": 0,
        "mes_schedule_runs": 0,
        "mes_schedule_operations": 0,
        "mes_operation_reports": 0,
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

    coiling_machines = connection.execute(
        text(
            """
            SELECT id
            FROM machines
            WHERE group_id LIKE 'COIL%'
            ORDER BY id
            """
        )
    ).scalars().all()
    if coiling_machines != ["NW1", "NW2", "NW3"]:
        raise RuntimeError(
            "Ожидались только станки навивки NW1, NW2, NW3, "
            f"получено {coiling_machines}"
        )

    max_end = connection.execute(
        text(
            """
            SELECT MAX(end_time)
            FROM plan_operations
            WHERE plan_version_id = 1
            """
        )
    ).scalar()
    if int(max_end or 0) <= 1380:
        raise RuntimeError(
            "Ожидалось, что план растянется больше чем на две смены "
            f"по горизонту, max_end={max_end}"
        )


def seed_compact_test_data():
    init_db_schema()

    table_names = [
        "products",
        "machines",
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
        products = build_product_map(connection)

        reset_test_data(connection)
        reset_calendar(connection)
        reset_machines(connection, columns)
        reset_products(connection, columns, products)
        reset_rates(connection, columns, products)
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
            "shift_templates",
            "shift_template_breaks",
            "products",
            "machines",
            "orders",
            "order_items",
            "routings",
            "routing_operations",
            "routing_operation_machine_groups",
            "order_operations",
            "plan_operations",
            "machine_product_rates",
            "mes_schedule_runs",
            "mes_schedule_operations",
            "mes_operation_reports",
        ]:
            sync_sequence(connection, table_name)

        assert_counts(connection)

    print("Новая тестовая модель APS-MES загружена")
    print("orders=8, order_items=11, order_operations=35, plan_operations=35")
    print("MES-задания, отчёты и логи очищены")
    print("Смены: 08:00–19:00 и 20:00–07:00, обеды 12:00–13:00 и 00:00–01:00")
    print("Станки навивки: NW1, NW2, NW3")


if __name__ == "__main__":
    seed_compact_test_data()
