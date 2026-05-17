from fastapi import FastAPI, WebSocket, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
from uuid import uuid4
from fastapi.middleware.cors import CORSMiddleware
from app.calendar_utils import (
    build_non_working_intervals,
    build_work_intervals,
    is_inside_work_interval,
)
from app.db import SessionLocal, init_db_schema
from app.models import (
    MesScheduleOperation,
    MesScheduleRun,
    PlanOperation,
    PlanChangeLog,
    PlanVersion,
    SystemSetting,
)
from app.repair_scheduler import RepairSchedulerError, repair_plan_after_manual_move
from app.websocket import connect, disconnect, broadcast, broadcast_sync
from sqlalchemy import text
from math import ceil


app = FastAPI(title="MES APS Backend")


@app.on_event("startup")
def startup():
    init_db_schema()


# ======================
# REQUEST MODELS
# ======================
class OperationUpdatePayload(BaseModel):
    id: Optional[int] = None
    machine: str
    start: int
    end: int


class FreezeHorizonPayload(BaseModel):
    minutes: int


class PlanVersionUpdatePayload(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class MesScheduleRunCreatePayload(BaseModel):
    period: Optional[str] = "tomorrow"
    start_minute: Optional[int] = None
    end_minute: Optional[int] = None
    description: Optional[str] = None


# ======================
# APS SETTINGS
# ======================
DEFAULT_FREEZE_HORIZON_MINUTES = 200
FREEZE_HORIZON_SETTING_KEY = "freeze_horizon_minutes"
PRODUCTION_MANAGER_ROLE = "production_manager"
INTER_OPERATION_GAP_MINUTES = 30
MACHINE_OPERATION_GAP_MINUTES = 15


def get_freeze_horizon_minutes(db) -> int:
    setting = (
        db.query(SystemSetting)
        .filter(SystemSetting.key == FREEZE_HORIZON_SETTING_KEY)
        .first()
    )

    if not setting:
        setting = SystemSetting(
            key=FREEZE_HORIZON_SETTING_KEY,
            value=str(DEFAULT_FREEZE_HORIZON_MINUTES),
            description="Горизонт заморозки плана в минутах от начала планового горизонта",
        )
        db.add(setting)
        db.commit()
        db.refresh(setting)

    try:
        return int(setting.value)
    except (TypeError, ValueError):
        return DEFAULT_FREEZE_HORIZON_MINUTES


def get_active_plan_version_id(db) -> int:
    active_versions = (
        db.query(PlanVersion)
        .filter(PlanVersion.status == "active")
        .order_by(PlanVersion.id.desc())
        .all()
    )

    if active_versions:
        if len(active_versions) > 1:
            raise HTTPException(
                status_code=409,
                detail="Найдено несколько активных версий плана. Оставьте только одну активную версию",
            )
        return int(active_versions[0].id)

    try:
        active_version = PlanVersion(
            id=1,
            name="Основной план",
            status="active",
            created_by="system",
            description="Текущая активная версия плана",
        )
        db.add(active_version)
        db.execute(
            text(
                """
                UPDATE plan_operations
                SET plan_version_id = 1
                WHERE plan_version_id IS NULL
                """
            )
        )
        db.commit()
        return 1
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось создать активную версию плана: {error}",
        )


def serialize_plan_version(plan_version):
    return {
        "id": plan_version.id,
        "name": plan_version.name,
        "status": plan_version.status,
        "created_at": (
            plan_version.created_at.isoformat() if plan_version.created_at else None
        ),
        "created_by": plan_version.created_by,
        "approved_at": (
            plan_version.approved_at.isoformat() if plan_version.approved_at else None
        ),
        "approved_by": plan_version.approved_by,
        "description": plan_version.description,
    }


def _row_value(row, key):
    if isinstance(row, dict):
        return row.get(key)

    if hasattr(row, "_mapping"):
        return row._mapping.get(key)

    return getattr(row, key)


def serialize_mes_schedule_run(run):
    return {
        "id": _row_value(run, "id"),
        "source_plan_version_id": _row_value(run, "source_plan_version_id"),
        "start_minute": _row_value(run, "start_minute"),
        "end_minute": _row_value(run, "end_minute"),
        "status": _row_value(run, "status"),
        "created_at": (
            _row_value(run, "created_at").isoformat()
            if _row_value(run, "created_at")
            else None
        ),
        "created_by": _row_value(run, "created_by"),
        "released_at": (
            _row_value(run, "released_at").isoformat()
            if _row_value(run, "released_at")
            else None
        ),
        "released_by": _row_value(run, "released_by"),
        "cancelled_at": (
            _row_value(run, "cancelled_at").isoformat()
            if _row_value(run, "cancelled_at")
            else None
        ),
        "cancelled_by": _row_value(run, "cancelled_by"),
        "description": _row_value(run, "description"),
        "is_hidden": bool(_row_value(run, "is_hidden")),
    }


def serialize_mes_schedule_operation(row):
    return {
        "id": _row_value(row, "id"),
        "schedule_run_id": _row_value(row, "schedule_run_id"),
        "source_plan_operation_id": _row_value(row, "source_plan_operation_id"),
        "operation_id": _row_value(row, "operation_id"),
        "order_id": _row_value(row, "order_id"),
        "order_item_id": _row_value(row, "order_item_id"),
        "product_id": _row_value(row, "product_id"),
        "product_name": _row_value(row, "product_name"),
        "order_no": _row_value(row, "order_no"),
        "machine_id": _row_value(row, "machine_id"),
        "machine_name": _row_value(row, "machine_name"),
        "machine_group_id": _row_value(row, "machine_group_id"),
        "operation_type": _row_value(row, "operation_type"),
        "operation_name": _row_value(row, "operation_name"),
        "quantity": _row_value(row, "quantity"),
        "setup_minutes": _row_value(row, "setup_minutes"),
        "planned_start_time": _row_value(row, "planned_start_time"),
        "planned_end_time": _row_value(row, "planned_end_time"),
        "status": _row_value(row, "status"),
    }


def resolve_mes_schedule_period(payload: MesScheduleRunCreatePayload):
    period = payload.period or "tomorrow"

    if period == "today":
        start_minute = 0
        end_minute = 1440
        default_description = "Производственное задание на сегодня"
    elif period == "tomorrow":
        start_minute = 1440
        end_minute = 2880
        default_description = "Производственное задание на завтра"
    elif period == "custom":
        if payload.start_minute is None or payload.end_minute is None:
            raise HTTPException(
                status_code=400,
                detail="Для произвольного периода нужно указать начало и конец",
            )

        start_minute = int(payload.start_minute)
        end_minute = int(payload.end_minute)
        default_description = "Производственное задание за выбранный период"
    else:
        raise HTTPException(
            status_code=400,
            detail="Неизвестный период производственного задания",
        )

    if end_minute <= start_minute:
        raise HTTPException(
            status_code=400,
            detail="Конец периода должен быть больше начала периода",
        )

    description = (payload.description or "").strip() or default_description
    return start_minute, end_minute, description


def get_mes_schedule_run_or_404(db, run_id: int) -> MesScheduleRun:
    run = db.query(MesScheduleRun).filter(MesScheduleRun.id == run_id).first()

    if not run:
        raise HTTPException(status_code=404, detail="Производственное задание не найдено")

    return run


def assert_mes_schedule_run_composition_editable(run: MesScheduleRun):
    if run.status == "released":
        raise HTTPException(
            status_code=409,
            detail="Нельзя изменить состав задания, уже выпущенного в производство",
        )

    if run.status == "cancelled":
        raise HTTPException(
            status_code=409,
            detail="Нельзя изменить состав отменённого задания",
        )

    if run.status != "created":
        raise HTTPException(
            status_code=409,
            detail="Изменять состав можно только у созданного задания",
        )


def get_plan_version_or_404(db, plan_version_id: int) -> PlanVersion:
    plan_version = (
        db.query(PlanVersion)
        .filter(PlanVersion.id == plan_version_id)
        .first()
    )

    if not plan_version:
        raise HTTPException(
            status_code=404,
            detail=f"Версия плана {plan_version_id} не найдена",
        )

    return plan_version


def get_requested_plan_version_id(db, plan_version_id: Optional[int]) -> int:
    if plan_version_id is None:
        return get_active_plan_version_id(db)

    get_plan_version_or_404(db, plan_version_id)
    return int(plan_version_id)


