from fastapi import FastAPI, WebSocket, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from db import SessionLocal, init_db_schema
from models import PlanOperation, PlanChangeLog, SystemSetting
from websocket import connect, disconnect, broadcast_sync
from sqlalchemy import text


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
                WITH operation_numbers AS (
                    SELECT
                        oo.id AS operation_id,
                        oi.order_id,
                        row_number() OVER (
                            PARTITION BY oi.id
                            ORDER BY oo.sequence_no, oo.id
                        ) AS operation_no
                    FROM order_operations oo
                    JOIN order_items oi ON oi.id = oo.order_item_id
                )
                SELECT
                    po.operation_id,
                    po.machine_id,
                    po.start_time,
                    po.end_time,
                    coalesce(m.name, po.machine_id) AS machine_name,
                    oo.operation_type,
                    ro.operation_name,
                    onum.order_id,
                    onum.operation_no
                FROM plan_operations po
                LEFT JOIN machines m ON m.id = po.machine_id
                LEFT JOIN order_operations oo ON oo.id = po.operation_id
                LEFT JOIN routing_operations ro ON ro.id = oo.routing_operation_id
                LEFT JOIN operation_numbers onum ON onum.operation_id = po.operation_id
                ORDER BY po.start_time, po.operation_id
                """
            )
        ).mappings().all()

        return [
            {
                "id": row["operation_id"],
                "label": (
                    f"{int(row['order_id']):03d}.{int(row['operation_no']):02d}"
                    if row["order_id"] is not None and row["operation_no"] is not None
                    else str(row["operation_id"])
                ),
                "machine": row["machine_id"],
                "machine_name": row["machine_name"],
                "operation_type": row["operation_type"],
                "operation_name": row["operation_name"],
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
        end = int(payload.end)
        machine_raw = payload.machine

        if start >= end:
            raise HTTPException(
                status_code=400,
                detail="Ошибка валидации: время начала должно быть меньше времени окончания",
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

        machine_exists = db.execute(
            text("SELECT 1 FROM machines WHERE id = :machine_id"),
            {"machine_id": machine_value},
        ).first()

        if not machine_exists:
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

        if operation_context:
            rate_exists = db.execute(
                text(
                    """
                    SELECT 1
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
            ).first()

            if not rate_exists:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Операция недопустима на выбранном станке: "
                        "нет нормы для изделия, станка и типа операции"
                    ),
                )

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

            next_operation = db.execute(
                text(
                    """
                    SELECT po.operation_id, po.start_time
                    FROM order_operations oo
                    JOIN plan_operations po ON po.operation_id = oo.id
                    WHERE oo.order_item_id = :order_item_id
                      AND oo.sequence_no > :sequence_no
                    ORDER BY oo.sequence_no ASC, oo.id ASC
                    LIMIT 1
                    """
                ),
                {
                    "order_item_id": operation_context["order_item_id"],
                    "sequence_no": operation_context["sequence_no"],
                },
            ).mappings().first()

            if next_operation:
                max_end = int(next_operation["start_time"]) - INTER_OPERATION_GAP_MINUTES
            else:
                max_end = None

            if max_end is not None and end > max_end:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Нарушена последовательность переделов: операция должна "
                        f"заканчиваться минимум за {INTER_OPERATION_GAP_MINUTES} мин. "
                        f"до начала операции {next_operation['operation_id']}"
                    ),
                )

        overlapping_op = (
            db.query(PlanOperation)
            .filter(PlanOperation.operation_id != op_id)
            .filter(PlanOperation.machine_id == machine_value)
            .filter(PlanOperation.start_time < end)
            .filter(PlanOperation.end_time > start)
            .first()
        )

        if overlapping_op:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Операция пересекается с другой операцией на этом же станке",
                    "machine": machine_value,
                    "current_operation_id": op_id,
                    "overlapping_operation_id": overlapping_op.operation_id,
                    "requested_start": start,
                    "requested_end": end,
                    "overlapping_start": (
                        int(overlapping_op.start_time)
                        if overlapping_op.start_time is not None
                        else 0
                    ),
                    "overlapping_end": (
                        int(overlapping_op.end_time)
                        if overlapping_op.end_time is not None
                        else 0
                    ),
                },
            )

        old_plan_version_id = op.plan_version_id
        old_machine_id = op.machine_id
        old_start_time = int(op.start_time) if op.start_time is not None else 0
        old_end_time = int(op.end_time) if op.end_time is not None else 0

        op.start_time = start
        op.end_time = end
        op.machine_id = machine_value

        change = PlanChangeLog(
            plan_version_id=old_plan_version_id,
            operation_id=op.operation_id,
            old_machine_id=old_machine_id,
            new_machine_id=machine_value,
            old_start_time=old_start_time,
            old_end_time=old_end_time,
            new_start_time=start,
            new_end_time=end,
            change_reason="manual_gantt_drag",
        )

        db.add(change)
        db.commit()
        db.refresh(op)

        operation_meta = db.execute(
            text(
                """
                WITH operation_numbers AS (
                    SELECT
                        oo.id AS operation_id,
                        oi.order_id,
                        row_number() OVER (
                            PARTITION BY oi.id
                            ORDER BY oo.sequence_no, oo.id
                        ) AS operation_no
                    FROM order_operations oo
                    JOIN order_items oi ON oi.id = oo.order_item_id
                )
                SELECT
                    coalesce(m.name, :machine_id) AS machine_name,
                    oo.operation_type,
                    ro.operation_name,
                    onum.order_id,
                    onum.operation_no
                FROM order_operations oo
                JOIN order_items oi ON oi.id = oo.order_item_id
                LEFT JOIN routing_operations ro ON ro.id = oo.routing_operation_id
                LEFT JOIN machines m ON m.id = :machine_id
                LEFT JOIN operation_numbers onum ON onum.operation_id = oo.id
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
                    f"{int(operation_meta['order_id']):03d}.{int(operation_meta['operation_no']):02d}"
                    if operation_meta
                    and operation_meta["order_id"] is not None
                    and operation_meta["operation_no"] is not None
                    else str(op.operation_id)
                ),
                "machine": op.machine_id,
                "machine_name": (
                    operation_meta["machine_name"] if operation_meta else op.machine_id
                ),
                "operation_type": (
                    operation_meta["operation_type"] if operation_meta else None
                ),
                "operation_name": (
                    operation_meta["operation_name"] if operation_meta else None
                ),
                "start": int(op.start_time) if op.start_time is not None else 0,
                "end": int(op.end_time) if op.end_time is not None else 0,
            },
        }

        broadcast_sync(event)

        return {"status": "ok"}

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
                WITH operation_numbers AS (
                    SELECT
                        oo.id AS operation_id,
                        oi.order_id,
                        row_number() OVER (
                            PARTITION BY oi.id
                            ORDER BY oo.sequence_no, oo.id
                        ) AS operation_no
                    FROM order_operations oo
                    JOIN order_items oi ON oi.id = oo.order_item_id
                )
                SELECT
                    coalesce(m.name, :machine_id) AS machine_name,
                    oo.operation_type,
                    ro.operation_name,
                    onum.order_id,
                    onum.operation_no
                FROM order_operations oo
                JOIN order_items oi ON oi.id = oo.order_item_id
                LEFT JOIN routing_operations ro ON ro.id = oo.routing_operation_id
                LEFT JOIN machines m ON m.id = :machine_id
                LEFT JOIN operation_numbers onum ON onum.operation_id = oo.id
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
                    f"{int(operation_meta['order_id']):03d}.{int(operation_meta['operation_no']):02d}"
                    if operation_meta
                    and operation_meta["order_id"] is not None
                    and operation_meta["operation_no"] is not None
                    else str(op.operation_id)
                ),
                "machine": op.machine_id,
                "machine_name": (
                    operation_meta["machine_name"] if operation_meta else op.machine_id
                ),
                "operation_type": (
                    operation_meta["operation_type"] if operation_meta else None
                ),
                "operation_name": (
                    operation_meta["operation_name"] if operation_meta else None
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
