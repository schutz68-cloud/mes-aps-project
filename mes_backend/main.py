from fastapi import FastAPI, WebSocket, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
from uuid import uuid4
from fastapi.middleware.cors import CORSMiddleware
from db import SessionLocal, init_db_schema
from models import PlanOperation, PlanChangeLog, SystemSetting
from repair_scheduler import RepairSchedulerError, repair_plan_after_manual_move
from websocket import connect, disconnect, broadcast_sync
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


# ======================
# APS SETTINGS
# ======================
DEFAULT_FREEZE_HORIZON_MINUTES = 200
FREEZE_HORIZON_SETTING_KEY = "freeze_horizon_minutes"
PRODUCTION_MANAGER_ROLE = "production_manager"
INTER_OPERATION_GAP_MINUTES = 15


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
def get_operations():
    db = SessionLocal()
    try:
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
                    coalesce(mpr.setup_minutes, 0) AS setup_minutes
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
                ORDER BY po.start_time, po.operation_id
                """
            )
        ).mappings().all()

        return [
            {
                "id": row["operation_id"],
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
            }
            for row in rows
        ]
    finally:
        db.close()


# ======================
# UPDATE FROM GANTT (DRAG)
# ======================
@app.post("/update_op/{op_id}")
async def update_operation(op_id: int, payload: OperationUpdatePayload):
    db = SessionLocal()
    try:
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

        op = db.query(PlanOperation).filter(PlanOperation.operation_id == op_id).first()

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

        previous_operation = db.execute(
            text(
                """
                SELECT po.operation_id, po.end_time
                FROM order_operations oo
                JOIN plan_operations po ON po.operation_id = oo.id
                WHERE oo.order_item_id = :order_item_id
                  AND oo.sequence_no < :sequence_no
                ORDER BY oo.sequence_no DESC, oo.id DESC
                LIMIT 1
                """
            ),
            {
                "order_item_id": operation_context["order_item_id"],
                "sequence_no": operation_context["sequence_no"],
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

        old_plan_version_id = op.plan_version_id
        old_machine_id = op.machine_id
        old_start_time = int(op.start_time) if op.start_time is not None else 0
        old_end_time = int(op.end_time) if op.end_time is not None else 0
        change_set_id = str(uuid4())

        op.start_time = start
        op.end_time = calculated_end
        op.machine_id = machine_value

        change = PlanChangeLog(
            change_set_id=change_set_id,
            plan_version_id=old_plan_version_id,
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
            )
        except RepairSchedulerError as error:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Невозможно перепланировать план после ручного изменения: {error}",
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
# ROLLBACK LAST CHANGE
# ======================
@app.post("/rollback_last_change")
async def rollback_last_change():
    print("↩️ ROLLBACK ENDPOINT CALLED")

    db = SessionLocal()
    try:
        last_change = (
            db.query(PlanChangeLog)
            .filter(
                (PlanChangeLog.is_rolled_back == False)
                | (PlanChangeLog.is_rolled_back.is_(None))
            )
            .filter(PlanChangeLog.change_reason == "manual_gantt_drag")
            .order_by(PlanChangeLog.id.desc())
            .first()
        )

        if not last_change:
            print("❌ NO AVAILABLE CHANGES FOR ROLLBACK")
            raise HTTPException(
                status_code=404,
                detail="Нет доступных изменений для отката",
            )

        op = (
            db.query(PlanOperation)
            .filter(PlanOperation.operation_id == last_change.operation_id)
            .first()
        )

        if not op:
            print("❌ OPERATION NOT FOUND:", last_change.operation_id)
            raise HTTPException(
                status_code=404,
                detail=f"Операция {last_change.operation_id} не найдена",
            )

        current_machine_id = op.machine_id
        current_start_time = int(op.start_time) if op.start_time is not None else 0
        current_end_time = int(op.end_time) if op.end_time is not None else 0

        op.machine_id = last_change.old_machine_id
        op.start_time = int(last_change.old_start_time)
        op.end_time = int(last_change.old_end_time)

        last_change.is_rolled_back = True
        last_change.rollback_reason = "manual_rollback"

        db.execute(
            text("UPDATE plan_change_log SET rollback_at = now() WHERE id = :id"),
            {"id": last_change.id},
        )

        rollback_log = PlanChangeLog(
            plan_version_id=last_change.plan_version_id,
            operation_id=last_change.operation_id,
            old_machine_id=current_machine_id,
            new_machine_id=last_change.old_machine_id,
            old_start_time=current_start_time,
            old_end_time=current_end_time,
            new_start_time=int(last_change.old_start_time),
            new_end_time=int(last_change.old_end_time),
            change_reason="manual_rollback",
            is_rolled_back=True,
            rollback_reason="rollback_event_record",
        )

        db.add(rollback_log)

        db.commit()
        db.refresh(op)
        db.refresh(last_change)
        db.refresh(rollback_log)

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

        broadcast_sync(event)

        return {
            "status": "ok",
            "rolled_back_change_id": last_change.id,
            "rollback_log_id": rollback_log.id,
            "operation": event["data"],
        }

    finally:
        db.close()


# ======================
# GET PLAN CHANGE LOG
# ======================
@app.post("/plan_change_log/change_set/{change_set_id}/rollback")
async def rollback_change_set(change_set_id: str):
    db = SessionLocal()
    try:
        rows = (
            db.query(PlanChangeLog)
            .filter(PlanChangeLog.change_set_id == change_set_id)
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
                plan_version_id=row.plan_version_id,
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
):
    db = SessionLocal()
    try:
        query = db.query(PlanChangeLog)

        if operation_id is not None:
            query = query.filter(PlanChangeLog.operation_id == operation_id)

        if machine:
            machine_clean = machine.strip()
            query = query.filter(
                (PlanChangeLog.old_machine_id == machine_clean)
                | (PlanChangeLog.new_machine_id == machine_clean)
            )

        if change_reason:
            query = query.filter(PlanChangeLog.change_reason == change_reason)

        if rolled_back is not None:
            query = query.filter(PlanChangeLog.is_rolled_back == rolled_back)

        rows = query.order_by(PlanChangeLog.id.desc()).limit(limit).all()

        return [
            {
                "id": row.id,
                "change_set_id": row.change_set_id,
                "plan_version_id": row.plan_version_id,
                "operation_id": row.operation_id,
                "old_machine_id": row.old_machine_id,
                "new_machine_id": row.new_machine_id,
                "old_start_time": row.old_start_time,
                "old_end_time": row.old_end_time,
                "new_start_time": row.new_start_time,
                "new_end_time": row.new_end_time,
                "change_reason": row.change_reason,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "is_rolled_back": bool(row.is_rolled_back),
                "rollback_at": row.rollback_at.isoformat() if row.rollback_at else None,
                "rollback_reason": row.rollback_reason,
            }
            for row in rows
        ]

    finally:
        db.close()