def validate_plan_version(db, plan_version_id: int) -> dict:
    plan_version = get_plan_version_or_404(db, plan_version_id)
    errors = []
    warnings = []
    summary = {
        "operations_checked": 0,
        "duplicate_operations": 0,
        "missing_order_operations": 0,
        "missing_order_items": 0,
        "missing_routing_operations": 0,
        "missing_machines": 0,
        "missing_rates": 0,
        "invalid_rates": 0,
        "invalid_machine_groups": 0,
        "duration_errors": 0,
        "route_buffer_errors": 0,
        "machine_buffer_errors": 0,
        "machine_overlap_errors": 0,
        "frozen_zone_errors": 0,
        "missing_plan_operations": 0,
        "extra_plan_operations": 0,
        "calendar_errors": 0,
        "setup_team_conflicts": 0,
        "missing_setup_team_links": 0,
    }

    def operation_context(row):
        return {
            "operation_id": row.get("operation_id"),
            "order_no": row.get("order_no"),
            "product_name": row.get("product_name"),
            "operation_name": row.get("operation_name") or row.get("operation_type"),
            "machine_id": row.get("machine_id"),
        }

    def add_error(error_type, message, row=None, details=None, summary_key=None):
        if summary_key:
            summary[summary_key] = summary.get(summary_key, 0) + 1

        error = {
            "type": error_type,
            "operation_id": row.get("operation_id") if row else None,
            "message": message,
            "details": details or {},
        }

        if row:
            error.update(operation_context(row))

        errors.append(error)

    duplicate_rows = db.execute(
        text(
            """
            SELECT
                operation_id,
                COUNT(*) AS duplicate_count
            FROM plan_operations
            WHERE plan_version_id = :plan_version_id
            GROUP BY operation_id
            HAVING COUNT(*) > 1
            """
        ),
        {"plan_version_id": plan_version_id},
    ).mappings().all()

    for row in duplicate_rows:
        add_error(
            "duplicate_plan_operation",
            "В версии плана найдены дубли плановой операции",
            {"operation_id": row["operation_id"]},
            {"duplicate_count": row["duplicate_count"]},
            "duplicate_operations",
        )

    rows = [
        dict(row)
        for row in db.execute(
            text(
                """
                SELECT
                    po.plan_version_id,
                    po.operation_id,
                    po.machine_id,
                    po.start_time,
                    po.end_time,
                    COALESCE(po.is_locked, false) AS is_locked,

                    oo.id AS order_operation_id,
                    oo.order_item_id,
                    oo.routing_operation_id,
                    oo.sequence_no,
                    oo.operation_type,
                    oo.quantity,

                    oi.id AS existing_order_item_id,
                    oi.order_id,
                    oi.product_id,

                    o.order_no,

                    p.name AS product_name,

                    ro.id AS existing_routing_operation_id,
                    ro.operation_name,

                    m.id AS existing_machine_id,
                    m.group_id AS machine_group_id,
                    m.name AS machine_name,

                    mpr.units_per_minute,
                    COALESCE(mpr.setup_minutes, 0) AS setup_minutes
                FROM plan_operations po
                LEFT JOIN order_operations oo
                  ON oo.id = po.operation_id
                LEFT JOIN order_items oi
                  ON oi.id = oo.order_item_id
                LEFT JOIN orders o
                  ON o.id = oi.order_id
                LEFT JOIN products p
                  ON p.id = oi.product_id
                LEFT JOIN routing_operations ro
                  ON ro.id = oo.routing_operation_id
                LEFT JOIN machines m
                  ON m.id = po.machine_id
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
    ]
    summary["operations_checked"] = len(rows)

    valid_route_rows = []
    valid_machine_rows = []

    for row in rows:
        has_critical_error = False

        if row["order_operation_id"] is None:
            add_error(
                "missing_order_operation",
                "Операция заказа не найдена для плановой операции",
                row,
                {},
                "missing_order_operations",
            )
            has_critical_error = True

        if row["order_operation_id"] is not None and row["existing_order_item_id"] is None:
            add_error(
                "missing_order_item",
                "Позиция заказа не найдена для операции",
                row,
                {},
                "missing_order_items",
            )
            has_critical_error = True

        if row["order_operation_id"] is not None and row["existing_routing_operation_id"] is None:
            add_error(
                "missing_routing_operation",
                "Операция маршрута не найдена для операции заказа",
                row,
                {},
                "missing_routing_operations",
            )
            has_critical_error = True

        if row["existing_machine_id"] is None:
            add_error(
                "missing_machine",
                "Станок не найден",
                row,
                {},
                "missing_machines",
            )
            has_critical_error = True

        if has_critical_error:
            continue

        allowed_group = db.execute(
            text(
                """
                SELECT 1
                FROM routing_operation_machine_groups
                WHERE routing_operation_id = :routing_operation_id
                  AND machine_group_id = :machine_group_id
                LIMIT 1
                """
            ),
            {
                "routing_operation_id": row["routing_operation_id"],
                "machine_group_id": row["machine_group_id"],
            },
        ).first()

        if not allowed_group:
            add_error(
                "invalid_machine_group",
                "Операция не может выполняться на выбранной группе оборудования",
                row,
                {"machine_group_id": row["machine_group_id"]},
                "invalid_machine_groups",
            )

        rate_is_valid = True
        if row["units_per_minute"] is None:
            add_error(
                "missing_rate",
                "Для изделия нет нормы на выбранном станке",
                row,
                {
                    "product_id": row["product_id"],
                    "machine_id": row["machine_id"],
                    "operation_type": row["operation_type"],
                },
                "missing_rates",
            )
            rate_is_valid = False
        elif float(row["units_per_minute"]) <= 0:
            add_error(
                "invalid_rate",
                "Для изделия задана некорректная норма на выбранном станке",
                row,
                {"units_per_minute": row["units_per_minute"]},
                "invalid_rates",
            )
            rate_is_valid = False

        if rate_is_valid:
            expected_duration = ceil(
                int(row["quantity"]) / float(row["units_per_minute"])
            ) + int(row["setup_minutes"] or 0)
            actual_duration = int(row["end_time"]) - int(row["start_time"])

            if actual_duration != expected_duration:
                add_error(
                    "duration_error",
                    "Длительность операции не соответствует норме",
                    row,
                    {
                        "expected_duration": expected_duration,
                        "actual_duration": actual_duration,
                        "setup_minutes": int(row["setup_minutes"] or 0),
                        "quantity": int(row["quantity"]),
                        "units_per_minute": float(row["units_per_minute"]),
                    },
                    "duration_errors",
                )

            valid_machine_rows.append(row)

        valid_route_rows.append(row)

    first_sequence_by_order_item = {}
    for row in valid_route_rows:
        order_item_id = row["order_item_id"]
        sequence_no = row["sequence_no"]
        if order_item_id is None or sequence_no is None:
            continue
        first_sequence_by_order_item[order_item_id] = min(
            first_sequence_by_order_item.get(order_item_id, sequence_no),
            sequence_no,
        )

    route_rows_by_order_item = {}
    for row in valid_route_rows:
        route_rows_by_order_item.setdefault(row["order_item_id"], []).append(row)

    for order_item_id, grouped_rows in route_rows_by_order_item.items():
        if order_item_id is None:
            continue
        ordered = sorted(grouped_rows, key=lambda row: (row["sequence_no"], row["operation_id"]))
        previous = None
        for row in ordered:
            if previous:
                required_start = int(previous["end_time"]) + INTER_OPERATION_GAP_MINUTES
                if int(row["start_time"]) < required_start:
                    add_error(
                        "route_buffer_error",
                        "Нарушен буфер между переделами изделия",
                        row,
                        {
                            "previous_operation_id": previous["operation_id"],
                            "previous_end_time": int(previous["end_time"]),
                            "required_start": required_start,
                            "actual_start": int(row["start_time"]),
                            "gap_minutes": INTER_OPERATION_GAP_MINUTES,
                        },
                        "route_buffer_errors",
                    )
            previous = row

    machine_rows_by_machine = {}
    for row in valid_machine_rows:
        machine_rows_by_machine.setdefault(row["machine_id"], []).append(row)

    for machine_id, grouped_rows in machine_rows_by_machine.items():
        if machine_id is None:
            continue
        ordered = sorted(
            grouped_rows,
            key=lambda row: (row["start_time"], row["end_time"], row["operation_id"]),
        )
        previous = None
        for row in ordered:
            if previous:
                is_first_order_item_operation = (
                    row["sequence_no"]
                    == first_sequence_by_order_item.get(row["order_item_id"])
                )

                if is_first_order_item_operation:
                    required_start = (
                        int(previous["start_time"])
                        + int(previous["setup_minutes"] or 0)
                        + MACHINE_OPERATION_GAP_MINUTES
                    )
                else:
                    required_start = int(previous["end_time"]) + MACHINE_OPERATION_GAP_MINUTES

                if int(row["start_time"]) < required_start:
                    add_error(
                        "machine_buffer_error",
                        "Нарушен буфер между операциями на станке",
                        row,
                        {
                            "previous_operation_id": previous["operation_id"],
                            "previous_start_time": int(previous["start_time"]),
                            "previous_end_time": int(previous["end_time"]),
                            "previous_setup_minutes": int(previous["setup_minutes"] or 0),
                            "required_start": required_start,
                            "actual_start": int(row["start_time"]),
                            "gap_minutes": MACHINE_OPERATION_GAP_MINUTES,
                        },
                        "machine_buffer_errors",
                    )

                if (
                    not is_first_order_item_operation
                    and int(row["start_time"]) < int(previous["end_time"])
                ):
                    add_error(
                        "machine_overlap_error",
                        "Операции пересекаются на одном станке",
                        row,
                        {
                            "previous_operation_id": previous["operation_id"],
                            "previous_end_time": int(previous["end_time"]),
                            "actual_start": int(row["start_time"]),
                        },
                        "machine_overlap_errors",
                    )
            previous = row

    max_end_time = max((int(row["end_time"]) for row in valid_machine_rows), default=0)
    work_intervals = build_work_intervals(db, max_end_time)

    for row in valid_machine_rows:
        start_time = int(row["start_time"])
        end_time = int(row["end_time"])
        is_inside_work_interval = any(
            start_time >= interval_start and end_time <= interval_end
            for interval_start, interval_end in work_intervals
        )

        if not is_inside_work_interval:
            add_error(
                "operation_calendar_error",
                "Операция выходит за рабочий интервал смены",
                row,
                {
                    "start_time": start_time,
                    "end_time": end_time,
                },
                "calendar_errors",
            )

    setup_team_links = {
        row["machine_group_id"]: row["setup_team_id"]
        for row in db.execute(
            text(
                """
                SELECT machine_group_id, setup_team_id
                FROM machine_group_setup_teams
                """
            )
        ).mappings().all()
    }
    setup_team_capacity = {
        row["id"]: int(row["capacity"] or 1)
        for row in db.execute(
            text(
                """
                SELECT id, COALESCE(capacity, 1) AS capacity
                FROM setup_teams
                WHERE COALESCE(is_active, true) = true
                """
            )
        ).mappings().all()
    }
    setup_intervals_by_team = {}
    warned_capacity_teams = set()

    for row in valid_machine_rows:
        setup_minutes = int(row["setup_minutes"] or 0)
        if setup_minutes <= 0:
            continue

        setup_team_id = setup_team_links.get(row["machine_group_id"])
        if not setup_team_id:
            add_error(
                "missing_setup_team_link",
                "Для группы оборудования не назначена бригада наладчиков",
                row,
                {"machine_group_id": row["machine_group_id"]},
                "missing_setup_team_links",
            )
            continue

        setup_team_capacity_value = setup_team_capacity.get(setup_team_id, 1)
        if setup_team_capacity_value > 1:
            if setup_team_id not in warned_capacity_teams:
                warnings.append(
                    {
                        "type": "setup_team_capacity_warning",
                        "message": (
                            "Проверка бригад наладчиков с capacity > 1 "
                            "пока не реализована"
                        ),
                        "details": {
                            "setup_team_id": setup_team_id,
                            "capacity": setup_team_capacity_value,
                        },
                    }
                )
                warned_capacity_teams.add(setup_team_id)
            continue

        setup_start = int(row["start_time"])
        setup_end = setup_start + setup_minutes
        setup_intervals_by_team.setdefault(setup_team_id, []).append(
            {
                "operation_id": row["operation_id"],
                "row": row,
                "setup_start": setup_start,
                "setup_end": setup_end,
            }
        )

    for setup_team_id, intervals in setup_intervals_by_team.items():
        previous = None
        for interval in sorted(
            intervals,
            key=lambda item: (
                item["setup_start"],
                item["setup_end"],
                item["operation_id"],
            ),
        ):
            if previous and interval["setup_start"] < previous["setup_end"]:
                add_error(
                    "setup_team_conflict",
                    "Конфликт наладчиков: наладки одной бригады пересекаются",
                    interval["row"],
                    {
                        "setup_team_id": setup_team_id,
                        "previous_operation_id": previous["operation_id"],
                        "previous_setup_start": previous["setup_start"],
                        "previous_setup_end": previous["setup_end"],
                        "setup_start": interval["setup_start"],
                        "setup_end": interval["setup_end"],
                    },
                    "setup_team_conflicts",
                )

            previous = interval

    if plan_version.status == "draft":
        active_plan_version_id = get_active_plan_version_id(db)
        active_operation_ids = {
            row["operation_id"]
            for row in db.execute(
                text(
                    """
                    SELECT operation_id
                    FROM plan_operations
                    WHERE plan_version_id = :plan_version_id
                    """
                ),
                {"plan_version_id": active_plan_version_id},
            ).mappings().all()
        }
        draft_operation_ids = {row["operation_id"] for row in rows}

        for operation_id in sorted(active_operation_ids - draft_operation_ids):
            add_error(
                "missing_plan_operation",
                "В версии плана отсутствует операция из active-плана",
                {"operation_id": operation_id},
                {"active_plan_version_id": active_plan_version_id},
                "missing_plan_operations",
            )

        for operation_id in sorted(draft_operation_ids - active_operation_ids):
            add_error(
                "extra_plan_operation",
                "В версии плана есть лишняя операция относительно active-плана",
                {"operation_id": operation_id},
                {"active_plan_version_id": active_plan_version_id},
                "extra_plan_operations",
            )

        freeze_horizon_minutes = get_freeze_horizon_minutes(db)
        frozen_rows = db.execute(
            text(
                """
                SELECT
                    draft_po.operation_id,
                    active_po.machine_id AS active_machine_id,
                    draft_po.machine_id AS draft_machine_id,
                    active_po.start_time AS active_start_time,
                    draft_po.start_time AS draft_start_time,
                    active_po.end_time AS active_end_time,
                    draft_po.end_time AS draft_end_time,
                    o.order_no,
                    p.name AS product_name,
                    ro.operation_name
                FROM plan_operations draft_po
                JOIN plan_operations active_po
                  ON active_po.operation_id = draft_po.operation_id
                LEFT JOIN order_operations oo
                  ON oo.id = draft_po.operation_id
                LEFT JOIN order_items oi
                  ON oi.id = oo.order_item_id
                LEFT JOIN orders o
                  ON o.id = oi.order_id
                LEFT JOIN products p
                  ON p.id = oi.product_id
                LEFT JOIN routing_operations ro
                  ON ro.id = oo.routing_operation_id
                WHERE draft_po.plan_version_id = :draft_plan_version_id
                  AND active_po.plan_version_id = :active_plan_version_id
                  AND active_po.start_time < :freeze_horizon_minutes
                  AND (
                      active_po.machine_id IS DISTINCT FROM draft_po.machine_id
                      OR active_po.start_time IS DISTINCT FROM draft_po.start_time
                      OR active_po.end_time IS DISTINCT FROM draft_po.end_time
                  )
                """
            ),
            {
                "draft_plan_version_id": plan_version_id,
                "active_plan_version_id": active_plan_version_id,
                "freeze_horizon_minutes": freeze_horizon_minutes,
            },
        ).mappings().all()

        for row in frozen_rows:
            add_error(
                "frozen_zone_error",
                "Операция в замороженной зоне отличается от active-плана",
                dict(row),
                {
                    "freeze_horizon_minutes": freeze_horizon_minutes,
                    "active_machine_id": row["active_machine_id"],
                    "draft_machine_id": row["draft_machine_id"],
                    "active_start_time": row["active_start_time"],
                    "draft_start_time": row["draft_start_time"],
                    "active_end_time": row["active_end_time"],
                    "draft_end_time": row["draft_end_time"],
                },
                "frozen_zone_errors",
            )

    is_valid = len(errors) == 0
    return {
        "status": "ok" if is_valid else "error",
        "is_valid": is_valid,
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
    }


# ======================
# CORS
# ======================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ======================
# ROOT
# ======================
@app.get("/")
def root():
    return {"status": "backend works"}


@app.get("/calendar/non_working_intervals")
def get_calendar_non_working_intervals(
    from_minute: int = 0,
    to_minute: int = 0,
):
    if to_minute <= from_minute:
        raise HTTPException(
            status_code=400,
            detail="Правая граница диапазона должна быть больше левой",
        )

    db = SessionLocal()
    try:
        return build_non_working_intervals(db, from_minute, to_minute)
    finally:
        db.close()


# ======================
# MES SCHEDULE RUNS
# ======================
@app.get("/mes/schedule_runs")
def list_mes_schedule_runs(include_hidden: bool = False):
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT
                    msr.*,
                    COUNT(mso.id) AS operations_count
                FROM mes_schedule_runs msr
                LEFT JOIN mes_schedule_operations mso
                  ON mso.schedule_run_id = msr.id
                WHERE (:include_hidden = true OR COALESCE(msr.is_hidden, false) = false)
                GROUP BY msr.id
                ORDER BY msr.id DESC
                """
            ),
            {"include_hidden": include_hidden},
        ).mappings().all()

        result = []
        for row in rows:
            item = serialize_mes_schedule_run(row)
            item["operations_count"] = int(row["operations_count"] or 0)
            result.append(item)

        return result
    finally:
        db.close()


@app.post("/mes/schedule_runs/from_active")
def create_mes_schedule_run_from_active(payload: MesScheduleRunCreatePayload):
    db = SessionLocal()
    try:
        start_minute, end_minute, description = resolve_mes_schedule_period(payload)
        active_plan_version_id = get_active_plan_version_id(db)
        validation_result = validate_plan_version(db, active_plan_version_id)

        if not validation_result["is_valid"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Нельзя создать производственное задание: активный план содержит ошибки",
                    "errors": validation_result["errors"],
                    "summary": validation_result["summary"],
                },
            )

        duplicate_run = (
            db.query(MesScheduleRun)
            .filter(MesScheduleRun.source_plan_version_id == active_plan_version_id)
            .filter(MesScheduleRun.start_minute == start_minute)
            .filter(MesScheduleRun.end_minute == end_minute)
            .filter(MesScheduleRun.status.in_(["created", "released"]))
            .first()
        )

        if duplicate_run:
            raise HTTPException(
                status_code=409,
                detail="Для этой версии плана и периода уже создано производственное задание",
            )

        operations_count = db.execute(
            text(
                """
                SELECT COUNT(*) AS operations_count
                FROM plan_operations po
                WHERE po.plan_version_id = :plan_version_id
                  AND po.start_time >= :start_minute
                  AND po.start_time < :end_minute
                """
            ),
            {
                "plan_version_id": active_plan_version_id,
                "start_minute": start_minute,
                "end_minute": end_minute,
            },
        ).scalar()

        if not operations_count:
            raise HTTPException(
                status_code=409,
                detail="В выбранном периоде нет операций для производственного задания",
            )

        run = MesScheduleRun(
            source_plan_version_id=active_plan_version_id,
            start_minute=start_minute,
            end_minute=end_minute,
            status="created",
            created_by="system",
            description=description,
        )
        db.add(run)
        db.flush()

        db.execute(
            text(
                """
                INSERT INTO mes_schedule_operations (
                    schedule_run_id,
                    source_plan_operation_id,
                    operation_id,
                    order_id,
                    order_item_id,
                    product_id,
                    product_name,
                    order_no,
                    machine_id,
                    machine_name,
                    machine_group_id,
                    operation_type,
                    operation_name,
                    quantity,
                    setup_minutes,
                    planned_start_time,
                    planned_end_time,
                    status
                )
                SELECT
                    :schedule_run_id,
                    po.id,
                    po.operation_id,
                    oi.order_id,
                    oo.order_item_id,
                    oi.product_id,
                    p.name,
                    o.order_no,
                    po.machine_id,
                    m.name,
                    m.group_id,
                    oo.operation_type,
                    ro.operation_name,
                    oo.quantity,
                    COALESCE(mpr.setup_minutes, 0),
                    po.start_time,
                    po.end_time,
                    'planned'
                FROM plan_operations po
                JOIN order_operations oo ON oo.id = po.operation_id
                JOIN order_items oi ON oi.id = oo.order_item_id
                LEFT JOIN orders o ON o.id = oi.order_id
                LEFT JOIN products p ON p.id = oi.product_id
                LEFT JOIN machines m ON m.id = po.machine_id
                LEFT JOIN routing_operations ro ON ro.id = oo.routing_operation_id
                LEFT JOIN machine_product_rates mpr
                  ON mpr.product_id = oi.product_id
                 AND mpr.machine_id = po.machine_id
                 AND mpr.operation_type = oo.operation_type
                WHERE po.plan_version_id = :plan_version_id
                  AND po.start_time >= :start_minute
                  AND po.start_time < :end_minute
                ORDER BY po.start_time, po.operation_id
                """
            ),
            {
                "schedule_run_id": run.id,
                "plan_version_id": active_plan_version_id,
                "start_minute": start_minute,
                "end_minute": end_minute,
            },
        )

        db.commit()
        db.refresh(run)

        return {
            "status": "ok",
            "run": serialize_mes_schedule_run(run),
            "operations_count": int(operations_count),
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось создать производственное задание: {error}",
        )
    finally:
        db.close()


