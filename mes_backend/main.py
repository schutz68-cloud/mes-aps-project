from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from db import SessionLocal
from models import PlanOperation, PlanChangeLog
from websocket import connect, disconnect, broadcast_sync
from sqlalchemy import text

app = FastAPI(title="MES APS Backend")
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
            # Держим соединение живым
            await ws.receive_text()
    except Exception:
        await disconnect(ws)


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
# Валидация: start < end, machine не пустой
# Поддержка переноса между станками
# ======================
@app.post("/update_op/{op_id}")
async def update_operation(op_id: int, request: Request):
    db = SessionLocal()
    try:
        payload = await request.json()

        # 1) Проверка обязательных полей
        required_fields = ("start", "end", "machine")
        missing = [f for f in required_fields if f not in payload]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required field(s): {', '.join(missing)}",
            )

        # 2) Валидация времени
        try:
            start = int(payload["start"])
            end = int(payload["end"])
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="'start' and 'end' must be integers"
            )

        if start >= end:
            raise HTTPException(
                status_code=400, detail="Validation error: start must be < end"
            )

        # 3) Валидация machine
        machine_raw = payload["machine"]
        if machine_raw is None:
            raise HTTPException(
                status_code=400, detail="Validation error: machine must not be empty"
            )

        if isinstance(machine_raw, str):
            machine_clean = machine_raw.strip()
            if machine_clean == "":
                raise HTTPException(
                    status_code=400,
                    detail="Validation error: machine must not be empty",
                )
        else:
            machine_clean = machine_raw

        # 4) Поиск операции
        op = db.query(PlanOperation).filter(PlanOperation.operation_id == op_id).first()

        if not op:
            raise HTTPException(status_code=404, detail=f"Operation {op_id} not found")

        # 5) Сохраняем старые значения ДО изменения
        old_plan_version_id = op.plan_version_id
        old_machine_id = op.machine_id
        old_start_time = int(op.start_time) if op.start_time is not None else 0
        old_end_time = int(op.end_time) if op.end_time is not None else 0

        # 6) Безопасное приведение machine к типу поля в БД
        if isinstance(op.machine_id, int):
            try:
                machine_value = int(machine_clean)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail="Validation error: machine must be integer"
                )
        else:
            machine_value = str(machine_clean)

        # 7) Обновляем операцию
        op.start_time = start
        op.end_time = end
        op.machine_id = machine_value

        # 8) Пишем историю изменений
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

        # 9) WebSocket событие
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
                detail="No available changes found for rollback",
            )

        print(
            "↩️ LAST CHANGE FOR ROLLBACK:",
            {
                "change_id": last_change.id,
                "operation_id": last_change.operation_id,
                "old_machine_id": last_change.old_machine_id,
                "old_start_time": last_change.old_start_time,
                "old_end_time": last_change.old_end_time,
                "new_machine_id": last_change.new_machine_id,
                "new_start_time": last_change.new_start_time,
                "new_end_time": last_change.new_end_time,
                "is_rolled_back": last_change.is_rolled_back,
            },
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
                detail=f"Operation {last_change.operation_id} not found",
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

        print("✅ ROLLBACK APPLIED:", event["data"])
        print(
            "🧾 ROLLBACK EVENT LOGGED:",
            {
                "rollback_log_id": rollback_log.id,
                "operation_id": rollback_log.operation_id,
                "change_reason": rollback_log.change_reason,
            },
        )

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
