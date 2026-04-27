from websocket import broadcast_sync
from ortools.sat.python import cp_model
import time

# Минимальный пример CP-SAT пересчёта
def recalc_plan(operations):
    model = cp_model.CpModel()
    start_vars = {}
    for op in operations:
        start_vars[op["id"]] = model.NewIntVar(op["start"], op["end"], f'start_{op["id"]}')
    # Ограничения пример (неполный, можно расширить)
    # solver = cp_model.CpSolver()
    # solver.Solve(model)
    # здесь просто возвращаем операции без изменений
    return operations

def plan_operations():
    operations = [
        {"id": 1, "machine": "W1", "start": 0, "end": 60},
        {"id": 2, "machine": "W1", "start": 70, "end": 120},
        {"id": 3, "machine": "W2", "start": 0, "end": 90},
    ]

    for op in operations:
        time.sleep(1)
        broadcast_sync({"type": "operation_update", "data": op})