@app.get("/mes/schedule_runs/{run_id}")
def get_mes_schedule_run(run_id: int):
    db = SessionLocal()
    try:
        run = get_mes_schedule_run_or_404(db, run_id)
        operations = (
            db.query(MesScheduleOperation)
            .filter(MesScheduleOperation.schedule_run_id == run_id)
            .order_by(
                MesScheduleOperation.planned_start_time,
                MesScheduleOperation.id,
            )
            .all()
        )

        return {
            "run": serialize_mes_schedule_run(run),
            "operations": [
                serialize_mes_schedule_operation(operation)
                for operation in operations
            ],
        }
    finally:
        db.close()


@app.post("/mes/schedule_runs/{run_id}/release")
def release_mes_schedule_run(run_id: int):
    db = SessionLocal()
    try:
        run = get_mes_schedule_run_or_404(db, run_id)

        if run.status == "released":
            raise HTTPException(
                status_code=409,
                detail="Производственное задание уже выпущено в производство",
            )

        if run.status == "cancelled":
            raise HTTPException(
                status_code=409,
                detail="Нельзя выпустить отменённое производственное задание",
            )

        if run.status != "created":
            raise HTTPException(
                status_code=409,
                detail="В производство можно выпустить только созданное задание",
            )

        run.status = "released"
        run.released_by = "system"
        db.execute(
            text(
                """
                UPDATE mes_schedule_runs
                SET released_at = now()
                WHERE id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        db.execute(
            text(
                """
                UPDATE mes_schedule_operations
                SET status = 'released'
                WHERE schedule_run_id = :run_id
                  AND status = 'planned'
                """
            ),
            {"run_id": run_id},
        )
        db.commit()
        db.refresh(run)

        return {"status": "ok", "run": serialize_mes_schedule_run(run)}

    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось выпустить производственное задание: {error}",
        )
    finally:
        db.close()


@app.post("/mes/schedule_runs/{run_id}/cancel")
def cancel_mes_schedule_run(run_id: int):
    db = SessionLocal()
    try:
        run = get_mes_schedule_run_or_404(db, run_id)

        if run.status == "released":
            raise HTTPException(
                status_code=409,
                detail="Нельзя отменить производственное задание, уже выпущенное в производство",
            )

        if run.status == "cancelled":
            raise HTTPException(
                status_code=409,
                detail="Производственное задание уже отменено",
            )

        if run.status != "created":
            raise HTTPException(
                status_code=409,
                detail="Отменить можно только созданное производственное задание",
            )

        run.status = "cancelled"
        run.cancelled_by = "system"
        db.execute(
            text(
                """
                UPDATE mes_schedule_runs
                SET cancelled_at = now()
                WHERE id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        db.commit()
        db.refresh(run)

        return {"status": "ok", "run": serialize_mes_schedule_run(run)}

    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось отменить производственное задание: {error}",
        )
    finally:
        db.close()


@app.post("/mes/schedule_runs/{run_id}/hide")
def hide_mes_schedule_run(run_id: int):
    db = SessionLocal()
    try:
        run = get_mes_schedule_run_or_404(db, run_id)
        run.is_hidden = True
        db.commit()
        db.refresh(run)

        return {"status": "ok", "run": serialize_mes_schedule_run(run)}

    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось скрыть производственное задание: {error}",
        )
    finally:
        db.close()


