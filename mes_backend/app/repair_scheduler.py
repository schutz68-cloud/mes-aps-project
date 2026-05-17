from math import ceil

from sqlalchemy import text

from app.calendar_utils import build_work_intervals, is_inside_work_interval
from app.models import PlanChangeLog


INTER_OPERATION_GAP_MINUTES = 30
MACHINE_OPERATION_GAP_MINUTES = 15
MAX_ITERATIONS = 1000


class RepairSchedulerError(Exception):
    pass


def _calculate_duration(operation):
    units_per_minute = float(operation["units_per_minute"] or 0)
    if units_per_minute <= 0:
        raise RepairSchedulerError("для операции задана некорректная норма производительности")

    return ceil(int(operation["quantity"]) / units_per_minute) + int(
        operation["setup_minutes"] or 0
    )


def _load_operations(db, plan_version_id):
    rows = db.execute(
        text(
            """
            SELECT
                po.plan_version_id,
                po.operation_id,
                po.machine_id,
                coalesce(po.start_time, 0) AS start_time,
                coalesce(po.end_time, 0) AS end_time,
                coalesce(po.is_locked, false) AS is_locked,
                po.lock_reason,
                oo.order_item_id,
                oo.sequence_no,
                CASE
                    WHEN oo.sequence_no = MIN(oo.sequence_no) OVER (
                        PARTITION BY oo.order_item_id
                    )
                    THEN true
                    ELSE false
                END AS is_first_order_item_operation,
                oo.quantity,
                oo.operation_type,
                oi.product_id,
                m.group_id AS machine_group_id,
                mgst.setup_team_id,
                coalesce(mpr.setup_minutes, 0) AS setup_minutes,
                mpr.units_per_minute
            FROM plan_operations po
            JOIN order_operations oo ON oo.id = po.operation_id
            JOIN order_items oi ON oi.id = oo.order_item_id
            LEFT JOIN machines m ON m.id = po.machine_id
            LEFT JOIN machine_group_setup_teams mgst ON mgst.machine_group_id = m.group_id
            LEFT JOIN machine_product_rates mpr
              ON mpr.product_id = oi.product_id
             AND mpr.machine_id = po.machine_id
             AND mpr.operation_type = oo.operation_type
            WHERE po.plan_version_id = :plan_version_id
            ORDER BY po.start_time, po.operation_id
            """
        ),
        {"plan_version_id": plan_version_id},
    ).mappings().all()

    operations = []
    for row in rows:
        if row["units_per_minute"] is None:
            raise RepairSchedulerError(
                f"для операции {row['operation_id']} нет нормы на выбранном станке"
            )

        operation = dict(row)
        operation["start_time"] = int(operation["start_time"])
        operation["end_time"] = int(operation["end_time"])
        operation["is_first_order_item_operation"] = bool(
            operation["is_first_order_item_operation"]
        )
        operation["duration_minutes"] = _calculate_duration(operation)
        if int(operation["setup_minutes"] or 0) > 0 and not operation.get("setup_team_id"):
            raise RepairSchedulerError(
                "Для группы оборудования не назначена бригада наладчиков"
            )
        operations.append(operation)

    return operations


def _get_machine_required_start(previous_operation, operation):
    if operation["is_first_order_item_operation"]:
        return (
            previous_operation["start_time"]
            + int(previous_operation["setup_minutes"] or 0)
            + MACHINE_OPERATION_GAP_MINUTES
        )

    return previous_operation["end_time"] + MACHINE_OPERATION_GAP_MINUTES


def _assert_can_shift(operation, freeze_horizon_minutes):
    if operation["start_time"] < freeze_horizon_minutes:
        raise RepairSchedulerError(
            "Невозможно перепланировать: операция находится в замороженной зоне плана"
        )

    if operation["is_locked"]:
        raise RepairSchedulerError(
            "Невозможно перепланировать: операция зафиксирована вручную"
        )


