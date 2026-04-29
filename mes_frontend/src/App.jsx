import { useCallback, useEffect, useRef, useState } from "react";
import Gantt from "./Gantt";

const MOVE_DEBOUNCE_MS = 350;
const HISTORY_LIMIT = 50;
const DEFAULT_HISTORY_FILTERS = {
  operationId: "",
  machine: "",
  changeReason: "",
  rollbackStatus: "",
};

const OPERATION_NAMES = {
  COILING: "Навивка",
  BENDING: "Загиб",
  FACING: "Торцовка",
  HEAT: "Термичка",
  COATING: "Покрытие",
};

// Временная роль до полноценной авторизации.
// production_manager может изменять frozen zone.
// dispatcher только видит настройку.
const CURRENT_USER_ROLE = "production_manager";

function App() {
  const [ops, setOps] = useState([]);
  const [changeLog, setChangeLog] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [freezeHorizonMinutes, setFreezeHorizonMinutes] = useState(0);
  const [freezeInput, setFreezeInput] = useState("");
  const [historyFilters, setHistoryFilters] = useState(DEFAULT_HISTORY_FILTERS);
  const [operationFilter, setOperationFilter] = useState("");

  const moveDebounceRef = useRef(new Map());

  const canEditFreezeZone = CURRENT_USER_ROLE === "production_manager";
  const operationGroups = Array.from(
    new Set(ops.map((op) => op.operation_type).filter(Boolean))
  ).sort();
  const filteredOps = operationFilter
    ? ops.filter((op) => op.operation_type === operationFilter)
    : ops;

  const loadOperations = useCallback(() => {
    fetch("http://127.0.0.1:8000/operations")
      .then((res) => res.json())
      .then((data) => {
        setOps(Array.isArray(data) ? data : []);
      })
      .catch(() => {});
  }, []);

  const loadChangeLog = useCallback(() => {
    const params = new URLSearchParams({ limit: String(HISTORY_LIMIT) });

    if (historyFilters.operationId.trim()) {
      params.set("operation_id", historyFilters.operationId.trim());
    }
    if (historyFilters.machine.trim()) {
      params.set("machine", historyFilters.machine.trim());
    }
    if (historyFilters.changeReason) {
      params.set("change_reason", historyFilters.changeReason);
    }
    if (historyFilters.rollbackStatus) {
      params.set("rolled_back", historyFilters.rollbackStatus);
    }

    fetch(`http://127.0.0.1:8000/plan_change_log?${params.toString()}`)
      .then((res) => res.json())
      .then((data) => {
        setChangeLog(Array.isArray(data) ? data : []);
      })
      .catch(() => {});
  }, [historyFilters]);

  const loadFreezeHorizon = useCallback(() => {
    fetch("http://127.0.0.1:8000/settings/freeze_horizon")
      .then((res) => res.json())
      .then((data) => {
        const minutes = Number(data.freeze_horizon_minutes ?? 0);
        setFreezeHorizonMinutes(minutes);
        setFreezeInput(String(minutes));
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadOperations();
    loadChangeLog();
    loadFreezeHorizon();
  }, [loadOperations, loadChangeLog, loadFreezeHorizon]);

  useEffect(() => {
    let disposed = false;
    const ws = new WebSocket("ws://127.0.0.1:8000/ws");

    ws.onmessage = (event) => {
      if (disposed) return;

      const msg = JSON.parse(event.data);

      if (msg.type === "operation_update") {
        setOps((prev) => {
          const idx = prev.findIndex((op) => op.id === msg.data.id);
          if (idx === -1) return [...prev, msg.data];

          const next = [...prev];
          next[idx] = { ...next[idx], ...msg.data };
          return next;
        });

        loadChangeLog();
      }
      if (msg.type === "plan_operations_updated") {
        const updates = Array.isArray(msg.data) ? msg.data : [];

        setOps((prev) => {
          const byId = new Map(prev.map((op) => [op.id, op]));

          for (const update of updates) {
            const existing = byId.get(update.id);
            byId.set(update.id, existing ? { ...existing, ...update } : update);
          }

          return Array.from(byId.values());
        });

        loadChangeLog();
      }
      if (msg.type === "settings_update") {
        const minutes = Number(msg.data.freeze_horizon_minutes ?? 0);

        setFreezeHorizonMinutes(minutes);
        setFreezeInput(String(minutes));
      }
    };

    ws.onerror = () => {};

    return () => {
      disposed = true;
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;

      if (ws.readyState === WebSocket.OPEN) {
        ws.close(1000, "Component unmount");
      }
    };
  }, [loadChangeLog]);

  useEffect(() => {
    return () => {
      for (const entry of moveDebounceRef.current.values()) {
        if (entry.timer) clearTimeout(entry.timer);
        if (entry.controller) entry.controller.abort();
        if (entry.resolve) entry.resolve({ cancelled: true });
      }

      moveDebounceRef.current.clear();
    };
  }, []);

  const handleMove = useCallback(
    (op) => {
      return new Promise((resolve, reject) => {
        const key = op.id;
        const prev = moveDebounceRef.current.get(key);

        if (prev) {
          if (prev.timer) clearTimeout(prev.timer);
          if (prev.controller) prev.controller.abort();
          if (prev.resolve) prev.resolve({ superseded: true });
        }

        const controller = new AbortController();

        const timer = setTimeout(async () => {
          try {
            const res = await fetch(`http://127.0.0.1:8000/update_op/${op.id}`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(op),
              signal: controller.signal,
            });

            if (!res.ok) {
              let errorData = null;

              try {
                errorData = await res.json();
              } catch {
                errorData = null;
              }

              const message =
                errorData?.detail?.message ||
                errorData?.detail ||
                `HTTP ${res.status}`;

              throw new Error(
                typeof message === "string" ? message : JSON.stringify(message)
              );
            }

            loadChangeLog();

            resolve({ ok: true });
          } catch (e) {
            if (e.name === "AbortError") resolve({ aborted: true });
            else reject(e);
          } finally {
            const current = moveDebounceRef.current.get(key);

            if (current && current.resolve === resolve) {
              moveDebounceRef.current.delete(key);
            }
          }
        }, MOVE_DEBOUNCE_MS);

        moveDebounceRef.current.set(key, { timer, controller, resolve });
      });
    },
    [loadChangeLog]
  );

  const handleRollback = useCallback(async () => {
    try {
      const res = await fetch("http://127.0.0.1:8000/rollback_last_change", {
        method: "POST",
      });

      const data = await res.json();

      if (!res.ok) {
        alert("Откат не выполнен: " + JSON.stringify(data));
        return;
      }

      if (data.operation) {
        setOps((prev) =>
          prev.map((op) =>
            op.id === data.operation.id ? { ...op, ...data.operation } : op
          )
        );
      }

      loadChangeLog();
    } catch (error) {
      console.error("Ошибка отката:", error);
      alert("Не удалось выполнить откат");
    }
  }, [loadChangeLog]);

  const handleRollbackChangeSet = useCallback(
    async (changeSetId) => {
      try {
        const res = await fetch(
          `http://127.0.0.1:8000/plan_change_log/change_set/${changeSetId}/rollback`,
          { method: "POST" }
        );

        const data = await res.json();

        if (!res.ok) {
          const detail = data?.detail?.message || data?.detail;
          alert(
            "Не удалось откатить группу изменений: " +
              (detail || JSON.stringify(data))
          );
          return;
        }

        const updates = Array.isArray(data.updated_operations)
          ? data.updated_operations
          : [];

        setOps((prev) => {
          const byId = new Map(prev.map((op) => [op.id, op]));

          for (const update of updates) {
            const existing = byId.get(update.id);
            byId.set(update.id, existing ? { ...existing, ...update } : update);
          }

          return Array.from(byId.values());
        });

        loadChangeLog();
        alert("Группа изменений откатана");
      } catch (error) {
        console.error("Ошибка отката группы изменений:", error);
        alert("Не удалось откатить группу изменений");
      }
    },
    [loadChangeLog]
  );

  const handleSaveFreezeHorizon = useCallback(async () => {
    const minutes = Number(freezeInput);

    if (!Number.isInteger(minutes) || minutes < 0) {
      alert("Горизонт заморозки должен быть целым неотрицательным числом");
      return;
    }

    try {
      const res = await fetch("http://127.0.0.1:8000/settings/freeze_horizon", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-User-Role": CURRENT_USER_ROLE,
        },
        body: JSON.stringify({ minutes }),
      });

      const data = await res.json();

      if (!res.ok) {
        alert(
          "Не удалось изменить горизонт заморозки: " +
            (data?.detail || JSON.stringify(data))
        );
        return;
      }

      setFreezeHorizonMinutes(data.freeze_horizon_minutes);
      setFreezeInput(String(data.freeze_horizon_minutes));

      alert("Горизонт заморозки обновлён");
    } catch (error) {
      console.error("Ошибка изменения горизонта заморозки:", error);
      alert("Не удалось изменить горизонт заморозки");
    }
  }, [freezeInput]);

  const isChangeRolledBack = (value) => {
    return value === true || value === "true" || value === 1 || value === "1";
  };

  const canRollbackChangeSetRow = (row) => {
    return (
      row.change_set_id &&
      row.change_reason === "manual_gantt_drag" &&
      !isChangeRolledBack(row.is_rolled_back)
    );
  };

  const rollbackChangeGroups = Array.from(
    new Map(
      changeLog
        .filter(canRollbackChangeSetRow)
        .map((row) => [
          row.change_set_id,
          {
            change_set_id: row.change_set_id,
          },
        ])
    ).values()
  );
  const latestRollbackChangeGroup = changeLog.find(canRollbackChangeSetRow);

  return (
    <div style={{ padding: "20px" }}>
      <div
        style={{
          display: "flex",
          gap: "12px",
          marginBottom: "12px",
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <button
          onClick={handleRollback}
          style={{
            padding: "8px 12px",
            cursor: "pointer",
          }}
        >
          Откатить последнюю операцию
        </button>

        <button
          onClick={() => {
            if (!latestRollbackChangeGroup?.change_set_id) {
              alert("Нет доступной группы изменений для отката");
              return;
            }

            handleRollbackChangeSet(latestRollbackChangeGroup.change_set_id);
          }}
          disabled={!latestRollbackChangeGroup}
          title={
            latestRollbackChangeGroup?.change_set_id
              ? latestRollbackChangeGroup.change_set_id
              : "Нет доступной группы изменений для отката"
          }
          style={{
            padding: "8px 12px",
            cursor: latestRollbackChangeGroup ? "pointer" : "not-allowed",
          }}
        >
          Откатить последнюю группу
        </button>

        <button
          onClick={() => {
            const next = !showHistory;
            setShowHistory(next);

            if (!showHistory) {
              loadChangeLog();
            }
          }}
          style={{
            padding: "8px 12px",
            cursor: "pointer",
          }}
        >
          {showHistory ? "Скрыть историю изменений" : "История изменений"}
        </button>

        <div
          style={{
            display: "flex",
            gap: "8px",
            alignItems: "center",
            padding: "8px",
            border: "1px solid #ccc",
          }}
        >
          <span>Горизонт заморозки:</span>

          <input
            type="number"
            value={freezeInput}
            disabled={!canEditFreezeZone}
            onChange={(e) => setFreezeInput(e.target.value)}
            style={{ width: "90px", padding: "6px" }}
          />

          <span>мин.</span>

          <button
            onClick={handleSaveFreezeHorizon}
            disabled={!canEditFreezeZone}
            style={{
              padding: "6px 10px",
              cursor: canEditFreezeZone ? "pointer" : "not-allowed",
            }}
          >
            Сохранить
          </button>

          {!canEditFreezeZone && (
            <span style={{ color: "#777" }}>
              Изменять может только начальник производства
            </span>
          )}
        </div>

        <div
          style={{
            display: "flex",
            gap: "8px",
            alignItems: "center",
            padding: "8px",
            border: "1px solid #ccc",
          }}
        >
          <span>Группа операций:</span>
          <select
            value={operationFilter}
            onChange={(e) => setOperationFilter(e.target.value)}
            style={{ padding: "6px" }}
          >
            <option value="">Все</option>
            {operationGroups.map((operationType) => (
              <option key={operationType} value={operationType}>
                {OPERATION_NAMES[operationType] || operationType}
              </option>
            ))}
          </select>
        </div>
      </div>

      {showHistory && (
        <div
          style={{
            marginBottom: "16px",
            border: "1px solid #ccc",
            padding: "12px",
            maxHeight: "320px",
            overflow: "auto",
          }}
        >
          <h3 style={{ marginTop: 0 }}>История изменений</h3>

          <div
            style={{
              display: "flex",
              gap: "8px",
              marginBottom: "12px",
              flexWrap: "wrap",
              alignItems: "center",
            }}
          >
            <input
              type="number"
              placeholder="Операция"
              value={historyFilters.operationId}
              onChange={(e) =>
                setHistoryFilters((prev) => ({
                  ...prev,
                  operationId: e.target.value,
                }))
              }
              style={{ width: "110px", padding: "6px" }}
            />

            <input
              type="text"
              placeholder="Станок"
              value={historyFilters.machine}
              onChange={(e) =>
                setHistoryFilters((prev) => ({
                  ...prev,
                  machine: e.target.value,
                }))
              }
              style={{ width: "110px", padding: "6px" }}
            />

            <select
              value={historyFilters.changeReason}
              onChange={(e) =>
                setHistoryFilters((prev) => ({
                  ...prev,
                  changeReason: e.target.value,
                }))
              }
              style={{ padding: "6px" }}
            >
              <option value="">Все типы</option>
              <option value="manual_gantt_drag">Перемещение</option>
              <option value="manual_rollback">Откат</option>
            </select>

            <select
              value={historyFilters.rollbackStatus}
              onChange={(e) =>
                setHistoryFilters((prev) => ({
                  ...prev,
                  rollbackStatus: e.target.value,
                }))
              }
              style={{ padding: "6px" }}
            >
              <option value="">Любой откат</option>
              <option value="true">Откат выполнен</option>
              <option value="false">Без отката</option>
            </select>

            <button
              onClick={() => setHistoryFilters(DEFAULT_HISTORY_FILTERS)}
              style={{ padding: "6px 10px", cursor: "pointer" }}
            >
              Сбросить
            </button>
          </div>

          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "14px",
            }}
          >
            <thead>
              <tr>
                <th style={thStyle}>ID</th>
                <th style={thStyle}>Группа</th>
                <th style={thStyle}>Операция</th>
                <th style={thStyle}>Тип</th>
                <th style={thStyle}>Станок</th>
                <th style={thStyle}>Время</th>
                <th style={thStyle}>Откат</th>
                <th style={thStyle}>Создано</th>
                <th style={thStyle}>Действие</th>
              </tr>
            </thead>

            <tbody>
              {changeLog.map((row) => {
                const isRollback = row.change_reason === "manual_rollback";
                const isDrag = row.change_reason === "manual_gantt_drag";
                const canRollbackChangeSet = canRollbackChangeSetRow(row);

                return (
                  <tr
                    key={row.id}
                    style={{
                      backgroundColor: isRollback
                        ? "#e8f3ff"
                        : isDrag
                        ? "#fff7df"
                        : "white",
                    }}
                  >
                    <td style={tdStyle}>{row.id}</td>
                    <td style={tdStyle}>
                      {row.change_set_id ? (
                        <span title={row.change_set_id}>
                          {row.change_set_id.slice(0, 8)}
                        </span>
                      ) : (
                        ""
                      )}
                    </td>
                    <td style={tdStyle}>{row.operation_id}</td>
                    <td style={tdStyle}>
                      {row.change_reason === "manual_gantt_drag"
                        ? "Перемещение"
                        : row.change_reason === "manual_rollback"
                        ? "Откат"
                        : row.change_reason}
                    </td>
                    <td style={tdStyle}>
                      {row.old_machine_id} → {row.new_machine_id}
                    </td>
                    <td style={tdStyle}>
                      {row.old_start_time}-{row.old_end_time} →{" "}
                      {row.new_start_time}-{row.new_end_time}
                    </td>
                    <td style={tdStyle}>
                      {row.is_rolled_back ? "Да" : "Нет"}
                    </td>
                    <td style={tdStyle}>
                      {row.created_at
                        ? new Date(row.created_at).toLocaleString()
                        : ""}
                    </td>
                    <td style={tdStyle}>
                      {canRollbackChangeSet && (
                        <button
                          onClick={() => handleRollbackChangeSet(row.change_set_id)}
                          style={{
                            padding: "4px 8px",
                            cursor: "pointer",
                          }}
                        >
                          Откатить группу
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Gantt
        data={filteredOps}
        onMove={handleMove}
        freezeHorizonMinutes={freezeHorizonMinutes}
      />
    </div>
  );
}

const thStyle = {
  border: "1px solid #ccc",
  padding: "6px",
  textAlign: "left",
  background: "#f3f3f3",
};

const tdStyle = {
  border: "1px solid #ccc",
  padding: "6px",
};

export default App;