@app.post("/mes/schedule_runs/{run_id}/show")
def show_mes_schedule_run(run_id: int):
    db = SessionLocal()
    try:
        run = get_mes_schedule_run_or_404(db, run_id)
        run.is_hidden = False
        db.commit()
        db.refresh(run)

        return {"status": "ok", "run": serialize_mes_schedule_run(run)}

    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось вернуть производственное задание в список: {error}",
        )
    finally:
        db.close()


@app.post("/mes/schedule_runs/{run_id}/order_items/{order_item_id}/exclude")
def exclude_mes_schedule_order_item(run_id: int, order_item_id: int):
    db = SessionLocal()
    try:
        run = get_mes_schedule_run_or_404(db, run_id)
        assert_mes_schedule_run_composition_editable(run)

        operations_count = (
            db.query(MesScheduleOperation)
            .filter(MesScheduleOperation.schedule_run_id == run_id)
            .filter(MesScheduleOperation.order_item_id == order_item_id)
            .count()
        )

        if operations_count == 0:
            raise HTTPException(
                status_code=404,
                detail="Позиция заказа не найдена в производственном задании",
            )

        updated = db.execute(
            text(
                """
                UPDATE mes_schedule_operations
                SET status = 'excluded'
                WHERE schedule_run_id = :run_id
                  AND order_item_id = :order_item_id
                  AND status = 'planned'
                """
            ),
            {"run_id": run_id, "order_item_id": order_item_id},
        )
        db.commit()

        return {
            "status": "ok",
            "schedule_run_id": run_id,
            "order_item_id": order_item_id,
            "updated_operations": updated.rowcount or 0,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось исключить позицию из задания: {error}",
        )
    finally:
        db.close()


@app.post("/mes/schedule_runs/{run_id}/order_items/{order_item_id}/include")
def include_mes_schedule_order_item(run_id: int, order_item_id: int):
    db = SessionLocal()
    try:
        run = get_mes_schedule_run_or_404(db, run_id)
        assert_mes_schedule_run_composition_editable(run)

        operations_count = (
            db.query(MesScheduleOperation)
            .filter(MesScheduleOperation.schedule_run_id == run_id)
            .filter(MesScheduleOperation.order_item_id == order_item_id)
            .count()
        )

        if operations_count == 0:
            raise HTTPException(
                status_code=404,
                detail="Позиция заказа не найдена в производственном задании",
            )

        updated = db.execute(
            text(
                """
                UPDATE mes_schedule_operations
                SET status = 'planned'
                WHERE schedule_run_id = :run_id
                  AND order_item_id = :order_item_id
                  AND status = 'excluded'
                """
            ),
            {"run_id": run_id, "order_item_id": order_item_id},
        )
        db.commit()

        return {
            "status": "ok",
            "schedule_run_id": run_id,
            "order_item_id": order_item_id,
            "updated_operations": updated.rowcount or 0,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось вернуть позицию в задание: {error}",
        )
    finally:
        db.close()


# ======================
# PLAN VERSIONS
# ======================
@app.get("/plan_versions")
def get_plan_versions():
    db = SessionLocal()
    try:
        get_active_plan_version_id(db)
        rows = db.query(PlanVersion).order_by(PlanVersion.id).all()
        return [serialize_plan_version(row) for row in rows]
    finally:
        db.close()


@app.get("/plan_versions/active")
def get_active_plan_version():
    db = SessionLocal()
    try:
        active_plan_version_id = get_active_plan_version_id(db)
        active_version = (
            db.query(PlanVersion)
            .filter(PlanVersion.id == active_plan_version_id)
            .first()
        )
        if not active_version:
            raise HTTPException(
                status_code=404,
                detail="Активная версия плана не найдена",
            )
        return serialize_plan_version(active_version)
    finally:
        db.close()


@app.post("/plan_versions/clone_active")
def clone_active_plan_version():
    db = SessionLocal()
    try:
        active_plan_version_id = get_active_plan_version_id(db)
        active_version = get_plan_version_or_404(db, active_plan_version_id)

        draft_version = PlanVersion(
            name=f"Копия плана #{active_version.id}",
            status="draft",
            created_by="system",
            description=f"Копия активной версии плана #{active_version.id}",
        )
        db.add(draft_version)
        db.flush()

        copied_rows = db.execute(
            text(
                """
                INSERT INTO plan_operations (
                    plan_version_id,
                    operation_id,
                    machine_id,
                    start_time,
                    end_time,
                    is_locked,
                    lock_reason
                )
                SELECT
                    :draft_plan_version_id,
                    operation_id,
                    machine_id,
                    start_time,
                    end_time,
                    is_locked,
                    lock_reason
                FROM plan_operations
                WHERE plan_version_id = :active_plan_version_id
                """
            ),
            {
                "draft_plan_version_id": draft_version.id,
                "active_plan_version_id": active_plan_version_id,
            },
        )

        db.commit()
        db.refresh(draft_version)

        return {
            "status": "ok",
            "plan_version": serialize_plan_version(draft_version),
            "copied_operations": copied_rows.rowcount,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось создать копию активного плана: {error}",
        )
    finally:
        db.close()


@app.patch("/plan_versions/{plan_version_id}")
async def update_plan_version(
    plan_version_id: int,
    payload: PlanVersionUpdatePayload,
):
    db = SessionLocal()
    try:
        plan_version = get_plan_version_or_404(db, plan_version_id)

        if payload.name is not None:
            if plan_version.status != "draft":
                raise HTTPException(
                    status_code=409,
                    detail="Название можно редактировать только у черновой версии плана",
                )

            name = payload.name.strip()
            if not name:
                raise HTTPException(
                    status_code=400,
                    detail="Название версии плана не должно быть пустым",
                )
            plan_version.name = name

        if payload.description is not None:
            description = payload.description.strip()
            plan_version.description = description or None

        db.commit()
        db.refresh(plan_version)

        event = {
            "type": "plan_versions_updated",
            "data": {
                "updated_plan_version": serialize_plan_version(plan_version),
            },
        }

        try:
            await broadcast(event)
        except Exception as error:
            print(
                "Предупреждение: не удалось отправить WebSocket-событие "
                f"обновления версии плана: {error}"
            )

        return {
            "status": "ok",
            "plan_version": serialize_plan_version(plan_version),
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось обновить версию плана: {error}",
        )
    finally:
        db.close()


@app.delete("/plan_versions/{plan_version_id}")
async def delete_plan_version(plan_version_id: int):
    db = SessionLocal()
    try:
        plan_version = get_plan_version_or_404(db, plan_version_id)

        if plan_version.status != "draft":
            raise HTTPException(
                status_code=409,
                detail="Удалять можно только черновую версию плана",
            )

        deleted_change_log = db.execute(
            text(
                """
                DELETE FROM plan_change_log
                WHERE plan_version_id = :plan_version_id
                """
            ),
            {"plan_version_id": plan_version_id},
        )

        deleted_plan_operations = db.execute(
            text(
                """
                DELETE FROM plan_operations
                WHERE plan_version_id = :plan_version_id
                """
            ),
            {"plan_version_id": plan_version_id},
        )

        db.delete(plan_version)
        db.commit()

        event = {
            "type": "plan_versions_updated",
            "data": {
                "deleted_plan_version_id": plan_version_id,
            },
        }

        try:
            await broadcast(event)
        except Exception as error:
            print(
                "Предупреждение: не удалось отправить WebSocket-событие "
                f"удаления версии плана: {error}"
            )

        return {
            "status": "ok",
            "deleted_plan_version_id": plan_version_id,
            "deleted_plan_operations": deleted_plan_operations.rowcount or 0,
            "deleted_change_log": deleted_change_log.rowcount or 0,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось удалить черновую версию плана: {error}",
        )
    finally:
        db.close()


@app.get("/plan_versions/{plan_version_id}/validate")
def validate_plan_version_endpoint(plan_version_id: int):
    db = SessionLocal()
    try:
        get_plan_version_or_404(db, plan_version_id)
        return validate_plan_version(db, plan_version_id)
    finally:
        db.close()