def _overlaps(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def _find_conflict_end(intervals, start, end, gap_after=0):
    conflicting_ends = [
        busy_end + gap_after
        for busy_start, busy_end in intervals
        if _overlaps(start, end, busy_start, busy_end + gap_after)
    ]

    return max(conflicting_ends) if conflicting_ends else None


def _build_busy_maps(operations, exclude_operation_id=None):
    machine_busy = {}
    setup_team_busy = {}

    for operation in operations:
        if operation["operation_id"] == exclude_operation_id:
            continue

        machine_busy.setdefault(operation["machine_id"], []).append(
            (operation["start_time"], operation["end_time"])
        )

        setup_minutes = int(operation["setup_minutes"] or 0)
        setup_team_id = operation.get("setup_team_id")

        if setup_minutes > 0 and setup_team_id:
            setup_team_busy.setdefault(setup_team_id, []).append(
                (
                    operation["start_time"],
                    operation["start_time"] + setup_minutes,
                )
            )

    return machine_busy, setup_team_busy


def _find_earliest_feasible_start(
    db,
    operations,
    operation,
    earliest_start,
):
    duration = int(operation["duration_minutes"])
    setup_minutes = int(operation["setup_minutes"] or 0)
    setup_team_id = operation.get("setup_team_id")

    if setup_minutes > 0 and not setup_team_id:
        raise RepairSchedulerError(
            "Для группы оборудования не назначена бригада наладчиков"
        )

    max_end_time = max(
        max((int(op["end_time"]) for op in operations), default=0),
        int(earliest_start) + duration,
    )
    work_intervals = build_work_intervals(db, max_end_time + 1440)

    machine_busy, setup_team_busy = _build_busy_maps(
        operations,
        exclude_operation_id=operation["operation_id"],
    )
    blocked_by_machine = False
    blocked_by_setup_team = False

    for work_start, work_end in work_intervals:
        candidate_start = max(int(earliest_start), int(work_start))

        while True:
            candidate_end = candidate_start + duration

            if candidate_end > work_end:
                break

            machine_conflict_end = _find_conflict_end(
                machine_busy.get(operation["machine_id"], []),
                candidate_start,
                candidate_end,
                MACHINE_OPERATION_GAP_MINUTES,
            )

            if machine_conflict_end is not None:
                blocked_by_machine = True
                candidate_start = machine_conflict_end
                continue

            if setup_minutes > 0 and setup_team_id:
                setup_conflict_end = _find_conflict_end(
                    setup_team_busy.get(setup_team_id, []),
                    candidate_start,
                    candidate_start + setup_minutes,
                    MACHINE_OPERATION_GAP_MINUTES,
                )

                if setup_conflict_end is not None:
                    blocked_by_setup_team = True
                    candidate_start = setup_conflict_end
                    continue

            if is_inside_work_interval(db, candidate_start, candidate_end):
                return candidate_start

            break

    if blocked_by_setup_team:
        raise RepairSchedulerError("Нет свободного окна у бригады наладчиков")

    if blocked_by_machine:
        raise RepairSchedulerError("Нет свободного окна на выбранном станке")

    raise RepairSchedulerError("Операция не помещается в рабочий интервал смены")


def _shift_operation(
    db,
    operations,
    operation,
    requested_start,
    changed_operations,
    change_set_id,
    plan_version_id,
):
    new_start = _find_earliest_feasible_start(
        db=db,
        operations=operations,
        operation=operation,
        earliest_start=requested_start,
    )

    if new_start <= operation["start_time"]:
        return False

    old_machine_id = operation["machine_id"]
    old_start_time = operation["start_time"]
    old_end_time = operation["end_time"]
    new_end = new_start + operation["duration_minutes"]

    db.execute(
        text(
            """
            UPDATE plan_operations
            SET start_time = :start_time,
                end_time = :end_time
            WHERE operation_id = :operation_id
              AND plan_version_id = :plan_version_id
            """
        ),
        {
            "operation_id": operation["operation_id"],
            "plan_version_id": plan_version_id,
            "start_time": new_start,
            "end_time": new_end,
        },
    )

    db.add(
        PlanChangeLog(
            change_set_id=change_set_id,
            plan_version_id=plan_version_id,
            operation_id=operation["operation_id"],
            old_machine_id=old_machine_id,
            new_machine_id=old_machine_id,
            old_start_time=old_start_time,
            old_end_time=old_end_time,
            new_start_time=new_start,
            new_end_time=new_end,
            change_reason="repair_scheduler_shift",
        )
    )

    operation["start_time"] = new_start
    operation["end_time"] = new_end
    changed_operations[operation["operation_id"]] = {
        "id": operation["operation_id"],
        "plan_version_id": plan_version_id,
        "machine": operation["machine_id"],
        "start": operation["start_time"],
        "end": operation["end_time"],
    }
    return True


def _validate_plan(db, operations):
    by_machine = {}
    by_order_item = {}

    for operation in operations:
        by_machine.setdefault(operation["machine_id"], []).append(operation)
        by_order_item.setdefault(operation["order_item_id"], []).append(operation)

    for machine_operations in by_machine.values():
        ordered = sorted(
            machine_operations,
            key=lambda op: (op["start_time"], op["end_time"], op["operation_id"]),
        )
        previous = None
        for operation in ordered:
            if previous:
                required_start = _get_machine_required_start(previous, operation)
                if operation["start_time"] < required_start:
                    raise RepairSchedulerError(
                        "Невозможно восстановить допустимый план после изменения"
                    )
            previous = operation

    max_end_time = max((int(op["end_time"]) for op in operations), default=0)
    work_intervals = build_work_intervals(db, max_end_time)

    for operation in operations:
        start_time = int(operation["start_time"])
        end_time = int(operation["end_time"])

        if not any(
            start_time >= interval_start and end_time <= interval_end
            for interval_start, interval_end in work_intervals
        ):
            raise RepairSchedulerError("Операция выходит за рабочий интервал смены")

    setup_intervals_by_team = {}

    for operation in operations:
        setup_minutes = int(operation["setup_minutes"] or 0)
        if setup_minutes <= 0:
            continue

        setup_team_id = operation.get("setup_team_id")
        if not setup_team_id:
            raise RepairSchedulerError(
                "Для группы оборудования не назначена бригада наладчиков"
            )

        setup_intervals_by_team.setdefault(setup_team_id, []).append(operation)

    for team_operations in setup_intervals_by_team.values():
        ordered = sorted(
            team_operations,
            key=lambda op: (op["start_time"], op["operation_id"]),
        )

        previous = None
        for operation in ordered:
            if previous:
                previous_setup_end = (
                    int(previous["start_time"])
                    + int(previous["setup_minutes"] or 0)
                    + MACHINE_OPERATION_GAP_MINUTES
                )

                if int(operation["start_time"]) < previous_setup_end:
                    raise RepairSchedulerError(
                        "Конфликт наладчиков: наладки одной бригады пересекаются"
                    )

            previous = operation

    for item_operations in by_order_item.values():
        ordered = sorted(
            item_operations,
            key=lambda op: (op["sequence_no"], op["operation_id"]),
        )
        previous = None
        for operation in ordered:
            if (
                previous
                and operation["start_time"]
                < previous["end_time"] + INTER_OPERATION_GAP_MINUTES
            ):
                raise RepairSchedulerError(
                    "Невозможно восстановить допустимый план после изменения"
                )
            previous = operation


def repair_plan_after_manual_move(
    db,
    changed_operation_id: int,
    freeze_horizon_minutes: int,
    change_set_id: str,
    plan_version_id: int,
) -> list[dict]:
    operations = _load_operations(db, plan_version_id)
    changed_operations = {}

    if not any(op["operation_id"] == changed_operation_id for op in operations):
        raise RepairSchedulerError("изменённая операция не найдена в плане")

    for _ in range(MAX_ITERATIONS):
        moved = False

        by_order_item = {}
        for operation in operations:
            by_order_item.setdefault(operation["order_item_id"], []).append(operation)

        for item_operations in by_order_item.values():
            ordered = sorted(
                item_operations,
                key=lambda op: (op["sequence_no"], op["operation_id"]),
            )
            previous = None
            for operation in ordered:
                if previous:
                    required_start = previous["end_time"] + INTER_OPERATION_GAP_MINUTES
                    if required_start > operation["start_time"]:
                        _assert_can_shift(operation, freeze_horizon_minutes)
                        moved = (
                            _shift_operation(
                                db,
                                operations,
                                operation,
                                required_start,
                                changed_operations,
                                change_set_id,
                                plan_version_id,
                            )
                            or moved
                        )
                previous = operation

        by_machine = {}
        for operation in operations:
            by_machine.setdefault(operation["machine_id"], []).append(operation)

        for machine_operations in by_machine.values():
            ordered = sorted(
                machine_operations,
                key=lambda op: (op["start_time"], op["end_time"], op["operation_id"]),
            )
            previous = None
            for operation in ordered:
                if previous:
                    required_start = _get_machine_required_start(previous, operation)
                    if required_start > operation["start_time"]:
                        _assert_can_shift(operation, freeze_horizon_minutes)
                        moved = (
                            _shift_operation(
                                db,
                                operations,
                                operation,
                                required_start,
                                changed_operations,
                                change_set_id,
                                plan_version_id,
                            )
                            or moved
                        )
                previous = operation

        if not moved:
            _validate_plan(db, operations)
            return list(changed_operations.values())

    raise RepairSchedulerError("превышен лимит итераций перепланирования")
