import { useCallback, useEffect, useRef, useState } from "react";
import Gantt from "./Gantt";

const MOVE_DEBOUNCE_MS = 350;
const HISTORY_LIMIT = 50;
const SELECTED_PLAN_VERSION_STORAGE_KEY = "aps_mes_selected_plan_version_id";
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

const formatMinutesDelta = (value) => {
  const minutes = Number(value || 0);

  if (minutes > 0) return `+${minutes} мин.`;
  if (minutes < 0) return `${minutes} мин.`;
  return "0 мин.";
};

const getPlanFinishDeltaText = (value) => {
  const minutes = Number(value || 0);

  if (minutes > 0) return `хуже на ${minutes} мин.`;
  if (minutes < 0) return `лучше на ${Math.abs(minutes)} мин.`;
  return "без изменений";
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
  const [activePlanVersion, setActivePlanVersion] = useState(null);
  const [planVersions, setPlanVersions] = useState([]);
  const [selectedPlanVersionId, setSelectedPlanVersionId] = useState("");
  const [machines, setMachines] = useState([]);
  const [planDiff, setPlanDiff] = useState(null);
  const [showPlanDiff, setShowPlanDiff] = useState(false);
  const [isPlanDiffLoading, setIsPlanDiffLoading] = useState(false);

  const moveDebounceRef = useRef(new Map());

  const canEditFreezeZone = CURRENT_USER_ROLE === "production_manager";
  const selectedPlanVersion = planVersions.find(
    (version) => String(version.id) === String(selectedPlanVersionId)
  );
  const selectedPlanVersionStatus = selectedPlanVersion?.status || "";
  const canEditSelectedPlan = selectedPlanVersionStatus === "draft";
  const operationGroups = Array.from(
    new Set(ops.map((op) => op.operation_type).filter(Boolean))
  ).sort();
  const filteredOps = operationFilter
    ? ops.filter((op) => op.operation_type === operationFilter)
    : ops;

  const loadOperations = useCallback((planVersionId = selectedPlanVersionId) => {
    const url = planVersionId
      ? `http://127.0.0.1:8000/operations?plan_version_id=${planVersionId}`
      : "http://127.0.0.1:8000/operations";

    fetch(url)
      .then((res) => res.json())
      .then((data) => {
        setOps(Array.isArray(data) ? data : []);
      })
      .catch(() => {});
  }, [selectedPlanVersionId]);

  const loadChangeLog = useCallback(() => {
    const params = new URLSearchParams({ limit: String(HISTORY_LIMIT) });

    if (selectedPlanVersionId) {
      params.set("plan_version_id", selectedPlanVersionId);
    }

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
  }, [historyFilters, selectedPlanVersionId]);

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

  const loadActivePlanVersion = useCallback(() => {
    fetch("http://127.0.0.1:8000/plan_versions/active")
      .then((res) => res.json())
      .then((data) => setActivePlanVersion(data))
      .catch(() => {});
  }, []);

  const loadMachines = useCallback(() => {
    fetch("http://127.0.0.1:8000/machines")
      .then((res) => res.json())
      .then((data) => {
        setMachines(Array.isArray(data) ? data : []);
      })
      .catch(() => {});
  }, []);

  const loadPlanVersions = useCallback(() => {
    fetch("http://127.0.0.1:8000/plan_versions")
      .then((res) => res.json())
      .then((data) => {
        const versions = Array.isArray(data) ? data : [];
        setPlanVersions(versions);

        setSelectedPlanVersionId((current) => {
          if (
            current &&
            versions.some((version) => String(version.id) === String(current))
          ) {
            return current;
          }

          const stored = localStorage.getItem(SELECTED_PLAN_VERSION_STORAGE_KEY);
          if (
            stored &&
            versions.some((version) => String(version.id) === String(stored))
          ) {
            return stored;
          }

          const active = versions.find((version) => version.status === "active");
          const activeId = active ? String(active.id) : "";
          if (activeId) {
            localStorage.setItem(SELECTED_PLAN_VERSION_STORAGE_KEY, activeId);
          } else {
            localStorage.removeItem(SELECTED_PLAN_VERSION_STORAGE_KEY);
          }
          return activeId;
        });
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadFreezeHorizon();
    loadActivePlanVersion();
    loadPlanVersions();
    loadMachines();
  }, [loadFreezeHorizon, loadActivePlanVersion, loadPlanVersions, loadMachines]);

  useEffect(() => {
    loadOperations();
    loadChangeLog();
  }, [loadOperations, loadChangeLog]);

  useEffect(() => {
    let disposed = false;
    const ws = new WebSocket("ws://127.0.0.1:8000/ws");

    ws.onmessage = (event) => {
      if (disposed) return;

      const msg = JSON.parse(event.data);

      if (msg.type === "operation_update") {
        if (
          selectedPlanVersionId &&
          String(msg.data.plan_version_id) !== String(selectedPlanVersionId)
        ) {
          return;
        }

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
        const visibleUpdates = selectedPlanVersionId
          ? updates.filter(
              (update) =>
                String(update.plan_version_id) === String(selectedPlanVersionId)
            )
          : updates;

        if (visibleUpdates.length === 0) {
          return;
        }

        setOps((prev) => {
          const byId = new Map(prev.map((op) => [op.id, op]));

          for (const update of visibleUpdates) {
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
  }, [loadChangeLog, selectedPlanVersionId]);

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

  const loadPlanDiff = useCallback(async () => {
    if (showPlanDiff) {
      setShowPlanDiff(false);
      setPlanDiff(null);
      return;
    }

    if (!selectedPlanVersionId || !canEditSelectedPlan) {
      alert("Сравнение доступно только для черновой версии плана");
      return;
    }

    setIsPlanDiffLoading(true);

    try {
      const res = await fetch(
        `http://127.0.0.1:8000/plan_versions/${selectedPlanVersionId}/diff`
      );

      const data = await res.json();

      if (!res.ok) {
        alert(
          "Не удалось сравнить версии плана: " +
            (data?.detail || JSON.stringify(data))
        );
        return;
      }

      setPlanDiff(data);
      setShowPlanDiff(true);
    } catch (error) {
      console.error("Ошибка сравнения версий плана:", error);
      alert("Не удалось сравнить версии плана");
    } finally {
      setIsPlanDiffLoading(false);
    }
  }, [selectedPlanVersionId, canEditSelectedPlan, showPlanDiff]);

  const handleMove = useCallback(
    (op) => {
      if (!canEditSelectedPlan) {
        return Promise.reject(
          new Error("Редактировать можно только черновую версию плана")
        );
      }

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
            const res = await fetch(
              `http://127.0.0.1:8000/update_op/${op.id}?plan_version_id=${selectedPlanVersionId}`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(op),
                signal: controller.signal,
              }
            );

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

            const data = await res.json();
            const updates = Array.isArray(data.changed_operations)
              ? data.changed_operations
              : data.operation
              ? [data.operation]
              : [];

            if (updates.length > 0) {
              setOps((prevOps) => {
                const byId = new Map(
                  prevOps.map((existingOp) => [existingOp.id, existingOp])
                );

                for (const update of updates) {
                  if (
                    selectedPlanVersionId &&
                    String(update.plan_version_id) !== String(selectedPlanVersionId)
                  ) {
                    continue;
                  }

                  const existing = byId.get(update.id);
                  byId.set(update.id, existing ? { ...existing, ...update } : update);
                }

                return Array.from(byId.values());
              });
            }

            loadChangeLog();
            setPlanDiff(null);
            setShowPlanDiff(false);

            resolve({ ok: true, data });
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
    [loadChangeLog, canEditSelectedPlan, selectedPlanVersionId]
  );

  const handleRollbackChangeSet = useCallback(
    async (changeSetId) => {
      if (!canEditSelectedPlan) {
        alert("Откат группы изменений можно выполнять только в черновой версии плана");
        return;
      }

      try {
        const res = await fetch(
          `http://127.0.0.1:8000/plan_change_log/change_set/${changeSetId}/rollback?plan_version_id=${selectedPlanVersionId}`,
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
        setPlanDiff(null);
        setShowPlanDiff(false);
        alert("Группа изменений откатана");
      } catch (error) {
        console.error("Ошибка отката группы изменений:", error);
        alert("Не удалось откатить группу изменений");
      }
    },
    [loadChangeLog, canEditSelectedPlan, selectedPlanVersionId]
  );

  const handleCloneActivePlan = useCallback(async () => {
    try {
      const res = await fetch("http://127.0.0.1:8000/plan_versions/clone_active", {
        method: "POST",
      });

      const data = await res.json();

      if (!res.ok) {
        alert(
          "Не удалось создать копию активного плана: " +
            (data?.detail || JSON.stringify(data))
        );
        return;
      }

      const newVersion = data.plan_version;

      await loadPlanVersions();
      localStorage.setItem(
        SELECTED_PLAN_VERSION_STORAGE_KEY,
        String(newVersion.id)
      );
      setSelectedPlanVersionId(String(newVersion.id));
      setPlanDiff(null);
      setShowPlanDiff(false);

      alert("Создана черновая копия активного плана");
    } catch (error) {
      console.error("Ошибка создания копии активного плана:", error);
      alert("Не удалось создать копию активного плана");
    }
  }, [loadPlanVersions]);

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
          onClick={() => {
            if (!latestRollbackChangeGroup?.change_set_id) {
              alert("Нет доступной группы изменений для отката");
              return;
            }

            handleRollbackChangeSet(latestRollbackChangeGroup.change_set_id);
          }}
          disabled={!latestRollbackChangeGroup || !canEditSelectedPlan}
          title={
            latestRollbackChangeGroup?.change_set_id
              ? latestRollbackChangeGroup.change_set_id
              : "Нет доступной группы изменений для отката"
          }
          style={{
            padding: "8px 12px",
            cursor:
              latestRollbackChangeGroup && canEditSelectedPlan
                ? "pointer"
                : "not-allowed",
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
            padding: "8px",
            border: "1px solid #ccc",
          }}
        >
          Активный план:{" "}
          {activePlanVersion
            ? `#${activePlanVersion.id} ${activePlanVersion.name || ""}`
            : "не загружен"}
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
          <span>Версия для просмотра:</span>

          <select
            value={selectedPlanVersionId}
            onChange={(e) => {
              const value = e.target.value;
              setSelectedPlanVersionId(value);
              setPlanDiff(null);
              setShowPlanDiff(false);

              if (value) {
                localStorage.setItem(SELECTED_PLAN_VERSION_STORAGE_KEY, value);
              } else {
                localStorage.removeItem(SELECTED_PLAN_VERSION_STORAGE_KEY);
              }
            }}
            style={{ padding: "6px", minWidth: "220px" }}
          >
            <option value="">Активная версия</option>
            {planVersions.map((version) => (
              <option key={version.id} value={String(version.id)}>
                #{version.id} {version.name || "Без названия"} ({version.status})
              </option>
            ))}
          </select>

          <button
            onClick={handleCloneActivePlan}
            style={{ padding: "6px 10px", cursor: "pointer" }}
          >
            Создать копию active
          </button>

          <button
            onClick={loadPlanDiff}
            disabled={!canEditSelectedPlan || isPlanDiffLoading}
            style={{
              padding: "8px 12px",
              cursor:
                canEditSelectedPlan && !isPlanDiffLoading
                  ? "pointer"
                  : "not-allowed",
            }}
          >
            {isPlanDiffLoading
              ? "Сравнение..."
              : showPlanDiff
              ? "Скрыть сравнение"
              : "Сравнить с active"}
          </button>

          <span style={{ color: canEditSelectedPlan ? "#2e7d32" : "#777" }}>
            {canEditSelectedPlan
              ? "Черновик можно редактировать"
              : "Active доступен только для просмотра"}
          </span>
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

      {showPlanDiff && planDiff && (
        <div
          style={{
            marginBottom: "16px",
            border: "1px solid #ccc",
            padding: "12px",
            maxHeight: "320px",
            overflow: "auto",
          }}
        >
          <h3 style={{ marginTop: 0 }}>
            Сравнение draft #{planDiff.draft_plan_version?.id} с active #
            {planDiff.active_plan_version?.id}
          </h3>

          <div style={{ marginBottom: "16px" }}>
            <h4 style={{ margin: "0 0 8px" }}>Итоги плана</h4>
            <div>
              Окончание плана: active{" "}
              {planDiff.summary?.plan_finish_active ?? 0} → draft{" "}
              {planDiff.summary?.plan_finish_draft ?? 0},{" "}
              {getPlanFinishDeltaText(planDiff.summary?.plan_finish_delta)}
            </div>
            <div>
              Изменено операций: {planDiff.summary?.changed_operations ?? 0} из{" "}
              {planDiff.summary?.total_operations ?? 0}
            </div>
            <div>
              Затронуто заказов: {planDiff.summary?.affected_orders ?? 0};{" "}
              операций позже: {planDiff.summary?.operations_finished_later ?? 0};{" "}
              операций раньше: {planDiff.summary?.operations_finished_earlier ?? 0}
            </div>
            <div>
              Смен станка: {planDiff.summary?.machine_changed ?? 0};{" "}
              просроченных заказов: active{" "}
              {planDiff.summary?.late_orders_active ?? 0} → draft{" "}
              {planDiff.summary?.late_orders_draft ?? 0}
            </div>
            <div>
              Суммарное опоздание: active{" "}
              {planDiff.summary?.total_lateness_active ?? 0} → draft{" "}
              {planDiff.summary?.total_lateness_draft ?? 0}
            </div>
          </div>

          <div style={{ marginBottom: "16px" }}>
            <h4 style={{ margin: "0 0 8px" }}>Влияние на заказы</h4>
            {Array.isArray(planDiff.order_impacts) &&
            planDiff.order_impacts.length > 0 ? (
              <table
                style={{
                  width: "100%",
                  borderCollapse: "collapse",
                  fontSize: "14px",
                  marginBottom: "12px",
                }}
              >
                <thead>
                  <tr>
                    <th style={thStyle}>Заказ</th>
                    <th style={thStyle}>Изделие</th>
                    <th style={thStyle}>Окончание active</th>
                    <th style={thStyle}>Окончание draft</th>
                    <th style={thStyle}>Δ</th>
                    <th style={thStyle}>Просрочка active</th>
                    <th style={thStyle}>Просрочка draft</th>
                  </tr>
                </thead>
                <tbody>
                  {planDiff.order_impacts.map((row) => (
                    <tr key={row.order_id}>
                      <td style={tdStyle}>{row.order_no || row.order_id}</td>
                      <td style={tdStyle}>
                        {row.product_name || row.product_id}
                      </td>
                      <td style={tdStyle}>{row.active_finish}</td>
                      <td style={tdStyle}>{row.draft_finish}</td>
                      <td style={tdStyle}>
                        {formatMinutesDelta(row.finish_delta)}
                      </td>
                      <td style={tdStyle}>{row.active_lateness}</td>
                      <td style={tdStyle}>{row.draft_lateness}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div>Окончание заказов не изменилось</div>
            )}
          </div>

          <div style={{ marginBottom: "16px" }}>
            <h4 style={{ margin: "0 0 8px" }}>Влияние на станки</h4>
            {Array.isArray(planDiff.machine_impacts) &&
            planDiff.machine_impacts.length > 0 ? (
              <table
                style={{
                  width: "100%",
                  borderCollapse: "collapse",
                  fontSize: "14px",
                  marginBottom: "12px",
                }}
              >
                <thead>
                  <tr>
                    <th style={thStyle}>Станок</th>
                    <th style={thStyle}>Окончание active</th>
                    <th style={thStyle}>Окончание draft</th>
                    <th style={thStyle}>Δ окончания</th>
                    <th style={thStyle}>Занято active</th>
                    <th style={thStyle}>Занято draft</th>
                    <th style={thStyle}>Δ занятости</th>
                    <th style={thStyle}>Изм. операций</th>
                  </tr>
                </thead>
                <tbody>
                  {planDiff.machine_impacts.map((row) => (
                    <tr key={row.machine_id}>
                      <td style={tdStyle}>
                        {row.machine_name || row.machine_id}
                      </td>
                      <td style={tdStyle}>{row.active_finish}</td>
                      <td style={tdStyle}>{row.draft_finish}</td>
                      <td style={tdStyle}>
                        {formatMinutesDelta(row.finish_delta)}
                      </td>
                      <td style={tdStyle}>{row.active_busy_minutes}</td>
                      <td style={tdStyle}>{row.draft_busy_minutes}</td>
                      <td style={tdStyle}>
                        {formatMinutesDelta(row.busy_delta)}
                      </td>
                      <td style={tdStyle}>{row.changed_operations}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div>Загрузка станков не изменилась</div>
            )}
          </div>

          {Array.isArray(planDiff.items) && planDiff.items.length === 0 ? (
            <div>Отличий от active-плана нет</div>
          ) : (
            <>
            <h4 style={{ margin: "0 0 8px" }}>Изменённые операции</h4>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: "14px",
              }}
            >
              <thead>
                <tr>
                  <th style={thStyle}>Заказ</th>
                  <th style={thStyle}>Изделие</th>
                  <th style={thStyle}>Операция</th>
                  <th style={thStyle}>Станок</th>
                  <th style={thStyle}>Начало</th>
                  <th style={thStyle}>Окончание</th>
                  <th style={thStyle}>Δ окончания</th>
                </tr>
              </thead>
              <tbody>
                {(Array.isArray(planDiff.items) ? planDiff.items : []).map((row) => (
                  <tr key={row.operation_id}>
                    <td style={tdStyle}>{row.order_no || row.order_id}</td>
                    <td style={tdStyle}>
                      {row.product_name || row.product_id}
                    </td>
                    <td style={tdStyle}>
                      {row.sequence_no}{" "}
                      {row.operation_name || row.operation_type}
                    </td>
                    <td style={tdStyle}>
                      {row.machine_changed
                        ? `${row.active_machine} → ${row.draft_machine}`
                        : row.draft_machine}
                    </td>
                    <td style={tdStyle}>
                      {row.start_changed
                        ? `${row.active_start} → ${row.draft_start}`
                        : row.draft_start}
                    </td>
                    <td style={tdStyle}>
                      {row.end_changed
                        ? `${row.active_end} → ${row.draft_end}`
                        : row.draft_end}
                    </td>
                    <td style={tdStyle}>
                      {formatMinutesDelta(row.end_delta)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </>
          )}
        </div>
      )}

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
                    <td style={tdStyle}>
                      <span title={`operation_id=${row.operation_id}`}>
                        {row.operation_label || row.operation_id}
                      </span>
                    </td>
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
        machines={machines}
        onMove={handleMove}
        freezeHorizonMinutes={freezeHorizonMinutes}
        canEdit={canEditSelectedPlan}
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