@app.post("/plan_versions/{plan_version_id}/approve")
async def approve_plan_version(plan_version_id: int):
    db = SessionLocal()
    try:
        draft_version = get_plan_version_or_404(db, plan_version_id)

        if draft_version.status != "draft":
            raise HTTPException(
                status_code=409,
                detail="Принять можно только черновую версию плана",
            )

        validation_result = validate_plan_version(db, plan_version_id)
        if not validation_result["is_valid"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Нельзя принять черновик: план содержит ошибки",
                    "errors": validation_result["errors"],
                    "summary": validation_result["summary"],
                },
            )

        active_versions = (
            db.query(PlanVersion)
            .filter(PlanVersion.status == "active")
            .all()
        )

        if len(active_versions) != 1:
            raise HTTPException(
                status_code=409,
                detail="Найдено несколько активных версий плана. Оставьте только одну активную версию",
            )

        old_active_version = active_versions[0]
        old_active_version.status = "archived"

        draft_version.status = "active"
        draft_version.approved_by = "system"

        db.execute(
            text(
                """
                UPDATE plan_versions
                SET approved_at = now()
                WHERE id = :plan_version_id
                """
            ),
            {"plan_version_id": plan_version_id},
        )

        db.commit()
        db.refresh(draft_version)

        event = {
            "type": "plan_versions_updated",
            "data": {
                "active_plan_version_id": draft_version.id,
                "archived_plan_version_id": old_active_version.id,
            },
        }

        try:
            await broadcast(event)
        except Exception as error:
            print(
                "Предупреждение: не удалось отправить WebSocket-событие "
                f"принятия версии плана: {error}"
            )

        return {
            "status": "ok",
            "active_plan_version": serialize_plan_version(draft_version),
            "archived_plan_version_id": old_active_version.id,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось принять черновую версию плана: {error}",
        )
    finally:
        db.close()


@app.get("/plan_versions/{plan_version_id}/diff")
def get_plan_version_diff(plan_version_id: int):
    db = SessionLocal()
    try:
        draft_version = (
            db.query(PlanVersion)
            .filter(PlanVersion.id == plan_version_id)
            .first()
        )
        if not draft_version:
            raise HTTPException(
                status_code=404,
                detail=f"Версия плана {plan_version_id} не найдена",
            )

        if draft_version.status != "draft":
            raise HTTPException(
                status_code=409,
                detail="Сравнение доступно только для черновой версии плана",
            )

        active_plan_version_id = get_active_plan_version_id(db)
        active_version = (
            db.query(PlanVersion)
            .filter(PlanVersion.id == active_plan_version_id)
            .first()
        )
        if not active_version:
            raise HTTPException(
                status_code=404,
                detail="Активная версия плана не найдена",
            )

        rows = db.execute(
            text(
                """
                SELECT
                    draft_po.operation_id,
                    oi.order_id,
                    oi.id AS order_item_id,
                    o.order_no,
                    oi.product_id,
                    oi.due_date AS order_item_due_time,
                    o.due_date AS order_due_time,
                    p.name AS product_name,
                    oo.operation_type,
                    ro.operation_name,
                    oo.sequence_no,
                    active_po.machine_id AS active_machine,
                    draft_po.machine_id AS draft_machine,
                    coalesce(active_machine.name, active_po.machine_id)
                        AS active_machine_name,
                    coalesce(draft_machine.name, draft_po.machine_id)
                        AS draft_machine_name,
                    active_machine.group_id AS active_machine_group_id,
                    draft_machine.group_id AS draft_machine_group_id,
                    active_po.start_time AS active_start,
                    draft_po.start_time AS draft_start,
                    active_po.end_time AS active_end,
                    draft_po.end_time AS draft_end,
                    coalesce(active_rate.setup_minutes, 0) AS active_setup_minutes,
                    coalesce(draft_rate.setup_minutes, 0) AS draft_setup_minutes,
                    active_po.machine_id IS DISTINCT FROM draft_po.machine_id
                        AS machine_changed,
                    active_po.start_time IS DISTINCT FROM draft_po.start_time
                        AS start_changed,
                    active_po.end_time IS DISTINCT FROM draft_po.end_time
                        AS end_changed
                FROM plan_operations active_po
                JOIN plan_operations draft_po
                  ON draft_po.operation_id = active_po.operation_id
                JOIN order_operations oo
                  ON oo.id = draft_po.operation_id
                JOIN order_items oi
                  ON oi.id = oo.order_item_id
                LEFT JOIN orders o
                  ON o.id = oi.order_id
                LEFT JOIN products p
                  ON p.id = oi.product_id
                LEFT JOIN routing_operations ro
                  ON ro.id = oo.routing_operation_id
                LEFT JOIN machines active_machine
                  ON active_machine.id = active_po.machine_id
                LEFT JOIN machines draft_machine
                  ON draft_machine.id = draft_po.machine_id
                LEFT JOIN machine_product_rates active_rate
                  ON active_rate.product_id = oi.product_id
                 AND active_rate.machine_id = active_po.machine_id
                 AND active_rate.operation_type = oo.operation_type
                LEFT JOIN machine_product_rates draft_rate
                  ON draft_rate.product_id = oi.product_id
                 AND draft_rate.machine_id = draft_po.machine_id
                 AND draft_rate.operation_type = oo.operation_type
                WHERE active_po.plan_version_id = :active_plan_version_id
                  AND draft_po.plan_version_id = :draft_plan_version_id
                ORDER BY o.id, oi.id, oo.sequence_no, oo.id
                """
            ),
            {
                "active_plan_version_id": active_plan_version_id,
                "draft_plan_version_id": plan_version_id,
            },
        ).mappings().all()

        def to_int(value, default=0):
            return int(value) if value is not None else default

        all_operations = []
        items = []
        orders = {}
        machines = {}

        for row in rows:
            active_start = to_int(row["active_start"])
            draft_start = to_int(row["draft_start"])
            active_end = to_int(row["active_end"])
            draft_end = to_int(row["draft_end"])
            active_setup = to_int(row["active_setup_minutes"])
            draft_setup = to_int(row["draft_setup_minutes"])
            start_delta = draft_start - active_start
            end_delta = draft_end - active_end
            duration_active = active_end - active_start
            duration_draft = draft_end - draft_start
            duration_delta = duration_draft - duration_active
            machine_changed = bool(row["machine_changed"])
            start_changed = bool(row["start_changed"])
            end_changed = bool(row["end_changed"])
            is_changed = machine_changed or start_changed or end_changed
            order_id = row["order_id"]
            due_time = (
                row["order_item_due_time"]
                if row["order_item_due_time"] is not None
                else row["order_due_time"]
            )

            operation_data = {
                "operation_id": row["operation_id"],
                "order_id": order_id,
                "order_item_id": row["order_item_id"],
                "order_no": row["order_no"],
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "operation_type": row["operation_type"],
                "operation_name": row["operation_name"],
                "sequence_no": row["sequence_no"],
                "active_machine": row["active_machine"],
                "draft_machine": row["draft_machine"],
                "active_start": active_start,
                "draft_start": draft_start,
                "active_end": active_end,
                "draft_end": draft_end,
                "machine_changed": machine_changed,
                "start_changed": start_changed,
                "end_changed": end_changed,
                "start_delta": start_delta,
                "end_delta": end_delta,
                "duration_active": duration_active,
                "duration_draft": duration_draft,
                "duration_delta": duration_delta,
                "finish_later": end_delta > 0,
                "finish_earlier": end_delta < 0,
            }
            all_operations.append(operation_data)
            if is_changed:
                items.append(operation_data)

            order_data = orders.setdefault(
                order_id,
                {
                    "order_id": order_id,
                    "order_no": row["order_no"],
                    "product_id": row["product_id"],
                    "product_name": row["product_name"],
                    "due_time": to_int(due_time) if due_time is not None else None,
                    "active_finish": 0,
                    "draft_finish": 0,
                    "changed_operations": 0,
                    "machine_changed": 0,
                },
            )
            order_data["active_finish"] = max(order_data["active_finish"], active_end)
            order_data["draft_finish"] = max(order_data["draft_finish"], draft_end)
            if is_changed:
                order_data["changed_operations"] += 1
            if machine_changed:
                order_data["machine_changed"] += 1

            for prefix in ("active", "draft"):
                machine_id = row[f"{prefix}_machine"]
                if not machine_id:
                    continue
                machine_data = machines.setdefault(
                    machine_id,
                    {
                        "machine_id": machine_id,
                        "machine_name": row[f"{prefix}_machine_name"],
                        "machine_group_id": row[f"{prefix}_machine_group_id"],
                        "active_finish": 0,
                        "draft_finish": 0,
                        "active_busy_minutes": 0,
                        "draft_busy_minutes": 0,
                        "active_setup_minutes": 0,
                        "draft_setup_minutes": 0,
                        "changed_operations": 0,
                    },
                )
                if prefix == "active":
                    machine_data["active_finish"] = max(
                        machine_data["active_finish"], active_end
                    )
                    machine_data["active_busy_minutes"] += duration_active
                    machine_data["active_setup_minutes"] += active_setup
                else:
                    machine_data["draft_finish"] = max(
                        machine_data["draft_finish"], draft_end
                    )
                    machine_data["draft_busy_minutes"] += duration_draft
                    machine_data["draft_setup_minutes"] += draft_setup

            if is_changed:
                affected_machine_ids = {
                    row["active_machine"],
                    row["draft_machine"],
                }
                for machine_id in affected_machine_ids:
                    if machine_id in machines:
                        machines[machine_id]["changed_operations"] += 1

        plan_finish_active = max(
            (operation["active_end"] for operation in all_operations),
            default=0,
        )
        plan_finish_draft = max(
            (operation["draft_end"] for operation in all_operations),
            default=0,
        )

        order_impacts = []
        late_orders_active = 0
        late_orders_draft = 0
        total_lateness_active = 0
        total_lateness_draft = 0
        for order_data in orders.values():
            due_time = order_data["due_time"]
            active_lateness = (
                max(order_data["active_finish"] - due_time, 0)
                if due_time is not None
                else 0
            )
            draft_lateness = (
                max(order_data["draft_finish"] - due_time, 0)
                if due_time is not None
                else 0
            )
            total_lateness_active += active_lateness
            total_lateness_draft += draft_lateness
            if active_lateness > 0:
                late_orders_active += 1
            if draft_lateness > 0:
                late_orders_draft += 1

            finish_delta = order_data["draft_finish"] - order_data["active_finish"]
            if finish_delta != 0 or order_data["changed_operations"] > 0:
                order_impacts.append(
                    {
                        "order_id": order_data["order_id"],
                        "order_no": order_data["order_no"],
                        "product_id": order_data["product_id"],
                        "product_name": order_data["product_name"],
                        "active_finish": order_data["active_finish"],
                        "draft_finish": order_data["draft_finish"],
                        "finish_delta": finish_delta,
                        "due_time": due_time,
                        "active_lateness": active_lateness,
                        "draft_lateness": draft_lateness,
                        "lateness_delta": draft_lateness - active_lateness,
                        "changed_operations": order_data["changed_operations"],
                        "machine_changed": order_data["machine_changed"],
                    }
                )

        order_impacts.sort(
            key=lambda item: (-item["finish_delta"], str(item["order_no"] or ""))
        )

        machine_impacts = []
        for machine_data in machines.values():
            machine_data["finish_delta"] = (
                machine_data["draft_finish"] - machine_data["active_finish"]
            )
            machine_data["busy_delta"] = (
                machine_data["draft_busy_minutes"]
                - machine_data["active_busy_minutes"]
            )
            machine_data["setup_delta"] = (
                machine_data["draft_setup_minutes"]
                - machine_data["active_setup_minutes"]
            )
            machine_data["active_run_minutes"] = max(
                machine_data["active_busy_minutes"]
                - machine_data["active_setup_minutes"],
                0,
            )
            machine_data["draft_run_minutes"] = max(
                machine_data["draft_busy_minutes"]
                - machine_data["draft_setup_minutes"],
                0,
            )
            machine_data["run_delta"] = (
                machine_data["draft_run_minutes"]
                - machine_data["active_run_minutes"]
            )
            if (
                machine_data["finish_delta"] != 0
                or machine_data["busy_delta"] != 0
                or machine_data["changed_operations"] > 0
            ):
                machine_impacts.append(machine_data)

        machine_impacts.sort(
            key=lambda item: (-abs(item["finish_delta"]), str(item["machine_id"]))
        )

        end_deltas = [operation["end_delta"] for operation in all_operations]
        operations_finished_later = sum(1 for delta in end_deltas if delta > 0)
        operations_finished_earlier = sum(1 for delta in end_deltas if delta < 0)
        operations_unchanged_finish = sum(1 for delta in end_deltas if delta == 0)
        delays = [delta for delta in end_deltas if delta > 0]
        gains = [delta for delta in end_deltas if delta < 0]

        return {
            "active_plan_version": serialize_plan_version(active_version),
            "draft_plan_version": serialize_plan_version(draft_version),
            "summary": {
                "total_operations": len(all_operations),
                "changed_operations": len(items),
                "affected_orders": len(
                    {item["order_id"] for item in items if item["order_id"] is not None}
                ),
                "affected_order_items": len(
                    {
                        item["order_item_id"]
                        for item in items
                        if item["order_item_id"] is not None
                    }
                ),
                "machine_changed": sum(1 for item in items if item["machine_changed"]),
                "start_changed": sum(1 for item in items if item["start_changed"]),
                "end_changed": sum(1 for item in items if item["end_changed"]),
                "operations_finished_later": operations_finished_later,
                "operations_finished_earlier": operations_finished_earlier,
                "operations_unchanged_finish": operations_unchanged_finish,
                "max_operation_delay": max(delays, default=0),
                "max_operation_gain": min(gains, default=0),
                "plan_finish_active": plan_finish_active,
                "plan_finish_draft": plan_finish_draft,
                "plan_finish_delta": plan_finish_draft - plan_finish_active,
                "late_orders_active": late_orders_active,
                "late_orders_draft": late_orders_draft,
                "late_orders_delta": late_orders_draft - late_orders_active,
                "total_lateness_active": total_lateness_active,
                "total_lateness_draft": total_lateness_draft,
                "total_lateness_delta": (
                    total_lateness_draft - total_lateness_active
                ),
            },
            "order_impacts": order_impacts,
            "machine_impacts": machine_impacts,
            "items": items,
        }

    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось сравнить версии плана: {error}",
        )
    finally:
        db.close()


