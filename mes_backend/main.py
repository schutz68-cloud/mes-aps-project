from fastapi import FastAPI, WebSocket, HTTPException, Header
from pydantic import BaseModel
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from db import SessionLocal
from models import PlanOperation, PlanChangeLog, SystemSetting
from websocket import connect, disconnect, broadcast_sync
from sqlalchemy import text


app = FastAPI(title="MES APS Backend")


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
        ops = db.query(PlanOperation).all()
        return [
            {
                "id": op.operation_id,
                "machine": op.machine_id,
                "start": int(op.start_time) if op.start_time is not None else 0,
                "end": int(op.end_time) if op.end_time is not None else 0,
            }
            for op in ops
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

        event = {
            "type": "operation_update",
            "data": {
                "id": op.operation_id,
                "machine": op.machine_id,
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

        event = {
            "type": "operation_update",
            "data": {
                "id": op.operation_id,
                "machine": op.machine_id,
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
def get_plan_change_log(limit: int = 20):
    db = SessionLocal()
    try:
        rows = (
            db.query(PlanChangeLog).order_by(PlanChangeLog.id.desc()).limit(limit).all()
        )

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
