import { useCallback, useEffect, useRef, useState } from "react";
import Gantt from "./Gantt";

const MOVE_DEBOUNCE_MS = 350;

function App() {
  const [ops, setOps] = useState([]);
  const [changeLog, setChangeLog] = useState([]);
  const [showHistory, setShowHistory] = useState(false);

  const moveDebounceRef = useRef(new Map()); // key: op.id -> { timer, controller, resolve }

  // ======================
  // LOAD OPERATIONS
  // ======================
  const loadOperations = useCallback(() => {
    fetch("http://127.0.0.1:8000/operations")
      .then((res) => res.json())
      .then((data) => {
        setOps(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        // при необходимости можно добавить UI-уведомление
      });
  }, []);

  // ======================
  // LOAD CHANGE LOG
  // ======================
  const loadChangeLog = useCallback(() => {
    fetch("http://127.0.0.1:8000/plan_change_log?limit=20")
      .then((res) => res.json())
      .then((data) => {
        setChangeLog(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        // при необходимости можно добавить UI-уведомление
      });
  }, []);

  // ======================
  // INITIAL LOAD
  // ======================
  useEffect(() => {
    loadOperations();
    loadChangeLog();
  }, [loadOperations, loadChangeLog]);

  // ======================
  // WEBSOCKET UPDATES
  // ======================
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
    };

    ws.onerror = () => {
      // без лишних логов
    };

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

  // ======================
  // CLEANUP DEBOUNCE
  // ======================
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

  // ======================
  // MOVE FROM GANTT
  // ======================
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

            if (!res.ok) throw new Error(`HTTP ${res.status}`);

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

  // ======================
  // ROLLBACK
  // ======================
  const handleRollback = useCallback(async () => {
    console.log("↩️ Откатить последнюю операцию");

    try {
      const res = await fetch("http://127.0.0.1:8000/rollback_last_change", {
        method: "POST",
      });

      const data = await res.json();

      console.log("↩️ ROLLBACK RESPONSE:", data);

      if (!res.ok) {
        alert("Rollback failed: " + JSON.stringify(data));
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
      console.error("↩️ ROLLBACK ERROR:", error);
      alert("Rollback request failed");
    }
  }, [loadChangeLog]);

  return (
    <div style={{ padding: "20px" }}>
      <h2>APS Gantt</h2>

      <div style={{ display: "flex", gap: "12px", marginBottom: "12px" }}>
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

        {/* <button
          onClick={loadChangeLog}
          style={{
            padding: "8px 12px",
            cursor: "pointer",
          }}
        >
          Обновить историю
        </button> */}
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
          <h3 style={{ marginTop: 0 }}>Последние 20 изменений</h3>

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
                <th style={thStyle}>Операция</th>
                <th style={thStyle}>Тип</th>
                <th style={thStyle}>Станок</th>
                <th style={thStyle}>Время</th>
                <th style={thStyle}>Откат</th>
                <th style={thStyle}>Создано</th>
              </tr>
            </thead>

            <tbody>
              {changeLog.map((row) => {
                const isRollback = row.change_reason === "manual_rollback";
                const isDrag = row.change_reason === "manual_gantt_drag";

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
                    <td style={tdStyle}>{row.operation_id}</td>
                    <td style={tdStyle}>
                      {row.change_reason === "manual_gantt_drag"
                        ? "Drag"
                        : row.change_reason === "manual_rollback"
                        ? "Rollback"
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
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <Gantt data={ops} onMove={handleMove} />
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