# ======================
# WEBSOCKET
# ======================
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await connect(ws)
    try:
        while True:
            await ws.receive_text()
    except Exception:
        await disconnect(ws)


# ======================
# GET FREEZE HORIZON
# ======================
@app.get("/settings/freeze_horizon")
def get_freeze_horizon():
    db = SessionLocal()
    try:
        minutes = get_freeze_horizon_minutes(db)
        return {"freeze_horizon_minutes": minutes}
    finally:
        db.close()


# ======================
# UPDATE FREEZE HORIZON
# ======================
@app.put("/settings/freeze_horizon")
async def update_freeze_horizon(
    payload: FreezeHorizonPayload,
    x_user_role: Optional[str] = Header(default=None),
):
    if x_user_role != PRODUCTION_MANAGER_ROLE:
        raise HTTPException(
            status_code=403,
            detail="Изменять горизонт заморозки может только начальник производства",
        )

    if payload.minutes < 120:
        raise HTTPException(
            status_code=400,
            detail="Горизонт заморозки не может быть меньше 120 минут",
        )

    db = SessionLocal()
    try:
        setting = (
            db.query(SystemSetting)
            .filter(SystemSetting.key == FREEZE_HORIZON_SETTING_KEY)
            .first()
        )

        if not setting:
            setting = SystemSetting(
                key=FREEZE_HORIZON_SETTING_KEY,
                value=str(payload.minutes),
                description="Горизонт заморозки плана в минутах от начала планового горизонта",
            )
            db.add(setting)
        else:
            setting.value = str(payload.minutes)

        db.commit()
        event = {
            "type": "settings_update",
            "data": {
                "freeze_horizon_minutes": payload.minutes
            },
        }       

        broadcast_sync(event)
        return {"status": "ok", "freeze_horizon_minutes": payload.minutes}

    finally:
        db.close()


# ======================
# GET OPERATIONS
# ======================
@app.get("/operations")
def get_operations(plan_version_id: Optional[int] = None):
    db = SessionLocal()
    try:
        requested_plan_version_id = get_requested_plan_version_id(db, plan_version_id)
        rows = db.execute(
            text(
                """
                WITH item_numbers AS (
                    SELECT
                        oi.id AS order_item_id,
                        oi.order_id,
                        row_number() OVER (
                            PARTITION BY oi.order_id
                            ORDER BY oi.priority, oi.id
                        ) AS item_no
                    FROM order_items oi
                ),
                operation_numbers AS (
                    SELECT
                        oo.id AS operation_id,
                        item_numbers.order_id,
                        o.order_no,
                        item_numbers.item_no,
                        p.name AS product_name,
                        row_number() OVER (
                            PARTITION BY oi.id
                            ORDER BY oo.sequence_no, oo.id
                        ) AS operation_no
                    FROM order_operations oo
                    JOIN order_items oi ON oi.id = oo.order_item_id
                    LEFT JOIN orders o ON o.id = oi.order_id
                    JOIN products p ON p.id = oi.product_id
                    JOIN item_numbers ON item_numbers.order_item_id = oi.id
                )
                SELECT
                    po.plan_version_id,
                    po.operation_id,
                    po.machine_id,
                    po.start_time,
                    po.end_time,
                    coalesce(m.name, po.machine_id) AS machine_name,
                    m.group_id AS machine_group_id,
                    oo.operation_type,
                    ro.operation_name,
                    onum.order_id,
                    onum.order_no,
                    onum.item_no,
                    onum.product_name,
                    onum.operation_no,
                    coalesce(mpr.setup_minutes, 0) AS setup_minutes,
                    mes.schedule_run_id AS mes_schedule_run_id,
                    mes.mes_schedule_status,
                    mes.mes_operation_status
                FROM plan_operations po
                LEFT JOIN machines m ON m.id = po.machine_id
                LEFT JOIN order_operations oo ON oo.id = po.operation_id
                LEFT JOIN order_items oi ON oi.id = oo.order_item_id
                LEFT JOIN routing_operations ro ON ro.id = oo.routing_operation_id
                LEFT JOIN operation_numbers onum ON onum.operation_id = po.operation_id
                LEFT JOIN machine_product_rates mpr
                  ON mpr.product_id = oi.product_id
                 AND mpr.machine_id = po.machine_id
                 AND mpr.operation_type = oo.operation_type
                LEFT JOIN LATERAL (
                    SELECT
                        mso.schedule_run_id,
                        mso.status AS mes_operation_status,
                        msr.status AS mes_schedule_status
                    FROM mes_schedule_operations mso
                    JOIN mes_schedule_runs msr
                      ON msr.id = mso.schedule_run_id
                    WHERE mso.source_plan_operation_id = po.id
                      AND msr.status IN ('created', 'released')
                    ORDER BY
                      CASE msr.status
                        WHEN 'released' THEN 1
                        WHEN 'created' THEN 2
                        ELSE 3
                      END,
                      msr.id DESC
                    LIMIT 1
                ) mes ON true
                WHERE po.plan_version_id = :plan_version_id
                ORDER BY po.start_time, po.operation_id
                """
            ),
            {"plan_version_id": requested_plan_version_id},
        ).mappings().all()

        return [
            {
                "id": row["operation_id"],
                "plan_version_id": row["plan_version_id"],
                "label": (
                    (
                        f"{row['order_no']} {row['product_name']}"
                        if row["order_no"]
                        else f"{int(row['order_id']):03d} {row['product_name']}"
                    )
                    if row["order_id"] is not None
                    and row["product_name"] is not None
                    else str(row["operation_id"])
                ),
                "order_id": row["order_id"],
                "machine": row["machine_id"],
                "machine_name": row["machine_name"],
                "machine_group_id": row["machine_group_id"],
                "operation_type": row["operation_type"],
                "operation_name": row["operation_name"],
                "product_name": row["product_name"],
                "setup_minutes": int(row["setup_minutes"] or 0),
                "start": int(row["start_time"]) if row["start_time"] is not None else 0,
                "end": int(row["end_time"]) if row["end_time"] is not None else 0,
                "mes_schedule_run_id": row["mes_schedule_run_id"],
                "mes_schedule_status": row["mes_schedule_status"],
                "mes_operation_status": row["mes_operation_status"],
            }
            for row in rows
        ]
    finally:
        db.close()


@app.get("/machines")
def get_machines():
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT
                    id,
                    group_id,
                    name,
                    status
                FROM machines
                WHERE status = 'active'
                ORDER BY group_id, id
                """
            )
        ).mappings().all()

        return [
            {
                "id": row["id"],
                "group_id": row["group_id"],
                "name": row["name"],
                "status": row["status"],
            }
            for row in rows
        ]
    finally:
        db.close()


# ======================
# UPDATE FROM GANTT (DRAG)
# ======================
@app.post("/update_op/{op_id}")
async def update_operation(
    op_id: int,
    payload: OperationUpdatePayload,
    plan_version_id: Optional[int] = None,
):
    db = SessionLocal()
    try:
        if plan_version_id is None:
            raise HTTPException(
                status_code=400,
                detail="Для изменения операции нужно явно указать версию плана",
            )

        requested_plan_version_id = get_requested_plan_version_id(db, plan_version_id)
        plan_version = get_plan_version_or_404(db, requested_plan_version_id)

        if plan_version.status != "draft":
            raise HTTPException(
                status_code=409,
                detail="Редактировать можно только черновую версию плана",
            )

        start = int(payload.start)
        machine_raw = payload.machine

        if start < 0:
            raise HTTPException(
                status_code=400,
                detail="Ошибка валидации: время начала не может быть отрицательным",
            )

        if machine_raw is None:
            raise HTTPException(
                status_code=400,
                detail="Ошибка валидации: станок не должен быть пустым",
            )

        if isinstance(machine_raw, str):
            machine_clean = machine_raw.strip()
            if machine_clean == "":
                raise HTTPException(
                    status_code=400,
                    detail="Ошибка валидации: станок не должен быть пустым",
                )
        else:
            machine_clean = machine_raw

        op = (
            db.query(PlanOperation)
            .filter(PlanOperation.operation_id == op_id)
            .filter(PlanOperation.plan_version_id == requested_plan_version_id)
            .first()
        )

        if not op:
            raise HTTPException(
                status_code=404,
                detail=f"Операция {op_id} не найдена",
            )

        freeze_horizon_minutes = get_freeze_horizon_minutes(db)
        current_start_time = int(op.start_time) if op.start_time is not None else 0

        if current_start_time < freeze_horizon_minutes:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Операция находится в замороженной зоне плана и не может быть перемещена",
                    "operation_id": op.operation_id,
                    "machine": op.machine_id,
                    "current_start": current_start_time,
                    "freeze_horizon_minutes": freeze_horizon_minutes,
                },
            )

        mes_lock = db.execute(
            text(
                """
                SELECT
                    mso.schedule_run_id,
                    msr.status AS schedule_status,
                    mso.status AS operation_status
                FROM mes_schedule_operations mso
                JOIN mes_schedule_runs msr
                  ON msr.id = mso.schedule_run_id
                WHERE mso.source_plan_operation_id = :plan_operation_id
                  AND msr.status = 'released'
                  AND mso.status = 'released'
                LIMIT 1
                """
            ),
            {"plan_operation_id": op.id},
        ).mappings().first()

        if mes_lock:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Операция уже выпущена в производственное задание "
                    f"№{mes_lock['schedule_run_id']} и не может быть изменена в APS"
                ),
            )

        if isinstance(op.machine_id, int):
            try:
                machine_value = int(machine_clean)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail="Ошибка валидации: станок должен быть числом",
                )
        else:
            machine_value = str(machine_clean)

        machine = db.execute(
            text("SELECT id, group_id FROM machines WHERE id = :machine_id"),
            {"machine_id": machine_value},
        ).mappings().first()

        if not machine:
            raise HTTPException(
                status_code=404,
                detail=f"Станок {machine_value} не найден",
            )

        operation_context = db.execute(
            text(
                """
                SELECT
                    oo.id AS order_operation_id,
                    oo.order_item_id,
                    oi.id AS existing_order_item_id,
                    oo.sequence_no,
                    oo.operation_type,
                    oo.quantity,
                    oo.routing_operation_id,
                    ro.id AS existing_routing_operation_id,
                    oi.product_id
                FROM order_operations oo
                LEFT JOIN order_items oi ON oi.id = oo.order_item_id
                LEFT JOIN routing_operations ro ON ro.id = oo.routing_operation_id
                WHERE oo.id = :operation_id
                """
            ),
            {"operation_id": op_id},
        ).mappings().first()

        if not operation_context:
            raise HTTPException(
                status_code=404,
                detail=f"Операция заказа {op_id} не найдена",
            )

        if operation_context["existing_order_item_id"] is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Позиция заказа {operation_context['order_item_id']} "
                    f"для операции {op_id} не найдена"
                ),
            )

        if operation_context["existing_routing_operation_id"] is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Операция маршрута {operation_context['routing_operation_id']} "
                    f"для операции заказа {op_id} не найдена"
                ),
            )

        if operation_context["product_id"] is None:
            raise HTTPException(
                status_code=400,
                detail=f"Для операции заказа {op_id} не задано изделие",
            )

        if operation_context["operation_type"] is None:
            raise HTTPException(
                status_code=400,
                detail=f"Для операции заказа {op_id} не задан тип операции",
            )

        allowed_group = db.execute(
            text(
                """
                SELECT 1
                FROM routing_operation_machine_groups
                WHERE routing_operation_id = :routing_operation_id
                  AND machine_group_id = :machine_group_id
                """
            ),
            {
                "routing_operation_id": operation_context["routing_operation_id"],
                "machine_group_id": machine["group_id"],
            },
        ).first()

        if not allowed_group:
            raise HTTPException(
                status_code=409,
                detail="Операция не может выполняться на выбранной группе оборудования",
            )

        rate = db.execute(
            text(
                """
                SELECT
                    units_per_minute,
                    coalesce(setup_minutes, 0) AS setup_minutes
                FROM machine_product_rates
                WHERE product_id = :product_id
                  AND machine_id = :machine_id
                  AND operation_type = :operation_type
                """
            ),
            {
                "product_id": operation_context["product_id"],
                "machine_id": machine_value,
                "operation_type": operation_context["operation_type"],
            },
        ).mappings().first()

        if not rate:
            raise HTTPException(
                status_code=409,
                detail="Для изделия нет нормы на выбранном станке",
            )

        units_per_minute = float(rate["units_per_minute"] or 0)
        if units_per_minute <= 0:
            raise HTTPException(
                status_code=409,
                detail="Для изделия задана некорректная норма на выбранном станке",
            )

        setup_minutes = int(rate["setup_minutes"] or 0)
        quantity = int(operation_context["quantity"])
        duration_minutes = ceil(quantity / units_per_minute) + setup_minutes
        calculated_end = start + duration_minutes

        if not is_inside_work_interval(db, start, calculated_end):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Операция должна полностью находиться в рабочем интервале смены",
                    "start_time": start,
                    "end_time": calculated_end,
                },
            )

        previous_operation = db.execute(
            text(
                """
                SELECT po.operation_id, po.end_time
                FROM order_operations oo
                JOIN plan_operations po ON po.operation_id = oo.id
                WHERE oo.order_item_id = :order_item_id
                  AND oo.sequence_no < :sequence_no
                  AND po.plan_version_id = :plan_version_id
                ORDER BY oo.sequence_no DESC, oo.id DESC
                LIMIT 1
                """
            ),
            {
                "order_item_id": operation_context["order_item_id"],
                "sequence_no": operation_context["sequence_no"],
                "plan_version_id": requested_plan_version_id,
            },
        ).mappings().first()

        if previous_operation:
            min_start = int(previous_operation["end_time"]) + INTER_OPERATION_GAP_MINUTES
        else:
            min_start = None

        if min_start is not None and start < min_start:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Нарушена последовательность переделов: операция должна "
                    f"начинаться не раньше чем через {INTER_OPERATION_GAP_MINUTES} мин. "
                    f"после окончания операции {previous_operation['operation_id']}"
                ),
            )

        old_machine_id = op.machine_id
        old_start_time = int(op.start_time) if op.start_time is not None else 0
        old_end_time = int(op.end_time) if op.end_time is not None else 0
        change_set_id = str(uuid4())

        op.start_time = start
        op.end_time = calculated_end
        op.machine_id = machine_value

        change = PlanChangeLog(
            change_set_id=change_set_id,
            plan_version_id=requested_plan_version_id,
            operation_id=op.operation_id,
            old_machine_id=old_machine_id,
            new_machine_id=machine_value,
            old_start_time=old_start_time,
            old_end_time=old_end_time,
            new_start_time=start,
            new_end_time=calculated_end,
            change_reason="manual_gantt_drag",
        )

        db.add(change)
        db.flush()

        try:
            changed_operations = repair_plan_after_manual_move(
                db=db,
                changed_operation_id=op.operation_id,
                freeze_horizon_minutes=freeze_horizon_minutes,
                change_set_id=change_set_id,
                plan_version_id=requested_plan_version_id,
            )
        except RepairSchedulerError as error:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Невозможно перепланировать план после ручного изменения: {error}",
            )

        validation_result = validate_plan_version(db, requested_plan_version_id)
        if not validation_result["is_valid"]:
            first_error = (
                validation_result["errors"][0]
                if validation_result.get("errors")
                else None
            )
            first_error_message = (
                first_error.get("message")
                if isinstance(first_error, dict)
                else None
            )
            message = "После переноса план содержит ошибки"
            if first_error_message:
                message = f"{message}: {first_error_message}"

            db.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "message": message,
                    "errors": validation_result["errors"],
                    "summary": validation_result["summary"],
                },
            )

        db.commit()
        db.refresh(op)

        operation_meta = db.execute(
            text(
                """
                WITH item_numbers AS (
                    SELECT
                        oi.id AS order_item_id,
                        oi.order_id,
                        row_number() OVER (
                            PARTITION BY oi.order_id
                            ORDER BY oi.priority, oi.id
                        ) AS item_no
                    FROM order_items oi
                ),
                operation_numbers AS (
                    SELECT
                        oo.id AS operation_id,
                        item_numbers.order_id,
                        o.order_no,
                        item_numbers.item_no,
                        p.name AS product_name,
                        row_number() OVER (
                            PARTITION BY oi.id
                            ORDER BY oo.sequence_no, oo.id
                    ) AS operation_no
                    FROM order_operations oo
                    JOIN order_items oi ON oi.id = oo.order_item_id
                    LEFT JOIN orders o ON o.id = oi.order_id
                    JOIN products p ON p.id = oi.product_id
                    JOIN item_numbers ON item_numbers.order_item_id = oi.id
                )
                SELECT
                    coalesce(m.name, :machine_id) AS machine_name,
                    m.group_id AS machine_group_id,
                    oo.operation_type,
                    ro.operation_name,
                    onum.order_id,
                    onum.order_no,
                    onum.item_no,
                    onum.product_name,
                    onum.operation_no,
                    coalesce(mpr.setup_minutes, 0) AS setup_minutes
                FROM order_operations oo
                JOIN order_items oi ON oi.id = oo.order_item_id
                LEFT JOIN routing_operations ro ON ro.id = oo.routing_operation_id
                LEFT JOIN machines m ON m.id = :machine_id
                LEFT JOIN operation_numbers onum ON onum.operation_id = oo.id
                LEFT JOIN machine_product_rates mpr
                  ON mpr.product_id = oi.product_id
                 AND mpr.machine_id = :machine_id
                 AND mpr.operation_type = oo.operation_type
                WHERE oo.id = :operation_id
                """
            ),
            {
                "operation_id": op.operation_id,
                "machine_id": op.machine_id,
            },
        ).mappings().first()

        event = {
            "type": "operation_update",
            "data": {
                "id": op.operation_id,
                "plan_version_id": requested_plan_version_id,
                "label": (
                    (
                        f"{operation_meta['order_no']} {operation_meta['product_name']}"
                        if operation_meta["order_no"]
                        else f"{int(operation_meta['order_id']):03d} {operation_meta['product_name']}"
                    )
                    if operation_meta
                    and operation_meta["order_id"] is not None
                    and operation_meta["product_name"] is not None
                    else str(op.operation_id)
                ),
                "order_id": (
                    operation_meta["order_id"] if operation_meta else None
                ),
                "machine": op.machine_id,
                "machine_name": (
                    operation_meta["machine_name"] if operation_meta else op.machine_id
                ),
                "machine_group_id": (
                    operation_meta["machine_group_id"] if operation_meta else None
                ),
                "operation_type": (
                    operation_meta["operation_type"] if operation_meta else None
                ),
                "operation_name": (
                    operation_meta["operation_name"] if operation_meta else None
                ),
                "product_name": (
                    operation_meta["product_name"] if operation_meta else None
                ),
                "setup_minutes": (
                    int(operation_meta["setup_minutes"] or 0)
                    if operation_meta
                    else 0
                ),
                "start": int(op.start_time) if op.start_time is not None else 0,
                "end": int(op.end_time) if op.end_time is not None else 0,
            },
        }

        operation_data = event["data"]
        bulk_operations = [operation_data]
        for changed_operation in changed_operations:
            if changed_operation["id"] != op.operation_id:
                bulk_operations.append(changed_operation)

        event = {
            "type": "plan_operations_updated",
            "data": bulk_operations,
        }

        broadcast_sync(event)

        return {
            "status": "ok",
            "operation": operation_data,
            "changed_operations": bulk_operations,
            "duration_minutes": duration_minutes,
            "change_set_id": change_set_id,
        }

    finally:
        db.close()

# ======================
# GET PLAN CHANGE LOG
# ======================
@app.post("/plan_change_log/change_set/{change_set_id}/rollback")
async def rollback_change_set(
    change_set_id: str,
    plan_version_id: Optional[int] = None,
):
    db = SessionLocal()
    try:
        if plan_version_id is None:
            raise HTTPException(
                status_code=400,
                detail="Для отката группы изменений нужно явно указать версию плана",
            )

        requested_plan_version_id = get_requested_plan_version_id(db, plan_version_id)
        plan_version = get_plan_version_or_404(db, requested_plan_version_id)

        if plan_version.status != "draft":
            raise HTTPException(
                status_code=409,
                detail="Откат группы изменений можно выполнять только в черновой версии плана",
            )

        rows = (
            db.query(PlanChangeLog)
            .filter(PlanChangeLog.change_set_id == change_set_id)
            .filter(PlanChangeLog.plan_version_id == requested_plan_version_id)
            .order_by(PlanChangeLog.id.desc())
            .all()
        )

        if not rows:
            raise HTTPException(status_code=404, detail="Группа изменений не найдена")

        rollback_reasons = {"manual_gantt_drag", "repair_scheduler_shift"}
        rollback_rows = [
            row
            for row in rows
            if row.change_reason in rollback_reasons and not row.is_rolled_back
        ]

        if not rollback_rows:
            raise HTTPException(
                status_code=409,
                detail="Группа изменений уже была откатана",
            )

        rollback_change_set_id = str(uuid4())
        updated_by_operation_id = {}

        for row in rollback_rows:
            op = (
                db.query(PlanOperation)
                .filter(PlanOperation.operation_id == row.operation_id)
                .filter(PlanOperation.plan_version_id == requested_plan_version_id)
                .first()
            )

            if not op:
                raise HTTPException(
                    status_code=404,
                    detail="Плановая операция не найдена",
                )

            current_machine_id = op.machine_id
            current_start_time = int(op.start_time) if op.start_time is not None else 0
            current_end_time = int(op.end_time) if op.end_time is not None else 0

            op.machine_id = row.old_machine_id
            op.start_time = int(row.old_start_time)
            op.end_time = int(row.old_end_time)

            row.is_rolled_back = True
            row.rollback_reason = "manual_change_set_rollback"

            db.execute(
                text("UPDATE plan_change_log SET rollback_at = now() WHERE id = :id"),
                {"id": row.id},
            )

            rollback_log = PlanChangeLog(
                change_set_id=rollback_change_set_id,
                plan_version_id=requested_plan_version_id,
                operation_id=row.operation_id,
                old_machine_id=current_machine_id,
                new_machine_id=row.old_machine_id,
                old_start_time=current_start_time,
                old_end_time=current_end_time,
                new_start_time=int(row.old_start_time),
                new_end_time=int(row.old_end_time),
                change_reason="manual_rollback",
                is_rolled_back=True,
                rollback_reason="change_set_rollback_event",
            )
            db.add(rollback_log)

            updated_by_operation_id[op.operation_id] = {
                "id": op.operation_id,
                "plan_version_id": requested_plan_version_id,
                "machine": op.machine_id,
                "start": int(op.start_time),
                "end": int(op.end_time),
            }

        db.commit()

        updated_operations = list(updated_by_operation_id.values())
        event = {
            "type": "plan_operations_updated",
            "data": updated_operations,
        }
        broadcast_sync(event)

        return {
            "status": "ok",
            "rolled_back_change_set_id": change_set_id,
            "rollback_change_set_id": rollback_change_set_id,
            "updated_operations": updated_operations,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось откатить группу изменений: {error}",
        )
    finally:
        db.close()


@app.get("/plan_change_log")
def get_plan_change_log(
    limit: int = 20,
    operation_id: Optional[int] = None,
    machine: Optional[str] = None,
    change_reason: Optional[str] = None,
    rolled_back: Optional[bool] = None,
    plan_version_id: Optional[int] = None,
):
    db = SessionLocal()
    try:
        requested_plan_version_id = get_requested_plan_version_id(db, plan_version_id)
        conditions = ["pcl.plan_version_id = :plan_version_id"]
        params = {
            "plan_version_id": requested_plan_version_id,
            "limit": limit,
        }
        if operation_id is not None:
            conditions.append("pcl.operation_id = :operation_id")
            params["operation_id"] = operation_id

        if machine:
            machine_clean = machine.strip()
            conditions.append(
                "(pcl.old_machine_id = :machine OR pcl.new_machine_id = :machine)"
            )
            params["machine"] = machine_clean

        if change_reason:
            conditions.append("pcl.change_reason = :change_reason")
            params["change_reason"] = change_reason

        if rolled_back is not None:
            conditions.append("coalesce(pcl.is_rolled_back, false) = :rolled_back")
            params["rolled_back"] = rolled_back

        where_sql = " AND ".join(conditions)
        rows = db.execute(
            text(
                f"""
                SELECT
                    pcl.id,
                    pcl.change_set_id,
                    pcl.plan_version_id,
                    pcl.operation_id,
                    pcl.old_machine_id,
                    pcl.new_machine_id,
                    pcl.old_start_time,
                    pcl.old_end_time,
                    pcl.new_start_time,
                    pcl.new_end_time,
                    pcl.change_reason,
                    pcl.created_at,
                    coalesce(pcl.is_rolled_back, false) AS is_rolled_back,
                    pcl.rollback_at,
                    pcl.rollback_reason,
                    oi.order_id,
                    o.order_no,
                    oi.product_id,
                    p.name AS product_name,
                    oo.operation_type,
                    ro.operation_name,
                    oo.sequence_no
                FROM plan_change_log pcl
                LEFT JOIN order_operations oo ON oo.id = pcl.operation_id
                LEFT JOIN order_items oi ON oi.id = oo.order_item_id
                LEFT JOIN orders o ON o.id = oi.order_id
                LEFT JOIN products p ON p.id = oi.product_id
                LEFT JOIN routing_operations ro ON ro.id = oo.routing_operation_id
                WHERE {where_sql}
                ORDER BY pcl.id DESC
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()

        return [
            {
                "id": row["id"],
                "change_set_id": row["change_set_id"],
                "plan_version_id": row["plan_version_id"],
                "operation_id": row["operation_id"],
                "order_id": row["order_id"],
                "order_no": row["order_no"],
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "operation_type": row["operation_type"],
                "operation_name": row["operation_name"],
                "sequence_no": row["sequence_no"],
                "operation_label": (
                    f"{row['order_no']} — {row['product_name']} — "
                    f"{row['sequence_no']} {row['operation_name']}"
                    if row["order_no"]
                    and row["product_name"]
                    and row["sequence_no"] is not None
                    and row["operation_name"]
                    else str(row["operation_id"])
                ),
                "old_machine_id": row["old_machine_id"],
                "new_machine_id": row["new_machine_id"],
                "old_start_time": row["old_start_time"],
                "old_end_time": row["old_end_time"],
                "new_start_time": row["new_start_time"],
                "new_end_time": row["new_end_time"],
                "change_reason": row["change_reason"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "is_rolled_back": bool(row["is_rolled_back"]),
                "rollback_at": row["rollback_at"].isoformat() if row["rollback_at"] else None,
                "rollback_reason": row["rollback_reason"],
            }
            for row in rows
        ]

    finally:
        db.close()
