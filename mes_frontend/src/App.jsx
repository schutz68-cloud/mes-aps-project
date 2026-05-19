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

const PLAN_VERSION_STATUS_LABELS = {
  active: "активный план",
  draft: "черновик",
  archived: "архивный план",
};

const VALIDATION_ERROR_LABELS = {
  duplicate_plan_operation: "Дубль плановой операции",
  missing_plan_operation: "Отсутствует плановая операция",
  extra_plan_operation: "Лишняя плановая операция",
  missing_order_operation: "Не найдена операция заказа",
  missing_order_item: "Не найдена позиция заказа",
  missing_routing_operation: "Не найдена операция маршрута",
  missing_machine: "Не найден станок",
  missing_rate: "Не найдена норма",
  invalid_rate: "Некорректная норма",
  invalid_machine_group: "Недопустимая группа оборудования",
  duration_error: "Ошибка длительности",
  route_buffer_error: "Ошибка буфера между переделами",
  machine_buffer_error: "Ошибка буфера станка",
  machine_overlap_error: "Пересечение на станке",
  frozen_zone_error: "Ошибка замороженной зоны",
  operation_calendar_error: "Ошибка сменного календаря",
  setup_team_conflict: "Конфликт наладчиков",
  missing_setup_team_link: "Не назначена бригада наладчиков",
};

const MES_RUN_STATUS_LABELS = {
  created: "создано",
  released: "выпущено",
  in_work: "выполняется",
  completed: "завершено",
  cancelled: "отменено",
};

const MES_OPERATION_STATUS_LABELS = {
  planned: "запланировано",
  released: "выпущено",
  in_work: "в работе",
  partially_completed: "частично выполнено",
  completed: "завершено",
  excluded: "исключено",
};

const MES_REPORT_TYPE_LABELS = {
  production: "выполнение",
  correction: "корректировка",
};

const MACHINE_GROUP_LABELS = {
  COIL_A: "Навивка",
  COIL_B: "Навивка",
  BEND: "Загиб",
  FACE: "Торцовка",
  HEAT: "Термичка",
  COAT_A: "Покрытие",
  COAT_B: "Покрытие",
};

const MACHINE_GROUP_ORDER = {
  COIL_A: 10,
  COIL_B: 11,
  BEND: 20,
  FACE: 30,
  HEAT: 40,
  COAT_A: 50,
  COAT_B: 51,
};

const PLAN_DAY_START_MINUTE_OF_DAY = 8 * 60;

function formatPlanMinute(minute) {
  const value = Number(minute ?? 0);
  const absoluteMinute = PLAN_DAY_START_MINUTE_OF_DAY + value;
  const day = Math.floor(absoluteMinute / 1440) + 1;
  const minuteOfDay = ((absoluteMinute % 1440) + 1440) % 1440;
  const hours = Math.floor(minuteOfDay / 60);
  const minutes = minuteOfDay % 60;

  return `День ${day}, ${String(hours).padStart(2, "0")}:${String(
    minutes
  ).padStart(2, "0")}`;
}

function formatPlanInterval(start, end) {
  return `${formatPlanMinute(start)}–${formatPlanMinute(end)}`;
}

function formatDateTime(value) {
  if (!value) return "";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "";
  }

  return date.toLocaleString("ru-RU");
}

function getMesOperationRowStyle(status) {
  const base = {
    borderLeft: "4px solid transparent",
  };

  if (status === "planned") {
    return { ...base, background: "#f8f9fa", borderLeftColor: "#bdbdbd" };
  }

  if (status === "released") {
    return { ...base, background: "#e8f5e9", borderLeftColor: "#43a047" };
  }

  if (status === "in_work") {
    return { ...base, background: "#fff8e1", borderLeftColor: "#f9a825" };
  }

  if (status === "partially_completed") {
    return { ...base, background: "#fff3cd", borderLeftColor: "#fb8c00" };
  }

  if (status === "completed") {
    return { ...base, background: "#e3f2fd", borderLeftColor: "#1e88e5" };
  }

  if (status === "excluded") {
    return {
      ...base,
      background: "#eeeeee",
      borderLeftColor: "#9e9e9e",
      color: "#777",
    };
  }

  return base;
}

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

const buildPlanVersionTitle = (version) => {
  if (!version) return "Версия плана не выбрана";

  const lines = [
    `План: #${version.id} ${version.name || "Без названия"}`,
    `Статус: ${
      PLAN_VERSION_STATUS_LABELS[version.status] ||
      version.status ||
      "не указан"
    }`,
  ];

  if (version.approved_at) {
    lines.push(
      `Принят: ${new Date(version.approved_at).toLocaleString()}${
        version.approved_by ? `, ${version.approved_by}` : ""
      }`
    );
  }

  if (version.description) {
    lines.push(`Описание: ${version.description}`);
  }

  return lines.join("\n");
};

// Временная роль до полноценной авторизации.
// production_manager может изменять замороженную зону.
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
  const [calendarBackgrounds, setCalendarBackgrounds] = useState([]);
  const [planDiff, setPlanDiff] = useState(null);
  const [showPlanDiff, setShowPlanDiff] = useState(false);
  const [isPlanDiffLoading, setIsPlanDiffLoading] = useState(false);
  const [planValidation, setPlanValidation] = useState(null);
  const [showPlanValidation, setShowPlanValidation] = useState(false);
  const [isPlanValidationLoading, setIsPlanValidationLoading] = useState(false);
  const [showActiveOverlay, setShowActiveOverlay] = useState(false);
  const [activeOverlayOps, setActiveOverlayOps] = useState([]);
  const [planVersionNameInput, setPlanVersionNameInput] = useState("");
  const [planVersionDescriptionInput, setPlanVersionDescriptionInput] =
    useState("");
  const [isPlanVersionSaving, setIsPlanVersionSaving] = useState(false);
  const [isPlanVersionEditOpen, setIsPlanVersionEditOpen] = useState(false);
  const [mesScheduleRuns, setMesScheduleRuns] = useState([]);
  const [selectedMesRun, setSelectedMesRun] = useState(null);
  const [selectedMesRunOperations, setSelectedMesRunOperations] = useState([]);
  const [mesScreen, setMesScreen] = useState("runs");
  const [mesWorkplaceOperations, setMesWorkplaceOperations] = useState([]);
  const [isMesWorkplaceLoading, setIsMesWorkplaceLoading] = useState(false);
  const [selectedMesOperationCard, setSelectedMesOperationCard] =
    useState(null);
  const [mesOperationCardTab, setMesOperationCardTab] = useState("fact");
  const [mesOperationCardReports, setMesOperationCardReports] = useState([]);
  const [
    isMesOperationCardReportsLoading,
    setIsMesOperationCardReportsLoading,
  ] = useState(false);
  const [selectedMesOperationForReports, setSelectedMesOperationForReports] =
    useState(null);
  const [selectedMesOperationReports, setSelectedMesOperationReports] =
    useState([]);
  const [isReportsLoading, setIsReportsLoading] = useState(false);
  const [shiftReportDay, setShiftReportDay] = useState("1");
  const [shiftReportShift, setShiftReportShift] = useState("1");
  const [shiftReport, setShiftReport] = useState(null);
  const [isShiftReportLoading, setIsShiftReportLoading] = useState(false);
  const [showHiddenMesRuns, setShowHiddenMesRuns] = useState(false);
  const [mesWorkshops, setMesWorkshops] = useState([]);
  const [selectedMesWorkshopId, setSelectedMesWorkshopId] = useState("");
  const [currentScreen, setCurrentScreen] = useState("aps");
  const [selectedOrderItemId, setSelectedOrderItemId] = useState(null);

  const moveDebounceRef = useRef(new Map());

  const canEditFreezeZone = CURRENT_USER_ROLE === "production_manager";
  const selectedPlanVersion = planVersions.find(
    (version) => String(version.id) === String(selectedPlanVersionId)
  );
  const displayedPlanVersion = selectedPlanVersion || activePlanVersion;
  const selectedPlanVersionStatus = selectedPlanVersion?.status || "";
  const isActiveSelectedPlan =
    !selectedPlanVersionId || selectedPlanVersionStatus === "active";
  const canEditSelectedPlan = selectedPlanVersionStatus === "draft";
  const canEditSelectedPlanName = selectedPlanVersionStatus === "draft";
  const canEditSelectedPlanDescription =
    selectedPlanVersionStatus === "draft" ||
    selectedPlanVersionStatus === "active" ||
    selectedPlanVersionStatus === "archived";
  const operationGroups = Array.from(
    new Set(ops.map((op) => op.operation_type).filter(Boolean))
  ).sort();
  const filteredOps = operationFilter
    ? ops.filter((op) => op.operation_type === operationFilter)
    : ops;
  const filteredActiveOverlayOps = operationFilter
    ? activeOverlayOps.filter((op) => op.operation_type === operationFilter)
    : activeOverlayOps;
  const selectedOrderItemOperations = selectedOrderItemId
    ? ops.filter(
        (op) => String(op.order_item_id) === String(selectedOrderItemId)
      )
    : [];
  const selectedOrderItemSummary =
    selectedOrderItemOperations.length > 0
      ? {
          orderNo:
            selectedOrderItemOperations[0].order_no ||
            selectedOrderItemOperations[0].order_id ||
            "",
          productName: selectedOrderItemOperations[0].product_name || "",
          quantity: selectedOrderItemOperations[0].quantity || 0,
          start: Math.min(
            ...selectedOrderItemOperations.map((op) => Number(op.start || 0))
          ),
          end: Math.max(
            ...selectedOrderItemOperations.map((op) => Number(op.end || 0))
          ),
        }
      : null;

  useEffect(() => {
    setPlanVersionNameInput(selectedPlanVersion?.name || "");
    setPlanVersionDescriptionInput(selectedPlanVersion?.description || "");
    setIsPlanVersionEditOpen(false);
  }, [selectedPlanVersion]);

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

  const loadCalendarBackgrounds = useCallback((operations) => {
    if (!Array.isArray(operations) || operations.length === 0) {
      setCalendarBackgrounds([]);
      return;
    }

    const starts = operations.map((op) => Number(op.start)).filter(Number.isFinite);
    const ends = operations.map((op) => Number(op.end)).filter(Number.isFinite);

    if (starts.length === 0 || ends.length === 0) {
      setCalendarBackgrounds([]);
      return;
    }

    const fromMinute = Math.max(Math.min(...starts) - 240, 0);
    const toMinute = Math.max(...ends) + 240;

    fetch(
      `http://127.0.0.1:8000/calendar/non_working_intervals?from_minute=${fromMinute}&to_minute=${toMinute}`
    )
      .then((res) => res.json())
      .then((data) => {
        setCalendarBackgrounds(Array.isArray(data) ? data : []);
      })
      .catch(() => {
        setCalendarBackgrounds([]);
      });
  }, []);

  const handleSelectGanttOperation = useCallback(
    (operation) => {
      if (!isActiveSelectedPlan) {
        setSelectedOrderItemId(null);
        return;
      }

      if (!operation?.order_item_id) {
        setSelectedOrderItemId(null);
        return;
      }

      setSelectedOrderItemId((current) =>
        String(current) === String(operation.order_item_id)
          ? null
          : operation.order_item_id
      );
    },
    [isActiveSelectedPlan]
  );

  const handleClearGanttSelection = useCallback(() => {
    setSelectedOrderItemId(null);
  }, []);

  useEffect(() => {
    if (!isActiveSelectedPlan) {
      setSelectedOrderItemId(null);
    }
  }, [isActiveSelectedPlan]);

  const loadChangeLog = useCallback((planVersionId = selectedPlanVersionId) => {
    const params = new URLSearchParams({ limit: String(HISTORY_LIMIT) });

    if (planVersionId) {
      params.set("plan_version_id", planVersionId);
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
    return fetch("http://127.0.0.1:8000/plan_versions/active")
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
    return fetch("http://127.0.0.1:8000/plan_versions")
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

          localStorage.removeItem(SELECTED_PLAN_VERSION_STORAGE_KEY);
          return "";
        });
      })
      .catch(() => {});
  }, []);

  const loadMesScheduleRuns = useCallback(() => {
    const url = showHiddenMesRuns
      ? "http://127.0.0.1:8000/mes/schedule_runs?include_hidden=true"
      : "http://127.0.0.1:8000/mes/schedule_runs";

    return fetch(url)
      .then((res) => res.json())
      .then((data) => setMesScheduleRuns(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, [showHiddenMesRuns]);

  const loadMesWorkshops = useCallback(() => {
    return fetch("http://127.0.0.1:8000/mes/workshops")
      .then((res) => res.json())
      .then((data) => setMesWorkshops(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, []);

  const loadMesWorkplaceOperations = useCallback(async () => {
    setIsMesWorkplaceLoading(true);

    try {
      const params = new URLSearchParams();

      if (selectedMesWorkshopId) {
        params.set("workshop_id", selectedMesWorkshopId);
      }

      const res = await fetch(
        `http://127.0.0.1:8000/mes/workplace_operations?${params.toString()}`
      );
      const data = await res.json();

      if (!res.ok) {
        const detail = data?.detail?.message || data?.detail;
        alert(
          "Не удалось загрузить рабочее место мастера: " +
            (detail || JSON.stringify(data))
        );
        return;
      }

      setMesWorkplaceOperations(Array.isArray(data) ? data : []);
    } catch (error) {
      console.error("Ошибка загрузки рабочего места мастера:", error);
      alert("Не удалось загрузить рабочее место мастера");
    } finally {
      setIsMesWorkplaceLoading(false);
    }
  }, [selectedMesWorkshopId]);

  useEffect(() => {
    loadFreezeHorizon();
    loadActivePlanVersion();
    loadPlanVersions();
    loadMachines();
    loadMesScheduleRuns();
    loadMesWorkshops();
  }, [
    loadFreezeHorizon,
    loadActivePlanVersion,
    loadPlanVersions,
    loadMachines,
    loadMesScheduleRuns,
    loadMesWorkshops,
  ]);

  useEffect(() => {
    loadOperations();
    loadChangeLog();
  }, [loadOperations, loadChangeLog]);

  useEffect(() => {
    loadCalendarBackgrounds(ops);
  }, [ops, loadCalendarBackgrounds]);

  useEffect(() => {
    if (currentScreen === "mes") {
      loadMesScheduleRuns();
      loadMesWorkshops();
    }
  }, [currentScreen, loadMesScheduleRuns, loadMesWorkshops]);

  useEffect(() => {
    setSelectedMesOperationForReports(null);
    setSelectedMesOperationReports([]);
    setSelectedMesOperationCard(null);
    setMesOperationCardReports([]);
  }, [selectedMesWorkshopId]);

  useEffect(() => {
    if (currentScreen === "mes" && mesScreen === "workplace") {
      loadMesWorkplaceOperations();
    }
  }, [currentScreen, mesScreen, loadMesWorkplaceOperations]);

  useEffect(() => {
    if (!selectedMesRun) {
      return;
    }

    const selectedRunIsVisible = mesScheduleRuns.some(
      (run) => String(run.id) === String(selectedMesRun.id)
    );

    if (!selectedRunIsVisible) {
      setSelectedMesRun(null);
      setSelectedMesRunOperations([]);
    }
  }, [mesScheduleRuns, selectedMesRun]);

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
      if (msg.type === "plan_versions_updated") {
        setPlanDiff(null);
        setShowPlanDiff(false);
        setPlanValidation(null);
        setShowPlanValidation(false);
        setShowActiveOverlay(false);
        setActiveOverlayOps([]);

        loadActivePlanVersion();
        loadPlanVersions();

        if (msg.data?.active_plan_version_id) {
          setSelectedPlanVersionId("");
          localStorage.removeItem(SELECTED_PLAN_VERSION_STORAGE_KEY);
          loadOperations("");
          loadChangeLog("");
          return;
        }

        if (
          msg.data?.deleted_plan_version_id &&
          String(msg.data.deleted_plan_version_id) === String(selectedPlanVersionId)
        ) {
          setPlanDiff(null);
          setShowPlanDiff(false);
          setPlanValidation(null);
          setShowPlanValidation(false);
          setShowActiveOverlay(false);
          setActiveOverlayOps([]);
          setSelectedPlanVersionId("");
          localStorage.removeItem(SELECTED_PLAN_VERSION_STORAGE_KEY);
          loadOperations("");
          loadChangeLog("");
        }
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
  }, [
    loadActivePlanVersion,
    loadChangeLog,
    loadOperations,
    loadPlanVersions,
    selectedPlanVersionId,
  ]);

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

  const handleValidatePlan = useCallback(async () => {
    if (showPlanValidation) {
      setShowPlanValidation(false);
      setPlanValidation(null);
      return;
    }

    const validationPlanVersionId = selectedPlanVersionId || activePlanVersion?.id;

    if (!validationPlanVersionId) {
      alert("Выберите версию плана для проверки");
      return;
    }

    setIsPlanValidationLoading(true);

    try {
      const res = await fetch(
        `http://127.0.0.1:8000/plan_versions/${validationPlanVersionId}/validate`
      );

      const data = await res.json();

      if (!res.ok) {
        alert(
          "Не удалось проверить план: " +
            (data?.detail || JSON.stringify(data))
        );
        return;
      }

      setPlanValidation(data);
      setShowPlanValidation(true);
    } catch (error) {
      console.error("Ошибка проверки плана:", error);
      alert("Не удалось проверить план");
    } finally {
      setIsPlanValidationLoading(false);
    }
  }, [selectedPlanVersionId, activePlanVersion, showPlanValidation]);

  const loadActiveOverlayOps = useCallback(async () => {
    try {
      const activeId = activePlanVersion?.id;

      if (!activeId) {
        alert("Активная версия плана не загружена");
        return false;
      }

      const res = await fetch(
        `http://127.0.0.1:8000/operations?plan_version_id=${activeId}`
      );

      const data = await res.json();

      if (!res.ok) {
        alert(
          "Не удалось загрузить активный план для наложения: " +
            (data?.detail || JSON.stringify(data))
        );
        return false;
      }

      setActiveOverlayOps(Array.isArray(data) ? data : []);
      return true;
    } catch (error) {
      console.error("Ошибка загрузки активного плана для наложения:", error);
      alert("Не удалось загрузить активный план для наложения");
      return false;
    }
  }, [activePlanVersion]);

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
            setPlanValidation(null);
            setShowPlanValidation(false);

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
        setPlanValidation(null);
        setShowPlanValidation(false);
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
      setPlanValidation(null);
      setShowPlanValidation(false);
      setShowActiveOverlay(false);
      setActiveOverlayOps([]);

      alert("Создана черновая копия активного плана");
    } catch (error) {
      console.error("Ошибка создания копии активного плана:", error);
      alert("Не удалось создать копию активного плана");
    }
  }, [loadPlanVersions]);

  const handleDeleteDraftPlan = useCallback(async () => {
    if (!selectedPlanVersionId || !canEditSelectedPlan) {
      alert("Отклонить можно только черновую версию плана");
      return;
    }

    const confirmed = window.confirm(
      "Отклонить выбранный черновик? Все операции и история изменений этого черновика будут удалены."
    );

    if (!confirmed) {
      return;
    }

    try {
      const res = await fetch(
        `http://127.0.0.1:8000/plan_versions/${selectedPlanVersionId}`,
        {
          method: "DELETE",
        }
      );

      const data = await res.json();

      if (!res.ok) {
        alert(
          "Не удалось отклонить черновик: " +
            (data?.detail || JSON.stringify(data))
        );
        return;
      }

      setPlanDiff(null);
      setShowPlanDiff(false);
      setPlanValidation(null);
      setShowPlanValidation(false);
      setShowActiveOverlay(false);
      setActiveOverlayOps([]);
      setSelectedPlanVersionId("");
      localStorage.removeItem(SELECTED_PLAN_VERSION_STORAGE_KEY);

      await loadPlanVersions();
      loadOperations("");
      loadChangeLog("");

      alert("Черновик отклонён");
    } catch (error) {
      console.error("Ошибка отклонения черновика:", error);
      alert("Не удалось отклонить черновик");
    }
  }, [
    selectedPlanVersionId,
    canEditSelectedPlan,
    loadPlanVersions,
    loadOperations,
    loadChangeLog,
  ]);

  const handleApproveDraftPlan = useCallback(async () => {
    if (!selectedPlanVersionId || !canEditSelectedPlan) {
      alert("Принять можно только черновую версию плана");
      return;
    }

    const confirmed = window.confirm(
      "Принять выбранный черновик как новый активный план? Старый активный план будет архивирован."
    );

    if (!confirmed) {
      return;
    }

    try {
      const res = await fetch(
        `http://127.0.0.1:8000/plan_versions/${selectedPlanVersionId}/approve`,
        {
          method: "POST",
        }
      );

      const data = await res.json();

      if (!res.ok) {
        const detail = data?.detail;

        if (detail?.errors) {
          setPlanValidation({
            status: "error",
            is_valid: false,
            errors: detail.errors,
            warnings: [],
            summary: detail.summary || {},
          });
          setShowPlanValidation(true);
          alert(detail.message || "Нельзя принять черновик: план содержит ошибки");
          return;
        }

        alert(
          "Не удалось принять черновик: " +
            (data?.detail || JSON.stringify(data))
        );
        return;
      }

      setPlanDiff(null);
      setShowPlanDiff(false);
      setPlanValidation(null);
      setShowPlanValidation(false);
      setShowActiveOverlay(false);
      setActiveOverlayOps([]);
      setSelectedPlanVersionId("");
      localStorage.removeItem(SELECTED_PLAN_VERSION_STORAGE_KEY);

      await loadActivePlanVersion();
      await loadPlanVersions();
      loadOperations("");
      loadChangeLog("");

      alert("Черновик принят как новый активный план");
    } catch (error) {
      console.error("Ошибка принятия черновика:", error);
      alert("Не удалось принять черновик");
    }
  }, [
    selectedPlanVersionId,
    canEditSelectedPlan,
    loadActivePlanVersion,
    loadPlanVersions,
    loadOperations,
    loadChangeLog,
  ]);

  const handleSavePlanVersionInfo = useCallback(async () => {
    if (!selectedPlanVersionId || !canEditSelectedPlanDescription) {
      alert("Описание можно редактировать только у существующей версии плана");
      return;
    }

    const description = planVersionDescriptionInput.trim();
    const payload = {
      description,
    };

    if (canEditSelectedPlanName) {
      const name = planVersionNameInput.trim();

      if (!name) {
        alert("Название версии плана не должно быть пустым");
        return;
      }

      payload.name = name;
    }

    setIsPlanVersionSaving(true);

    try {
      const res = await fetch(
        `http://127.0.0.1:8000/plan_versions/${selectedPlanVersionId}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );

      const data = await res.json();

      if (!res.ok) {
        alert(
          "Не удалось обновить версию плана: " +
            (data?.detail || JSON.stringify(data))
        );
        return;
      }

      await loadPlanVersions();
      await loadActivePlanVersion();

      setIsPlanVersionEditOpen(false);
      alert("Версия плана обновлена");
    } catch (error) {
      console.error("Ошибка обновления версии плана:", error);
      alert("Не удалось обновить версию плана");
    } finally {
      setIsPlanVersionSaving(false);
    }
  }, [
    selectedPlanVersionId,
    canEditSelectedPlanDescription,
    canEditSelectedPlanName,
    planVersionNameInput,
    planVersionDescriptionInput,
    loadPlanVersions,
    loadActivePlanVersion,
  ]);

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

  const handleCreateMesRun = useCallback(
    async (period) => {
      const description =
        period === "today"
          ? "Производственное задание на сегодня"
          : "Производственное задание на завтра";

      try {
        const res = await fetch(
          "http://127.0.0.1:8000/mes/schedule_runs/from_active",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ period, description }),
          }
        );

        const data = await res.json();

        if (!res.ok) {
          const detail = data?.detail?.message || data?.detail;
          alert(
            "Не удалось создать производственное задание: " +
              (detail || JSON.stringify(data))
          );
          return;
        }

        await loadMesScheduleRuns();
        await loadMesWorkplaceOperations();
        loadOperations();
        alert("Производственное задание создано");
      } catch (error) {
        console.error("Ошибка создания производственного задания:", error);
        alert("Не удалось создать производственное задание");
      }
    },
    [loadMesScheduleRuns, loadMesWorkplaceOperations, loadOperations]
  );

  const handleOpenMesRun = useCallback(async (runId, options = {}) => {
    try {
      const res = await fetch(
        `http://127.0.0.1:8000/mes/schedule_runs/${runId}`
      );
      const data = await res.json();

      if (!res.ok) {
        const detail = data?.detail?.message || data?.detail;
        alert(
          "Не удалось открыть производственное задание: " +
            (detail || JSON.stringify(data))
        );
        return;
      }

      setSelectedMesRun(data.run);
      setSelectedMesRunOperations(
        Array.isArray(data.operations) ? data.operations : []
      );
      if (!options.keepReports) {
        setSelectedMesOperationForReports(null);
        setSelectedMesOperationReports([]);
        setSelectedMesOperationCard(null);
        setMesOperationCardReports([]);
      }
    } catch (error) {
      console.error("Ошибка открытия производственного задания:", error);
      alert("Не удалось открыть производственное задание");
    }
  }, []);

  const handleReleaseMesRun = useCallback(
    async (runId) => {
      if (!window.confirm("Выпустить производственное задание в производство?")) {
        return;
      }

      try {
        const res = await fetch(
          `http://127.0.0.1:8000/mes/schedule_runs/${runId}/release`,
          { method: "POST" }
        );

        const data = await res.json();

        if (!res.ok) {
          const detail = data?.detail?.message || data?.detail;
          alert(
            "Не удалось выпустить производственное задание: " +
              (detail || JSON.stringify(data))
          );
          return;
        }

        await loadMesScheduleRuns();
        if (selectedMesRun?.id === runId) {
          await handleOpenMesRun(runId);
        }

        await loadMesWorkplaceOperations();
        loadOperations();
        alert("Производственное задание выпущено в производство");
      } catch (error) {
        console.error("Ошибка выпуска производственного задания:", error);
        alert("Не удалось выпустить производственное задание");
      }
    },
    [
      handleOpenMesRun,
      loadMesScheduleRuns,
      loadMesWorkplaceOperations,
      loadOperations,
      selectedMesRun,
    ]
  );

  const handleCancelMesRun = useCallback(
    async (runId) => {
      if (!window.confirm("Отменить производственное задание?")) {
        return;
      }

      try {
        const res = await fetch(
          `http://127.0.0.1:8000/mes/schedule_runs/${runId}/cancel`,
          { method: "POST" }
        );

        const data = await res.json();

        if (!res.ok) {
          const detail = data?.detail?.message || data?.detail;
          alert(
            "Не удалось отменить производственное задание: " +
              (detail || JSON.stringify(data))
          );
          return;
        }

        await loadMesScheduleRuns();
        if (selectedMesRun?.id === runId) {
          await handleOpenMesRun(runId);
        }

        await loadMesWorkplaceOperations();
        loadOperations();
        alert("Производственное задание отменено");
      } catch (error) {
        console.error("Ошибка отмены производственного задания:", error);
        alert("Не удалось отменить производственное задание");
      }
    },
    [
      handleOpenMesRun,
      loadMesScheduleRuns,
      loadMesWorkplaceOperations,
      loadOperations,
      selectedMesRun,
    ]
  );

  const handleHideMesRun = useCallback(
    async (runId) => {
      if (!window.confirm("Убрать производственное задание из рабочего списка?")) {
        return;
      }

      try {
        const res = await fetch(
          `http://127.0.0.1:8000/mes/schedule_runs/${runId}/hide`,
          { method: "POST" }
        );

        const data = await res.json();

        if (!res.ok) {
          const detail = data?.detail?.message || data?.detail;
          alert(
            "Не удалось убрать производственное задание из списка: " +
              (detail || JSON.stringify(data))
          );
          return;
        }

        if (String(selectedMesRun?.id) === String(runId)) {
          setSelectedMesRun(null);
          setSelectedMesRunOperations([]);
          setSelectedMesOperationForReports(null);
          setSelectedMesOperationReports([]);
          setSelectedMesOperationCard(null);
          setMesOperationCardReports([]);
        }

        await loadMesScheduleRuns();
        await loadMesWorkplaceOperations();
        loadOperations();
        alert("Производственное задание убрано из списка");
      } catch (error) {
        console.error("Ошибка удаления производственного задания из списка:", error);
        alert("Не удалось убрать производственное задание из списка");
      }
    },
    [
      loadMesScheduleRuns,
      loadMesWorkplaceOperations,
      loadOperations,
      selectedMesRun,
    ]
  );

  const handleShowMesRun = useCallback(
    async (runId) => {
      try {
        const res = await fetch(
          `http://127.0.0.1:8000/mes/schedule_runs/${runId}/show`,
          { method: "POST" }
        );

        const data = await res.json();

        if (!res.ok) {
          const detail = data?.detail?.message || data?.detail;
          alert(
            "Не удалось вернуть производственное задание в список: " +
              (detail || JSON.stringify(data))
          );
          return;
        }

        await loadMesScheduleRuns();
        await loadMesWorkplaceOperations();
        loadOperations();
        alert("Производственное задание добавлено в список");
      } catch (error) {
        console.error("Ошибка возврата производственного задания в список:", error);
        alert("Не удалось вернуть производственное задание в список");
      }
    },
    [loadMesScheduleRuns, loadMesWorkplaceOperations, loadOperations]
  );

  const handleExcludeMesOrderItem = useCallback(
    async (runId, orderItemId) => {
      if (!window.confirm("Исключить позицию из производственного задания?")) {
        return;
      }

      try {
        const res = await fetch(
          `http://127.0.0.1:8000/mes/schedule_runs/${runId}/order_items/${orderItemId}/exclude`,
          { method: "POST" }
        );

        const data = await res.json();

        if (!res.ok) {
          const detail = data?.detail?.message || data?.detail;
          alert(
            "Не удалось исключить позицию из задания: " +
              (detail || JSON.stringify(data))
          );
          return;
        }

        await handleOpenMesRun(runId);
        await loadMesScheduleRuns();
        await loadMesWorkplaceOperations();
        loadOperations();
      } catch (error) {
        console.error("Ошибка исключения позиции из задания:", error);
        alert("Не удалось исключить позицию из задания");
      }
    },
    [
      handleOpenMesRun,
      loadMesScheduleRuns,
      loadMesWorkplaceOperations,
      loadOperations,
    ]
  );

  const handleIncludeMesOrderItem = useCallback(
    async (runId, orderItemId) => {
      if (!window.confirm("Вернуть позицию в производственное задание?")) {
        return;
      }

      try {
        const res = await fetch(
          `http://127.0.0.1:8000/mes/schedule_runs/${runId}/order_items/${orderItemId}/include`,
          { method: "POST" }
        );

        const data = await res.json();

        if (!res.ok) {
          const detail = data?.detail?.message || data?.detail;
          alert(
            "Не удалось вернуть позицию в задание: " +
              (detail || JSON.stringify(data))
          );
          return;
        }

        await handleOpenMesRun(runId);
        await loadMesScheduleRuns();
        await loadMesWorkplaceOperations();
        loadOperations();
      } catch (error) {
        console.error("Ошибка возврата позиции в задание:", error);
        alert("Не удалось вернуть позицию в задание");
      }
    },
    [
      handleOpenMesRun,
      loadMesScheduleRuns,
      loadMesWorkplaceOperations,
      loadOperations,
    ]
  );

  const handleStartMesOperation = useCallback(
    async (operationId) => {
      try {
        const res = await fetch(
          `http://127.0.0.1:8000/mes/schedule_operations/${operationId}/start`,
          { method: "POST" }
        );

        const data = await res.json();

        if (!res.ok) {
          const detail = data?.detail?.message || data?.detail;
          alert(
            "Не удалось начать операцию: " +
              (detail || JSON.stringify(data))
          );
          return;
        }

        if (selectedMesRun?.id) {
          await handleOpenMesRun(selectedMesRun.id);
        }

        if (
          selectedMesOperationCard &&
          String(selectedMesOperationCard.id) === String(operationId) &&
          data.operation
        ) {
          setSelectedMesOperationCard(data.operation);
        }

        await loadMesScheduleRuns();
        await loadMesWorkplaceOperations();
        loadOperations();
      } catch (error) {
        console.error("Ошибка старта операции:", error);
        alert("Не удалось начать операцию");
      }
    },
    [
      handleOpenMesRun,
      loadMesScheduleRuns,
      loadMesWorkplaceOperations,
      loadOperations,
      selectedMesOperationCard,
      selectedMesRun,
    ]
  );

  const handleOpenMesOperationReports = useCallback(async (operation) => {
    setSelectedMesOperationForReports(operation);
    setSelectedMesOperationReports([]);
    setIsReportsLoading(true);

    try {
      const res = await fetch(
        `http://127.0.0.1:8000/mes/schedule_operations/${operation.id}/reports`
      );
      const data = await res.json();

      if (!res.ok) {
        const detail = data?.detail?.message || data?.detail;
        alert(
          "Не удалось загрузить журнал выполнения операции: " +
            (detail || JSON.stringify(data))
        );
        return;
      }

      const reports = Array.isArray(data)
        ? data
        : Array.isArray(data.reports)
        ? data.reports
        : [];

      setSelectedMesOperationReports(reports);
    } catch (error) {
      console.error("Ошибка загрузки журнала выполнения операции:", error);
      alert("Не удалось загрузить журнал выполнения операции");
    } finally {
      setIsReportsLoading(false);
    }
  }, []);

  const handleCloseMesOperationReports = useCallback(() => {
    setSelectedMesOperationForReports(null);
    setSelectedMesOperationReports([]);
  }, []);

  const loadMesOperationCardReports = useCallback(async (operation) => {
    if (!operation?.id) {
      setMesOperationCardReports([]);
      return;
    }

    setIsMesOperationCardReportsLoading(true);

    try {
      const res = await fetch(
        `http://127.0.0.1:8000/mes/schedule_operations/${operation.id}/reports`
      );
      const data = await res.json();

      if (!res.ok) {
        const detail = data?.detail?.message || data?.detail;
        alert(
          "Не удалось загрузить журнал выполнения операции: " +
            (detail || JSON.stringify(data))
        );
        return;
      }

      const reports = Array.isArray(data)
        ? data
        : Array.isArray(data.reports)
        ? data.reports
        : [];
      setMesOperationCardReports(reports);
    } catch (error) {
      console.error("Ошибка загрузки журнала карточки операции:", error);
      alert("Не удалось загрузить журнал выполнения операции");
    } finally {
      setIsMesOperationCardReportsLoading(false);
    }
  }, []);

  const handleOpenMesOperationCard = useCallback((operation) => {
    setSelectedMesOperationCard(operation);
    setMesOperationCardTab("fact");
    setMesOperationCardReports([]);
  }, []);

  const handleCloseMesOperationCard = useCallback(() => {
    setSelectedMesOperationCard(null);
    setMesOperationCardReports([]);
  }, []);

  useEffect(() => {
    if (selectedMesOperationCard && mesOperationCardTab === "journal") {
      loadMesOperationCardReports(selectedMesOperationCard);
    }
  }, [
    loadMesOperationCardReports,
    mesOperationCardTab,
    selectedMesOperationCard,
  ]);

  const handleLoadShiftReport = useCallback(async () => {
    const day = Number(shiftReportDay);
    const shift = Number(shiftReportShift);

    if (!Number.isInteger(day) || day < 1) {
      alert("День сменного отчёта должен быть целым числом больше нуля");
      return;
    }

    if (![1, 2].includes(shift)) {
      alert("Номер смены должен быть 1 или 2");
      return;
    }

    setIsShiftReportLoading(true);

    try {
      const params = new URLSearchParams({
        day: String(day),
        shift: String(shift),
      });

      if (selectedMesWorkshopId) {
        params.set("workshop_id", selectedMesWorkshopId);
      }

      const res = await fetch(
        `http://127.0.0.1:8000/mes/shift_report?${params.toString()}`
      );
      const data = await res.json();

      if (!res.ok) {
        const detail = data?.detail?.message || data?.detail;
        alert(
          "Не удалось загрузить сменный отчёт: " +
            (detail || JSON.stringify(data))
        );
        return;
      }

      setShiftReport(data);
    } catch (error) {
      console.error("Ошибка загрузки сменного отчёта:", error);
      alert("Не удалось загрузить сменный отчёт");
    } finally {
      setIsShiftReportLoading(false);
    }
  }, [selectedMesWorkshopId, shiftReportDay, shiftReportShift]);

  const handleCompleteMesOperation = useCallback(
    async (operation) => {
      const goodRaw = window.prompt("Введите годное количество", "0");
      if (goodRaw === null) {
        return;
      }

      const defectRaw = window.prompt("Введите количество брака", "0");
      if (defectRaw === null) {
        return;
      }

      const comment = window.prompt("Комментарий", "") || "";
      const goodQuantity = Number(goodRaw);
      const defectQuantity = Number(defectRaw);

      if (
        !Number.isInteger(goodQuantity) ||
        !Number.isInteger(defectQuantity) ||
        goodQuantity < 0 ||
        defectQuantity < 0 ||
        goodQuantity + defectQuantity <= 0
      ) {
        alert(
          "Количество должно быть целым неотрицательным числом, сумма должна быть больше нуля"
        );
        return;
      }

      const availableQuantity = Number(operation.available_quantity || 0);
      const reportedQuantity = goodQuantity + defectQuantity;

      if (reportedQuantity > availableQuantity) {
        alert(
          `Нельзя зафиксировать выполнение больше доступного количества. Доступно: ${availableQuantity}`
        );
        return;
      }

      try {
        const shouldRefreshReports =
          selectedMesOperationForReports &&
          String(selectedMesOperationForReports.id) === String(operation.id);
        const res = await fetch(
          `http://127.0.0.1:8000/mes/schedule_operations/${operation.id}/complete`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              good_quantity: goodQuantity,
              defect_quantity: defectQuantity,
              comment,
            }),
          }
        );

        const data = await res.json();

        if (!res.ok) {
          const detail = data?.detail?.message || data?.detail;
          alert(
            "Не удалось зафиксировать выполнение: " +
              (detail || JSON.stringify(data))
          );
          return;
        }

        if (selectedMesRun?.id) {
          await handleOpenMesRun(selectedMesRun.id);
        }

        await loadMesScheduleRuns();
        await loadMesWorkplaceOperations();
        loadOperations();

        if (
          selectedMesOperationCard &&
          String(selectedMesOperationCard.id) === String(operation.id) &&
          data.operation
        ) {
          setSelectedMesOperationCard(data.operation);
        }

        if (shouldRefreshReports) {
          await handleOpenMesOperationReports(data.operation || operation);
        }
        if (
          selectedMesOperationCard &&
          String(selectedMesOperationCard.id) === String(operation.id) &&
          mesOperationCardTab === "journal"
        ) {
          await loadMesOperationCardReports(data.operation || operation);
        }
      } catch (error) {
        console.error("Ошибка фиксации выполнения:", error);
        alert("Не удалось зафиксировать выполнение");
      }
    },
    [
      handleOpenMesOperationReports,
      handleOpenMesRun,
      loadMesOperationCardReports,
      loadMesScheduleRuns,
      loadMesWorkplaceOperations,
      loadOperations,
      mesOperationCardTab,
      selectedMesOperationCard,
      selectedMesOperationForReports,
      selectedMesRun,
    ]
  );

  const handleCorrectMesOperationReport = useCallback(
    async (report) => {
      const reportId = report.id ?? report.report_id;

      if (!reportId) {
        alert("Не найден ID записи журнала выполнения");
        return;
      }

      const currentGood = Number(report.good_quantity || 0);
      const currentDefect = Number(report.defect_quantity || 0);

      const goodRaw = window.prompt(
        "Введите новое годное количество для этой записи",
        String(currentGood)
      );
      if (goodRaw === null) {
        return;
      }

      const defectRaw = window.prompt(
        "Введите новое количество брака для этой записи",
        String(currentDefect)
      );
      if (defectRaw === null) {
        return;
      }

      const reason = window.prompt("Причина исправления", "");
      if (reason === null) {
        return;
      }

      const goodQuantity = Number(goodRaw);
      const defectQuantity = Number(defectRaw);

      if (
        !Number.isInteger(goodQuantity) ||
        !Number.isInteger(defectQuantity) ||
        goodQuantity < 0 ||
        defectQuantity < 0
      ) {
        alert("Количество должно быть целым неотрицательным числом");
        return;
      }

      if (!String(reason || "").trim()) {
        alert("Укажите причину исправления");
        return;
      }

      try {
        const operationForReports = selectedMesOperationForReports;
        const res = await fetch(
          `http://127.0.0.1:8000/mes/operation_reports/${reportId}/correct`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              good_quantity: goodQuantity,
              defect_quantity: defectQuantity,
              reason: reason.trim(),
            }),
          }
        );

        const data = await res.json();

        if (!res.ok) {
          const detail = data?.detail?.message || data?.detail;
          alert(
            "Не удалось исправить запись выполнения: " +
              (detail || JSON.stringify(data))
          );
          return;
        }

        if (data.report) {
          setSelectedMesOperationReports((current) => [
            ...current,
            data.report,
          ]);
        }

        if (data.operation) {
          setSelectedMesOperationForReports(data.operation);
          if (
            selectedMesOperationCard &&
            String(selectedMesOperationCard.id) === String(data.operation.id)
          ) {
            setSelectedMesOperationCard(data.operation);
          }
        }

        if (selectedMesRun?.id) {
          await handleOpenMesRun(selectedMesRun.id, { keepReports: true });
        }

        await loadMesScheduleRuns();
        await loadMesWorkplaceOperations();
        loadOperations();

        if (operationForReports?.id) {
          await handleOpenMesOperationReports(
            data.operation || operationForReports
          );
        }
        if (
          selectedMesOperationCard &&
          mesOperationCardTab === "journal"
        ) {
          await loadMesOperationCardReports(
            data.operation || selectedMesOperationCard
          );
        }

        alert(
          "Исправление сохранено: создана корректирующая запись, исходная запись журнала не изменялась"
        );
      } catch (error) {
        console.error("Ошибка исправления записи выполнения:", error);
        alert("Не удалось исправить запись выполнения");
      }
    },
    [
      handleOpenMesOperationReports,
      handleOpenMesRun,
      loadMesOperationCardReports,
      loadMesScheduleRuns,
      loadMesWorkplaceOperations,
      loadOperations,
      mesOperationCardTab,
      selectedMesOperationCard,
      selectedMesOperationForReports,
      selectedMesRun,
    ]
  );

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
  const visibleSelectedMesRunOperations = selectedMesWorkshopId
    ? selectedMesRunOperations.filter(
        (operation) =>
          String(operation.workshop_id || "") === String(selectedMesWorkshopId)
      )
    : selectedMesRunOperations;
  const mesOperationsByGroup = Array.from(
    visibleSelectedMesRunOperations.reduce((map, operation) => {
      const key = operation.machine_group_id || "UNKNOWN";

      if (!map.has(key)) {
        map.set(key, []);
      }

      map.get(key).push(operation);
      return map;
    }, new Map())
  )
    .sort(([groupA], [groupB]) => {
      return (
        (MACHINE_GROUP_ORDER[groupA] ?? 999) -
          (MACHINE_GROUP_ORDER[groupB] ?? 999) ||
        String(groupA).localeCompare(String(groupB), "ru")
      );
    })
    .map(([groupId, operations]) => [
      groupId,
      [...operations].sort((a, b) => {
        return (
          Number(a.planned_start_time ?? 0) -
            Number(b.planned_start_time ?? 0) ||
          String(a.machine_id || "").localeCompare(
            String(b.machine_id || ""),
            "ru"
          ) ||
          Number(a.operation_id ?? 0) - Number(b.operation_id ?? 0)
        );
      }),
    ]);

  const mesWorkplaceGroups = [
    {
      key: "in_work",
      title: "В работе",
      operations: mesWorkplaceOperations.filter(
        (operation) => operation.status === "in_work"
      ),
    },
    {
      key: "released",
      title: "К выполнению",
      operations: mesWorkplaceOperations.filter(
        (operation) => operation.status === "released"
      ),
    },
    {
      key: "partially_completed",
      title: "Частично выполнено",
      operations: mesWorkplaceOperations.filter(
        (operation) => operation.status === "partially_completed"
      ),
    },
  ];
  const hasMesWorkplaceOperations = mesWorkplaceGroups.some(
    (group) => group.operations.length > 0
  );
  const productionCardReports = mesOperationCardReports.filter(
    (report) => (report.report_type || "production") !== "correction"
  );
  const correctionCardReports = mesOperationCardReports.filter(
    (report) => report.report_type === "correction"
  );
  const orphanCorrectionCardReports = correctionCardReports.filter(
    (correction) =>
      !productionCardReports.some(
        (report) =>
          String(report.id ?? report.report_id) ===
          String(correction.corrected_report_id)
      )
  );

  return (
    <div style={{ padding: "20px" }}>
      <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
        <button
          onClick={() => setCurrentScreen("aps")}
          style={{
            padding: "8px 12px",
            fontWeight: currentScreen === "aps" ? "bold" : "normal",
            border:
              currentScreen === "aps" ? "2px solid #555" : "1px solid #ccc",
            background: currentScreen === "aps" ? "#f0f0f0" : "white",
            cursor: "pointer",
          }}
        >
          APS-планировщик
        </button>

        <button
          onClick={() => setCurrentScreen("mes")}
          style={{
            padding: "8px 12px",
            fontWeight: currentScreen === "mes" ? "bold" : "normal",
            border:
              currentScreen === "mes" ? "2px solid #555" : "1px solid #ccc",
            background: currentScreen === "mes" ? "#f0f0f0" : "white",
            cursor: "pointer",
          }}
        >
          MES-задания
        </button>
      </div>

      {currentScreen === "aps" && (
        <div>
      <div
        style={{
          display: "flex",
          gap: "8px",
          marginBottom: "10px",
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <div
          style={{
            display: "flex",
            gap: "8px",
            alignItems: "center",
            padding: "6px 8px",
            border: "1px solid #ccc",
          }}
        >
          <span>План:</span>

          <select
            value={selectedPlanVersionId}
            onChange={(e) => {
              const value = e.target.value;
              setSelectedPlanVersionId(value);
              setPlanDiff(null);
              setShowPlanDiff(false);
              setPlanValidation(null);
              setShowPlanValidation(false);
              setIsPlanVersionEditOpen(false);
              setShowActiveOverlay(false);
              setActiveOverlayOps([]);
              setSelectedOrderItemId(null);

              if (value) {
                localStorage.setItem(SELECTED_PLAN_VERSION_STORAGE_KEY, value);
              } else {
                localStorage.removeItem(SELECTED_PLAN_VERSION_STORAGE_KEY);
              }
            }}
            style={{ padding: "6px", minWidth: "220px" }}
          >
            <option value="">Активный план</option>
            {planVersions.map((version) => (
              <option key={version.id} value={String(version.id)}>
                #{version.id} {version.name || "Без названия"} (
                {PLAN_VERSION_STATUS_LABELS[version.status] || version.status})
              </option>
            ))}
          </select>

          <span
            title={buildPlanVersionTitle(displayedPlanVersion)}
            style={{
              color: "#666",
              maxWidth: "180px",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {displayedPlanVersion
              ? `${
                  PLAN_VERSION_STATUS_LABELS[displayedPlanVersion.status] ||
                  displayedPlanVersion.status ||
                  ""
                }${
                  displayedPlanVersion.approved_at ? " · принят" : ""
                }`
              : ""}
          </span>

          <button
            onClick={() => setIsPlanVersionEditOpen((prev) => !prev)}
            disabled={!displayedPlanVersion || !canEditSelectedPlanDescription}
            title="Редактировать описание версии"
            style={{
              padding: "4px 7px",
              cursor:
                displayedPlanVersion && canEditSelectedPlanDescription
                  ? "pointer"
                  : "not-allowed",
            }}
          >
            ✎
          </button>

          <button
            onClick={handleCloneActivePlan}
            title="Создать черновую копию активного плана"
            style={{ padding: "6px 8px", cursor: "pointer" }}
          >
            Копия
          </button>

          <button
            onClick={handleValidatePlan}
            title="Проверить выбранную версию плана"
            disabled={
              isPlanValidationLoading ||
              (!selectedPlanVersionId && !activePlanVersion?.id)
            }
            style={{
              padding: "6px 8px",
              cursor:
                !isPlanValidationLoading &&
                (selectedPlanVersionId || activePlanVersion?.id)
                  ? "pointer"
                  : "not-allowed",
            }}
          >
            {isPlanValidationLoading
              ? "Проверка..."
              : showPlanValidation
              ? "Скрыть проверку"
              : "Проверить"}
          </button>

          <button
            onClick={loadPlanDiff}
            disabled={!canEditSelectedPlan || isPlanDiffLoading}
            title="Сравнить черновик с активным планом"
            style={{
              padding: "6px 8px",
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
              : "Сравнить"}
          </button>

          <button
            onClick={async () => {
              if (!canEditSelectedPlan) {
                alert(
                  "Наложение активного плана доступно только для черновой версии плана"
                );
                return;
              }

              if (showActiveOverlay) {
                setShowActiveOverlay(false);
                setActiveOverlayOps([]);
                return;
              }

              const loaded = await loadActiveOverlayOps();
              if (loaded) {
                setShowActiveOverlay(true);
              }
            }}
            disabled={!canEditSelectedPlan}
            title="Показать наложение активного плана"
            style={{
              padding: "6px 8px",
              cursor: canEditSelectedPlan ? "pointer" : "not-allowed",
            }}
          >
            {showActiveOverlay ? "Скрыть наложение" : "Наложение"}
          </button>

          <button
            onClick={handleDeleteDraftPlan}
            disabled={!canEditSelectedPlan}
            title="Отклонить выбранный черновик"
            style={{
              padding: "6px 8px",
              cursor: canEditSelectedPlan ? "pointer" : "not-allowed",
            }}
          >
            Отклонить
          </button>

          <button
            onClick={handleApproveDraftPlan}
            disabled={!canEditSelectedPlan}
            title="Принять выбранный черновик как новый активный план"
            style={{
              padding: "6px 8px",
              cursor: canEditSelectedPlan ? "pointer" : "not-allowed",
            }}
          >
            Принять
          </button>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          gap: "8px",
          marginBottom: "10px",
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <div
          style={{
            display: "flex",
            gap: "8px",
            alignItems: "center",
            padding: "6px 8px",
            border: "1px solid #ccc",
          }}
        >
          <span>Горизонт:</span>

          <input
            type="number"
            value={freezeInput}
            disabled={!canEditFreezeZone}
            onChange={(e) => setFreezeInput(e.target.value)}
            style={{ width: "76px", padding: "6px" }}
          />

          <span>мин.</span>

          <button
            onClick={handleSaveFreezeHorizon}
            disabled={!canEditFreezeZone}
            style={{
              padding: "6px 8px",
              cursor: canEditFreezeZone ? "pointer" : "not-allowed",
            }}
          >
            Сохранить
          </button>
        </div>

        <div
          style={{
            display: "flex",
            gap: "8px",
            alignItems: "center",
            padding: "6px 8px",
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

        <div
          style={{
            display: "flex",
            gap: "8px",
            alignItems: "center",
            padding: "6px 8px",
            border: "1px solid #ccc",
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
              padding: "6px 8px",
              cursor:
                latestRollbackChangeGroup && canEditSelectedPlan
                  ? "pointer"
                  : "not-allowed",
            }}
          >
            Откат
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
              padding: "6px 8px",
              cursor: "pointer",
            }}
          >
            {showHistory ? "Скрыть историю" : "История"}
          </button>
        </div>
      </div>

      {isPlanVersionEditOpen &&
        displayedPlanVersion &&
        canEditSelectedPlanDescription && (
          <div
            style={{
              display: "flex",
              gap: "8px",
              alignItems: "center",
              marginBottom: "12px",
              padding: "8px",
              border: "1px solid #ccc",
              background: "#fafafa",
              flexWrap: "wrap",
            }}
          >
            <span>
              {canEditSelectedPlanName
                ? "Редактирование сценария:"
                : "Редактирование описания:"}
            </span>

            {canEditSelectedPlanName && (
              <input
                type="text"
                value={planVersionNameInput}
                onChange={(e) => setPlanVersionNameInput(e.target.value)}
                placeholder="Название сценария"
                style={{ width: "240px", padding: "6px" }}
              />
            )}

            <input
              type="text"
              value={planVersionDescriptionInput}
              onChange={(e) => setPlanVersionDescriptionInput(e.target.value)}
              placeholder={
                canEditSelectedPlanName ? "Описание сценария" : "Описание версии"
              }
              style={{ width: "420px", padding: "6px" }}
            />

            <button
              onClick={async () => {
                await handleSavePlanVersionInfo();
                setIsPlanVersionEditOpen(false);
              }}
              disabled={isPlanVersionSaving}
              style={{
                padding: "6px 10px",
                cursor: isPlanVersionSaving ? "not-allowed" : "pointer",
              }}
            >
              {isPlanVersionSaving ? "Сохранение..." : "Сохранить"}
            </button>

            <button
              onClick={() => {
                setPlanVersionNameInput(displayedPlanVersion?.name || "");
                setPlanVersionDescriptionInput(
                  displayedPlanVersion?.description || ""
                );
                setIsPlanVersionEditOpen(false);
              }}
              disabled={isPlanVersionSaving}
              style={{
                padding: "6px 10px",
                cursor: isPlanVersionSaving ? "not-allowed" : "pointer",
              }}
            >
              Отмена
            </button>
          </div>
        )}

      {showPlanValidation && planValidation && (
        <div
          style={{
            marginBottom: "16px",
            border: "1px solid #ccc",
            padding: "12px",
            maxHeight: "320px",
            overflow: "auto",
          }}
        >
          <h3 style={{ marginTop: 0 }}>Проверка плана</h3>

          <div style={{ marginBottom: "8px" }}>
            {planValidation.is_valid
              ? "План корректен. Ошибок нет."
              : "План содержит ошибки."}
          </div>

          <div style={{ marginBottom: "12px" }}>
            Проверено операций: {planValidation.summary?.operations_checked ?? 0};{" "}
            Ошибки длительности: {planValidation.summary?.duration_errors ?? 0};{" "}
            Ошибки маршрута: {planValidation.summary?.route_buffer_errors ?? 0};{" "}
            Ошибки станков: {planValidation.summary?.machine_buffer_errors ?? 0};{" "}
            Ошибки календаря: {planValidation.summary?.calendar_errors ?? 0};{" "}
            Конфликты наладчиков:{" "}
            {planValidation.summary?.setup_team_conflicts ?? 0};{" "}
            Ошибки бригад наладчиков:{" "}
            {planValidation.summary?.missing_setup_team_links ?? 0};{" "}
            Ошибки замороженной зоны:{" "}
            {planValidation.summary?.frozen_zone_errors ?? 0}
          </div>

          {Array.isArray(planValidation.errors) &&
            planValidation.errors.length > 0 && (
              <table
                style={{
                  width: "100%",
                  borderCollapse: "collapse",
                  fontSize: "14px",
                }}
              >
                <thead>
                  <tr>
                    <th style={thStyle}>Тип</th>
                    <th style={thStyle}>Операция</th>
                    <th style={thStyle}>Сообщение</th>
                  </tr>
                </thead>
                <tbody>
                  {planValidation.errors.map((error, index) => (
                    <tr
                      key={`${error.type}-${error.operation_id || index}-${index}`}
                    >
                      <td style={tdStyle}>
                        {VALIDATION_ERROR_LABELS[error.type] || error.type}
                      </td>
                      <td style={tdStyle}>
                        {error.order_no || ""} {error.product_name || ""}{" "}
                        {error.operation_name || error.operation_id || ""}
                      </td>
                      <td style={tdStyle}>{error.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
      )}

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
            Сравнение черновика #{planDiff.draft_plan_version?.id} с активным планом #
            {planDiff.active_plan_version?.id}
          </h3>

          <div style={{ marginBottom: "16px" }}>
            <h4 style={{ margin: "0 0 8px" }}>Итоги плана</h4>
            <div>
              Окончание плана: активный план{" "}
              {planDiff.summary?.plan_finish_active ?? 0} → черновик{" "}
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
              просроченных заказов: активный план{" "}
              {planDiff.summary?.late_orders_active ?? 0} → черновик{" "}
              {planDiff.summary?.late_orders_draft ?? 0}
            </div>
            <div>
              Суммарное опоздание: активный план{" "}
              {planDiff.summary?.total_lateness_active ?? 0} → черновик{" "}
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
                    <th style={thStyle}>Окончание активного плана</th>
                    <th style={thStyle}>Окончание черновика</th>
                    <th style={thStyle}>Δ</th>
                    <th style={thStyle}>Просрочка активного плана</th>
                    <th style={thStyle}>Просрочка черновика</th>
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
                    <th style={thStyle}>Окончание активного плана</th>
                    <th style={thStyle}>Окончание черновика</th>
                    <th style={thStyle}>Δ окончания</th>
                    <th style={thStyle}>Занято активный план</th>
                    <th style={thStyle}>Занято черновик</th>
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
            <div>Отличий от активного плана нет</div>
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
        backgroundIntervals={calendarBackgrounds}
        activeOverlayData={showActiveOverlay ? filteredActiveOverlayOps : []}
        showActiveOverlay={showActiveOverlay}
        onMove={handleMove}
        freezeHorizonMinutes={freezeHorizonMinutes}
        canEdit={canEditSelectedPlan}
        enableOrderSelection={isActiveSelectedPlan}
        selectedOrderItemId={isActiveSelectedPlan ? selectedOrderItemId : null}
        onSelectOperation={handleSelectGanttOperation}
        onClearSelection={handleClearGanttSelection}
      />
      {isActiveSelectedPlan && selectedOrderItemSummary && (
        <div
          style={{
            marginTop: "10px",
            padding: "8px 10px",
            border: "1px solid #ccc",
            background: "#f7fbff",
            display: "flex",
            gap: "16px",
            alignItems: "center",
            flexWrap: "wrap",
            fontSize: "14px",
          }}
        >
          <strong>Выбранная позиция:</strong>
          <span>Заказ: {selectedOrderItemSummary.orderNo}</span>
          <span>Изделие: {selectedOrderItemSummary.productName}</span>
          <span>Количество: {selectedOrderItemSummary.quantity}</span>
          <span>Старт: {formatPlanMinute(selectedOrderItemSummary.start)}</span>
          <span>Финиш: {formatPlanMinute(selectedOrderItemSummary.end)}</span>
          <button
            onClick={handleClearGanttSelection}
            style={{ padding: "4px 8px", cursor: "pointer" }}
          >
            Сбросить выделение
          </button>
        </div>
      )}
        </div>
      )}

      {currentScreen === "mes" && (
      <div
        style={{
          marginBottom: "16px",
          border: "1px solid #ccc",
          padding: "12px",
          background: "#fafafa",
        }}
      >
        <div
          style={{
            display: "flex",
            gap: "8px",
            marginBottom: "12px",
            flexWrap: "wrap",
          }}
        >
          {[
            ["runs", "Производственные задания"],
            ["workplace", "Рабочее место мастера"],
            ["shiftReport", "Сменный отчёт"],
          ].map(([key, label]) => (
            <button
              key={key}
              onClick={() => setMesScreen(key)}
              style={{
                padding: "7px 10px",
                fontWeight: mesScreen === key ? "bold" : "normal",
                border:
                  mesScreen === key ? "2px solid #555" : "1px solid #ccc",
                background: mesScreen === key ? "#f0f0f0" : "white",
                cursor: "pointer",
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {mesScreen === "runs" && (
          <div>
        <div
          style={{
            display: "flex",
            gap: "8px",
            alignItems: "center",
            marginBottom: "10px",
            flexWrap: "wrap",
          }}
        >
          <h3 style={{ margin: 0 }}>Производственные задания</h3>

          <button
            onClick={() => handleCreateMesRun("today")}
            style={{ padding: "6px 8px", cursor: "pointer" }}
          >
            Создать на сегодня
          </button>

          <button
            onClick={() => handleCreateMesRun("tomorrow")}
            style={{ padding: "6px 8px", cursor: "pointer" }}
          >
            Создать на завтра
          </button>

          <label
            style={{
              display: "flex",
              gap: "6px",
              alignItems: "center",
              color: "#555",
            }}
          >
            <span>Участок:</span>
            <select
              value={selectedMesWorkshopId}
              onChange={(e) => setSelectedMesWorkshopId(e.target.value)}
              style={{ padding: "6px", minWidth: "180px" }}
            >
              <option value="">Все участки</option>
              {mesWorkshops.map((workshop) => (
                <option key={workshop.id} value={String(workshop.id)}>
                  {workshop.name}
                  {workshop.responsible_name
                    ? ` — ${workshop.responsible_name}`
                    : ""}
                </option>
              ))}
            </select>
          </label>

          <label
            style={{
              display: "flex",
              gap: "6px",
              alignItems: "center",
              color: "#555",
            }}
          >
            <input
              type="checkbox"
              checked={showHiddenMesRuns}
              onChange={(e) => setShowHiddenMesRuns(e.target.checked)}
            />
            Показать скрытые задания
          </label>
        </div>

        {mesScheduleRuns.length > 0 && (
          <div style={{ maxHeight: "240px", overflow: "auto" }}>
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
                  <th style={thStyle}>План</th>
                  <th style={thStyle}>Период</th>
                  <th style={thStyle}>Статус</th>
                  <th style={thStyle}>Операций</th>
                  <th style={thStyle}>Создано</th>
                  <th style={thStyle}>Действия</th>
                </tr>
              </thead>
              <tbody>
                {mesScheduleRuns.map((run) => (
                  <tr key={run.id}>
                    <td style={tdStyle}>#{run.id}</td>
                    <td style={tdStyle}>#{run.source_plan_version_id}</td>
                    <td style={tdStyle}>
                      {formatPlanInterval(run.start_minute, run.end_minute)}
                    </td>
                    <td style={tdStyle}>
                      {MES_RUN_STATUS_LABELS[run.status] || run.status}
                      {run.is_hidden ? " · скрыто" : ""}
                    </td>
                    <td style={tdStyle}>{run.operations_count ?? 0}</td>
                    <td style={tdStyle}>
                      {run.created_at
                        ? new Date(run.created_at).toLocaleString()
                        : ""}
                    </td>
                    <td style={tdStyle}>
                      <div
                        style={{
                          display: "flex",
                          gap: "6px",
                          flexWrap: "wrap",
                        }}
                      >
                        <button
                          onClick={() => handleOpenMesRun(run.id)}
                          style={{ padding: "4px 8px", cursor: "pointer" }}
                        >
                          Открыть
                        </button>

                        <button
                          onClick={() => handleReleaseMesRun(run.id)}
                          disabled={run.status !== "created"}
                          style={{
                            padding: "4px 8px",
                            cursor:
                              run.status === "created" ? "pointer" : "not-allowed",
                          }}
                        >
                          Выпустить
                        </button>

                        <button
                          onClick={() => handleCancelMesRun(run.id)}
                          disabled={run.status !== "created"}
                          style={{
                            padding: "4px 8px",
                            cursor:
                              run.status === "created" ? "pointer" : "not-allowed",
                          }}
                        >
                          Отменить
                        </button>

                        <button
                          onClick={() =>
                            run.is_hidden
                              ? handleShowMesRun(run.id)
                              : handleHideMesRun(run.id)
                          }
                          style={{
                            padding: "4px 8px",
                            cursor: "pointer",
                          }}
                        >
                          {run.is_hidden ? "Добавить в список" : "Убрать из списка"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {selectedMesRun && (
          <div style={{ marginTop: "12px" }}>
            <h4 style={{ margin: "0 0 8px" }}>
              Операции задания #{selectedMesRun.id}
            </h4>

            {selectedMesRun.description && (
              <div style={{ marginBottom: "8px", color: "#666" }}>
                {selectedMesRun.description}
              </div>
            )}

            {selectedMesRunOperations.length === 0 ? (
              <div>В задании нет операций</div>
            ) : visibleSelectedMesRunOperations.length === 0 ? (
              <div>Для выбранного участка операций в этом задании нет</div>
            ) : (
              <div style={{ maxHeight: "420px", overflow: "auto" }}>
                {mesOperationsByGroup.map(([groupId, operations]) => (
                  <div key={groupId} style={{ marginBottom: "14px" }}>
                    <h5
                      style={{
                        margin: "0 0 6px",
                        padding: "6px 8px",
                        background: "#eeeeee",
                      }}
                    >
                      {MACHINE_GROUP_LABELS[groupId] || "Группа оборудования"} /{" "}
                      {groupId}
                    </h5>

                    <table
                      style={{
                        width: "100%",
                        borderCollapse: "collapse",
                        fontSize: "14px",
                      }}
                    >
                      <thead>
                        <tr>
                          <th style={thStyle}>MES ID</th>
                          <th style={thStyle}>Заказ</th>
                          <th style={thStyle}>Изделие</th>
                          <th style={thStyle}>Участок</th>
                          <th style={thStyle}>Операция</th>
                          <th style={thStyle}>Станок</th>
                          <th style={thStyle}>Плановое время</th>
                          <th style={thStyle}>План</th>
                          <th style={thStyle}>Доступно</th>
                          <th style={thStyle}>Годно</th>
                          <th style={thStyle}>Брак</th>
                          <th style={thStyle}>Остаток</th>
                          <th style={thStyle}>Факт начала</th>
                          <th style={thStyle}>Факт окончания</th>
                          <th style={thStyle}>Статус операции</th>
                          <th style={thStyle}>Действие</th>
                        </tr>
                      </thead>
                      <tbody>
                        {operations.map((operation) => {
                          const remainingQuantity = Math.max(
                            Number(operation.quantity || 0) -
                              Number(operation.good_quantity || 0),
                            0
                          );
                          const canStartOperation =
                            ["released", "in_work"].includes(
                              selectedMesRun.status
                            ) &&
                            ["released", "partially_completed"].includes(
                              operation.status
                            );

                          return (
                            <tr
                              key={operation.id}
                              style={getMesOperationRowStyle(operation.status)}
                            >
                              <td style={tdStyle}>{operation.id}</td>
                              <td style={tdStyle}>
                                {operation.order_no || operation.order_id}
                              </td>
                              <td style={tdStyle}>
                                {operation.product_name || operation.product_id}
                              </td>
                              <td style={tdStyle}>
                                {operation.workshop_name || "Без участка"}
                              </td>
                              <td style={tdStyle}>
                                {operation.operation_name ||
                                  OPERATION_NAMES[operation.operation_type] ||
                                  operation.operation_type}
                              </td>
                              <td style={tdStyle}>
                                {operation.machine_name || operation.machine_id}
                              </td>
                              <td style={tdStyle}>
                                {formatPlanInterval(
                                  operation.planned_start_time,
                                  operation.planned_end_time
                                )}
                              </td>
                              <td style={tdStyle}>{operation.quantity ?? 0}</td>
                              <td style={tdStyle}>
                                {Number(operation.available_quantity || 0)}
                              </td>
                              <td style={tdStyle}>
                                {operation.good_quantity ?? 0}
                              </td>
                              <td style={tdStyle}>
                                {operation.defect_quantity ?? 0}
                              </td>
                              <td style={tdStyle}>{remainingQuantity}</td>
                              <td style={tdStyle}>
                                {operation.actual_start_at
                                  ? new Date(
                                      operation.actual_start_at
                                    ).toLocaleString()
                                  : ""}
                              </td>
                              <td style={tdStyle}>
                                {operation.actual_end_at
                                  ? new Date(
                                      operation.actual_end_at
                                    ).toLocaleString()
                                  : ""}
                              </td>
                              <td style={tdStyle}>
                                {MES_OPERATION_STATUS_LABELS[
                                  operation.status
                                ] || operation.status}
                              </td>
                              <td style={tdStyle}>
                                <div
                                  style={{
                                    display: "flex",
                                    gap: "6px",
                                    flexWrap: "wrap",
                                  }}
                                >
                                  {operation.id && (
                                    <button
                                      onClick={() =>
                                        handleOpenMesOperationCard(operation)
                                      }
                                      style={{
                                        padding: "4px 8px",
                                        cursor: "pointer",
                                      }}
                                    >
                                      Карточка
                                    </button>
                                  )}

                                  {selectedMesRun.status === "created" &&
                                    operation.status !== "excluded" && (
                                      <button
                                        onClick={() =>
                                          handleExcludeMesOrderItem(
                                            selectedMesRun.id,
                                            operation.order_item_id
                                          )
                                        }
                                        style={{
                                          padding: "4px 8px",
                                          cursor: "pointer",
                                        }}
                                      >
                                        Исключить позицию
                                      </button>
                                    )}

                                  {selectedMesRun.status === "created" &&
                                    operation.status === "excluded" && (
                                      <button
                                        onClick={() =>
                                          handleIncludeMesOrderItem(
                                            selectedMesRun.id,
                                            operation.order_item_id
                                          )
                                        }
                                        style={{
                                          padding: "4px 8px",
                                          cursor: "pointer",
                                        }}
                                      >
                                        Вернуть позицию
                                      </button>
                                    )}

                                  {canStartOperation && (
                                    <button
                                      onClick={() =>
                                        handleStartMesOperation(operation.id)
                                      }
                                      style={{
                                        padding: "4px 8px",
                                        cursor: "pointer",
                                      }}
                                    >
                                      Начать
                                    </button>
                                  )}

                                  {operation.status === "in_work" && (
                                    <button
                                      onClick={() =>
                                        handleCompleteMesOperation(operation)
                                      }
                                      style={{
                                        padding: "4px 8px",
                                        cursor: "pointer",
                                      }}
                                    >
                                      Зафиксировать выполнение
                                    </button>
                                  )}
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ))}
              </div>
            )}

            {false && selectedMesOperationForReports && (
              <div
                style={{
                  marginTop: "16px",
                  border: "1px solid #ccc",
                  padding: "12px",
                  background: "#fafafa",
                  overflowX: "auto",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    gap: "12px",
                    alignItems: "center",
                    marginBottom: "8px",
                  }}
                >
                  <h3 style={{ margin: 0 }}>Журнал выполнения операции</h3>

                  <button
                    onClick={handleCloseMesOperationReports}
                    style={{ padding: "4px 8px", cursor: "pointer" }}
                  >
                    Закрыть
                  </button>
                </div>

                <div style={{ marginBottom: "10px", fontSize: "14px" }}>
                  <div>
                    <b>Заказ:</b>{" "}
                    {selectedMesOperationForReports.order_no || ""}
                  </div>
                  <div>
                    <b>Изделие:</b>{" "}
                    {selectedMesOperationForReports.product_name || ""}
                  </div>
                  <div>
                    <b>Операция:</b>{" "}
                    {selectedMesOperationForReports.operation_name ||
                      selectedMesOperationForReports.operation_type ||
                      ""}
                  </div>
                  <div>
                    <b>Станок:</b>{" "}
                    {selectedMesOperationForReports.machine_name ||
                      selectedMesOperationForReports.machine_id ||
                      ""}
                  </div>
                  <div>
                    <b>Статус:</b>{" "}
                    {MES_OPERATION_STATUS_LABELS[
                      selectedMesOperationForReports.status
                    ] ||
                      selectedMesOperationForReports.status ||
                      ""}
                  </div>
                  <div>
                    <b>Количество:</b> план{" "}
                    {Number(selectedMesOperationForReports.quantity || 0)},
                    годно{" "}
                    {Number(
                      selectedMesOperationForReports.good_quantity || 0
                    )}
                    , брак{" "}
                    {Number(
                      selectedMesOperationForReports.defect_quantity || 0
                    )}
                    , доступно{" "}
                    {Number(
                      selectedMesOperationForReports.available_quantity || 0
                    )}
                    , остаток{" "}
                    {Math.max(
                      Number(selectedMesOperationForReports.quantity || 0) -
                        Number(
                          selectedMesOperationForReports.good_quantity || 0
                        ),
                      0
                    )}
                  </div>
                </div>

                {isReportsLoading ? (
                  <div>Журнал загружается...</div>
                ) : selectedMesOperationReports.length === 0 ? (
                  <div style={{ color: "#777" }}>
                    По этой операции пока нет записей выполнения
                  </div>
                ) : (
                  <table
                    style={{
                      width: "100%",
                      borderCollapse: "collapse",
                      fontSize: "14px",
                    }}
                  >
                    <thead>
                      <tr>
                        <th style={thStyle}>№</th>
                        <th style={thStyle}>ID записи</th>
                        <th style={thStyle}>Тип</th>
                        <th style={thStyle}>Начало</th>
                        <th style={thStyle}>Окончание</th>
                        <th style={thStyle}>Годно</th>
                        <th style={thStyle}>Брак</th>
                        <th style={thStyle}>Комментарий</th>
                        <th style={thStyle}>Причина исправления</th>
                        <th style={thStyle}>Кто ввёл</th>
                        <th style={thStyle}>Создано</th>
                        <th style={thStyle}>Действие</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedMesOperationReports.map((report, index) => {
                        const isCorrection =
                          report.report_type === "correction";
                        const correctedIndex =
                          report.corrected_report_id == null
                            ? -1
                            : selectedMesOperationReports.findIndex(
                                (candidate) =>
                                  String(candidate.id ?? candidate.report_id) ===
                                  String(report.corrected_report_id)
                              );
                        const reportRowStyle = isCorrection
                          ? { background: "#fff3cd" }
                          : {};

                        return (
                          <tr key={report.id || index} style={reportRowStyle}>
                            <td style={tdStyle}>{index + 1}</td>
                            <td style={tdStyle}>
                              {report.id ?? report.report_id ?? ""}
                            </td>
                            <td style={tdStyle}>
                              {MES_REPORT_TYPE_LABELS[report.report_type] ||
                                report.report_type ||
                                "выполнение"}
                            </td>
                            <td style={tdStyle}>
                              {formatDateTime(report.started_at)}
                            </td>
                            <td style={tdStyle}>
                              {formatDateTime(report.ended_at)}
                            </td>
                            <td style={tdStyle}>
                              {Number(report.good_quantity || 0)}
                            </td>
                            <td style={tdStyle}>
                              {Number(report.defect_quantity || 0)}
                            </td>
                            <td style={tdStyle}>{report.comment || ""}</td>
                            <td style={tdStyle}>
                              {isCorrection && report.corrected_report_id
                                ? `Исправляет строку ${
                                    correctedIndex >= 0
                                      ? correctedIndex + 1
                                      : "?"
                                  } (ID ${
                                    report.corrected_report_id
                                  }). `
                                : ""}
                              {report.correction_reason || ""}
                            </td>
                            <td style={tdStyle}>{report.reported_by || ""}</td>
                            <td style={tdStyle}>
                              {formatDateTime(report.created_at)}
                            </td>
                            <td style={tdStyle}>
                              {!isCorrection && (
                                <button
                                  onClick={() =>
                                    handleCorrectMesOperationReport(report)
                                  }
                                  style={{
                                    padding: "4px 8px",
                                    cursor: "pointer",
                                  }}
                                >
                                  Исправить
                                </button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </div>
        )}

          </div>
        )}
        {selectedMesOperationCard && (
          <div
            style={{
              marginTop: "16px",
              border: "1px solid #bbb",
              padding: "12px",
              background: "#fff",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                gap: "12px",
                alignItems: "center",
                marginBottom: "10px",
              }}
            >
              <h3 style={{ margin: 0 }}>Карточка операции</h3>
              <button
                onClick={handleCloseMesOperationCard}
                style={{ padding: "4px 8px", cursor: "pointer" }}
              >
                Закрыть
              </button>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                gap: "6px 16px",
                marginBottom: "10px",
                fontSize: "14px",
              }}
            >
              <div><b>Задание:</b> #{selectedMesOperationCard.schedule_run_id}</div>
              <div><b>Участок:</b> {selectedMesOperationCard.workshop_name || "Без участка"}</div>
              <div><b>Ответственный:</b> {selectedMesOperationCard.responsible_name || ""}</div>
              <div><b>Заказ:</b> {selectedMesOperationCard.order_no || ""}</div>
              <div><b>Изделие / партия:</b> {selectedMesOperationCard.product_name || ""}</div>
              <div>
                <b>Операция:</b>{" "}
                {selectedMesOperationCard.operation_name ||
                  selectedMesOperationCard.operation_type ||
                  ""}
              </div>
              <div>
                <b>Станок:</b>{" "}
                {selectedMesOperationCard.machine_name ||
                  selectedMesOperationCard.machine_id ||
                  ""}
              </div>
              <div>
                <b>Статус:</b>{" "}
                {MES_OPERATION_STATUS_LABELS[selectedMesOperationCard.status] ||
                  selectedMesOperationCard.status ||
                  ""}
              </div>
            </div>

            <div style={{ display: "flex", gap: "8px", marginBottom: "10px" }}>
              <button
                onClick={() => setMesOperationCardTab("fact")}
                style={{
                  padding: "6px 10px",
                  fontWeight: mesOperationCardTab === "fact" ? "bold" : "normal",
                  cursor: "pointer",
                }}
              >
                Факт
              </button>
              <button
                onClick={() => setMesOperationCardTab("journal")}
                style={{
                  padding: "6px 10px",
                  fontWeight:
                    mesOperationCardTab === "journal" ? "bold" : "normal",
                  cursor: "pointer",
                }}
              >
                Журнал
              </button>
            </div>

            {mesOperationCardTab === "fact" && (
              <div>
                <div
                  style={{
                    display: "flex",
                    gap: "16px",
                    flexWrap: "wrap",
                    marginBottom: "10px",
                    padding: "8px",
                    background: "#f7f7f7",
                    border: "1px solid #ddd",
                  }}
                >
                  <span>План: {Number(selectedMesOperationCard.quantity || 0)}</span>
                  <span>
                    Доступно:{" "}
                    {Number(selectedMesOperationCard.available_quantity || 0)}
                  </span>
                  <span>Годно: {Number(selectedMesOperationCard.good_quantity || 0)}</span>
                  <span>Брак: {Number(selectedMesOperationCard.defect_quantity || 0)}</span>
                  <span>
                    Остаток:{" "}
                    {Math.max(
                      Number(selectedMesOperationCard.quantity || 0) -
                        Number(selectedMesOperationCard.good_quantity || 0),
                      0
                    )}
                  </span>
                </div>

                <div style={{ marginBottom: "10px", fontSize: "14px" }}>
                  <div>
                    <b>Плановое начало:</b>{" "}
                    {formatPlanMinute(selectedMesOperationCard.planned_start_time)}
                  </div>
                  <div>
                    <b>Плановое окончание:</b>{" "}
                    {formatPlanMinute(selectedMesOperationCard.planned_end_time)}
                  </div>
                  <div>
                    <b>Факт начала:</b>{" "}
                    {formatDateTime(selectedMesOperationCard.actual_start_at)}
                  </div>
                  <div>
                    <b>Факт окончания:</b>{" "}
                    {formatDateTime(selectedMesOperationCard.actual_end_at)}
                  </div>
                </div>

                <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                  {["released", "partially_completed"].includes(
                    selectedMesOperationCard.status
                  ) && (
                    <button
                      onClick={() =>
                        handleStartMesOperation(selectedMesOperationCard.id)
                      }
                      style={{ padding: "6px 10px", cursor: "pointer" }}
                    >
                      Начать
                    </button>
                  )}
                  {selectedMesOperationCard.status === "in_work" && (
                    <button
                      onClick={() =>
                        handleCompleteMesOperation(selectedMesOperationCard)
                      }
                      style={{ padding: "6px 10px", cursor: "pointer" }}
                    >
                      Внести факт
                    </button>
                  )}
                </div>
              </div>
            )}

            {mesOperationCardTab === "journal" && (
              <div>
                {isMesOperationCardReportsLoading ? (
                  <div>Журнал загружается...</div>
                ) : mesOperationCardReports.length === 0 ? (
                  <div style={{ color: "#777" }}>
                    По этой операции пока нет записей выполнения
                  </div>
                ) : (
                  <div style={{ display: "grid", gap: "10px" }}>
                    {productionCardReports.map((report, index) => {
                      const reportId = report.id ?? report.report_id;
                      const corrections = correctionCardReports.filter(
                        (correction) =>
                          String(correction.corrected_report_id) ===
                          String(reportId)
                      );
                      const totalGood =
                        Number(report.good_quantity || 0) +
                        corrections.reduce(
                          (sum, correction) =>
                            sum + Number(correction.good_quantity || 0),
                          0
                        );
                      const totalDefect =
                        Number(report.defect_quantity || 0) +
                        corrections.reduce(
                          (sum, correction) =>
                            sum + Number(correction.defect_quantity || 0),
                          0
                        );

                      return (
                        <div
                          key={reportId || index}
                          style={{
                            border: "1px solid #ddd",
                            padding: "10px",
                            background: "#fff",
                          }}
                        >
                          <div
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              gap: "10px",
                              flexWrap: "wrap",
                            }}
                          >
                            <strong>
                              Выполнение #{reportId} · строка {index + 1}
                            </strong>
                            <button
                              onClick={() => handleCorrectMesOperationReport(report)}
                              style={{ padding: "4px 8px", cursor: "pointer" }}
                            >
                              Исправить
                            </button>
                          </div>
                          <div>Начало: {formatDateTime(report.started_at)}</div>
                          <div>Окончание: {formatDateTime(report.ended_at)}</div>
                          <div>
                            Введено: годно {Number(report.good_quantity || 0)},
                            брак {Number(report.defect_quantity || 0)}
                          </div>
                          <div>Комментарий: {report.comment || ""}</div>
                          {corrections.map((correction) => (
                            <div
                              key={correction.id ?? correction.report_id}
                              style={{
                                marginTop: "8px",
                                marginLeft: "16px",
                                padding: "8px",
                                background: "#fff3cd",
                                borderLeft: "4px solid #fb8c00",
                              }}
                            >
                              <strong>
                                Корректировка #{correction.id ?? correction.report_id}
                              </strong>
                              <div>
                                Изменение: годно{" "}
                                {Number(correction.good_quantity || 0)}, брак{" "}
                                {Number(correction.defect_quantity || 0)}
                              </div>
                              <div>
                                Причина: {correction.correction_reason || ""}
                              </div>
                              <div>
                                Создано: {formatDateTime(correction.created_at)}
                              </div>
                            </div>
                          ))}
                          {corrections.length > 0 && (
                            <div style={{ marginTop: "8px" }}>
                              Итог после корректировок: годно {totalGood}, брак{" "}
                              {totalDefect}
                            </div>
                          )}
                        </div>
                      );
                    })}

                    {orphanCorrectionCardReports.length > 0 && (
                      <div
                        style={{
                          border: "1px solid #ddd",
                          padding: "10px",
                          background: "#fff8e1",
                        }}
                      >
                        <strong>Корректировки без исходной записи</strong>
                        {orphanCorrectionCardReports.map((correction) => (
                          <div key={correction.id ?? correction.report_id}>
                            #{correction.id ?? correction.report_id}: годно{" "}
                            {Number(correction.good_quantity || 0)}, брак{" "}
                            {Number(correction.defect_quantity || 0)}. Причина:{" "}
                            {correction.correction_reason || ""}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {mesScreen === "workplace" && (
          <div>
            <div
              style={{
                display: "flex",
                gap: "8px",
                alignItems: "center",
                marginBottom: "12px",
                flexWrap: "wrap",
              }}
            >
              <h3 style={{ margin: 0 }}>Рабочее место мастера</h3>
              <span>Участок:</span>
              <select
                value={selectedMesWorkshopId}
                onChange={(e) => setSelectedMesWorkshopId(e.target.value)}
                style={{ padding: "6px", minWidth: "180px" }}
              >
                <option value="">Все участки</option>
                {mesWorkshops.map((workshop) => (
                  <option key={workshop.id} value={String(workshop.id)}>
                    {workshop.name}
                    {workshop.responsible_name
                      ? ` — ${workshop.responsible_name}`
                      : ""}
                  </option>
                ))}
              </select>
              <button
                onClick={loadMesWorkplaceOperations}
                style={{ padding: "6px 10px", cursor: "pointer" }}
              >
                Обновить
              </button>
            </div>

            {isMesWorkplaceLoading ? (
              <div>Рабочее место загружается...</div>
            ) : !hasMesWorkplaceOperations ? (
              <div style={{ color: "#777" }}>
                Для выбранного участка нет операций к выполнению
              </div>
            ) : (
              mesWorkplaceGroups.map((group) =>
                group.operations.length === 0 ? null : (
                  <div key={group.key} style={{ marginBottom: "16px" }}>
                    <h4 style={{ margin: "0 0 8px" }}>{group.title}</h4>
                    <div style={{ overflowX: "auto" }}>
                      <table
                        style={{
                          width: "100%",
                          borderCollapse: "collapse",
                          fontSize: "14px",
                        }}
                      >
                        <thead>
                          <tr>
                            <th style={thStyle}>Задание</th>
                            <th style={thStyle}>Заказ</th>
                            <th style={thStyle}>Изделие / партия</th>
                            <th style={thStyle}>Операция</th>
                            <th style={thStyle}>Станок</th>
                            <th style={thStyle}>План</th>
                            <th style={thStyle}>Доступно</th>
                            <th style={thStyle}>Годно</th>
                            <th style={thStyle}>Брак</th>
                            <th style={thStyle}>Остаток</th>
                            <th style={thStyle}>Статус</th>
                            <th style={thStyle}>Действие</th>
                          </tr>
                        </thead>
                        <tbody>
                          {group.operations.map((operation) => (
                            <tr
                              key={operation.id}
                              style={getMesOperationRowStyle(operation.status)}
                            >
                              <td style={tdStyle}>#{operation.schedule_run_id}</td>
                              <td style={tdStyle}>{operation.order_no || ""}</td>
                              <td style={tdStyle}>
                                {operation.product_name || ""}
                              </td>
                              <td style={tdStyle}>
                                {operation.operation_name ||
                                  operation.operation_type ||
                                  ""}
                              </td>
                              <td style={tdStyle}>
                                {operation.machine_name || operation.machine_id}
                              </td>
                              <td style={tdStyle}>
                                {formatPlanInterval(
                                  operation.planned_start_time,
                                  operation.planned_end_time
                                )}
                              </td>
                              <td style={tdStyle}>
                                {Number(operation.available_quantity || 0)}
                              </td>
                              <td style={tdStyle}>
                                {Number(operation.good_quantity || 0)}
                              </td>
                              <td style={tdStyle}>
                                {Number(operation.defect_quantity || 0)}
                              </td>
                              <td style={tdStyle}>
                                {Math.max(
                                  Number(operation.quantity || 0) -
                                    Number(operation.good_quantity || 0),
                                  0
                                )}
                              </td>
                              <td style={tdStyle}>
                                {MES_OPERATION_STATUS_LABELS[operation.status] ||
                                  operation.status}
                              </td>
                              <td style={tdStyle}>
                                <div
                                  style={{
                                    display: "flex",
                                    gap: "6px",
                                    flexWrap: "wrap",
                                  }}
                                >
                                  {["released", "partially_completed"].includes(
                                    operation.status
                                  ) && (
                                    <button
                                      onClick={() =>
                                        handleStartMesOperation(operation.id)
                                      }
                                      style={{
                                        padding: "4px 8px",
                                        cursor: "pointer",
                                      }}
                                    >
                                      Начать
                                    </button>
                                  )}
                                  {operation.status === "in_work" && (
                                    <button
                                      onClick={() =>
                                        handleCompleteMesOperation(operation)
                                      }
                                      style={{
                                        padding: "4px 8px",
                                        cursor: "pointer",
                                      }}
                                    >
                                      Внести факт
                                    </button>
                                  )}
                                  <button
                                    onClick={() =>
                                      handleOpenMesOperationCard(operation)
                                    }
                                    style={{
                                      padding: "4px 8px",
                                      cursor: "pointer",
                                    }}
                                  >
                                    Карточка
                                  </button>
                                </div>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )
              )
            )}
          </div>
        )}

        {mesScreen === "shiftReport" && (
        <div
          style={{
            marginTop: "16px",
            border: "1px solid #ccc",
            padding: "12px",
            background: "#fafafa",
          }}
        >
          <h3 style={{ marginTop: 0 }}>Сменный отчёт мастера</h3>

          <div
            style={{
              display: "flex",
              gap: "8px",
              alignItems: "center",
              flexWrap: "wrap",
              marginBottom: "12px",
            }}
          >
            <span>День:</span>
            <input
              type="number"
              min="1"
              value={shiftReportDay}
              onChange={(e) => setShiftReportDay(e.target.value)}
              style={{ width: "80px", padding: "6px" }}
            />

            <span>Смена:</span>
            <select
              value={shiftReportShift}
              onChange={(e) => setShiftReportShift(e.target.value)}
              style={{ padding: "6px" }}
            >
              <option value="1">Смена 1, 08:00–19:00</option>
              <option value="2">Смена 2, 20:00–07:00</option>
            </select>

            <button
              onClick={handleLoadShiftReport}
              style={{ padding: "6px 10px", cursor: "pointer" }}
            >
              Показать отчёт
            </button>
          </div>

          {shiftReport && (
            <div
              style={{
                display: "flex",
                gap: "16px",
                flexWrap: "wrap",
                marginBottom: "12px",
                padding: "8px",
                background: "#f7f7f7",
                border: "1px solid #ddd",
              }}
            >
              <span>
                Период: {formatDateTime(shiftReport.shift_start)} —{" "}
                {formatDateTime(shiftReport.shift_end)}
              </span>
              <span>
                Участок: {shiftReport.workshop?.name || "Все участки"}
              </span>
              {shiftReport.workshop?.responsible_name && (
                <span>
                  Ответственный: {shiftReport.workshop.responsible_name}
                </span>
              )}
              <span>
                Записей факта: {shiftReport.summary?.reports_count ?? 0}
              </span>
              <span>
                Операций: {shiftReport.summary?.operations_count ?? 0}
              </span>
              <span>Годно: {shiftReport.summary?.good_quantity ?? 0}</span>
              <span>Брак: {shiftReport.summary?.defect_quantity ?? 0}</span>
            </div>
          )}

          {isShiftReportLoading ? (
            <div>Сменный отчёт загружается...</div>
          ) : shiftReport &&
            Array.isArray(shiftReport.reports) &&
            shiftReport.reports.length === 0 ? (
            <div style={{ color: "#777" }}>
              За выбранную смену фактических записей нет
            </div>
          ) : shiftReport && Array.isArray(shiftReport.reports) ? (
            <div style={{ overflowX: "auto" }}>
              <table
                style={{
                  width: "100%",
                  borderCollapse: "collapse",
                  fontSize: "14px",
                }}
              >
                <thead>
                  <tr>
                    <th style={thStyle}>№</th>
                    <th style={thStyle}>ID записи</th>
                    <th style={thStyle}>Тип</th>
                    <th style={thStyle}>Задание</th>
                    <th style={thStyle}>Заказ</th>
                    <th style={thStyle}>Изделие</th>
                    <th style={thStyle}>Участок</th>
                    <th style={thStyle}>Операция</th>
                    <th style={thStyle}>Станок</th>
                    <th style={thStyle}>Начало</th>
                    <th style={thStyle}>Окончание</th>
                    <th style={thStyle}>Годно</th>
                    <th style={thStyle}>Брак</th>
                    <th style={thStyle}>Комментарий</th>
                    <th style={thStyle}>Причина исправления</th>
                    <th style={thStyle}>Кто ввёл</th>
                    <th style={thStyle}>Создано</th>
                  </tr>
                </thead>
                <tbody>
                  {shiftReport.reports.map((report, index) => {
                    const isCorrection = report.report_type === "correction";

                    return (
                      <tr
                        key={report.report_id || index}
                        style={isCorrection ? { background: "#fff3cd" } : {}}
                      >
                        <td style={tdStyle}>{index + 1}</td>
                        <td style={tdStyle}>
                          {report.report_id ?? report.id ?? ""}
                        </td>
                        <td style={tdStyle}>
                          {MES_REPORT_TYPE_LABELS[report.report_type] ||
                            report.report_type ||
                            "выполнение"}
                        </td>
                        <td style={tdStyle}>#{report.schedule_run_id}</td>
                        <td style={tdStyle}>{report.order_no || ""}</td>
                        <td style={tdStyle}>{report.product_name || ""}</td>
                        <td style={tdStyle}>
                          {report.workshop_name || "Без участка"}
                        </td>
                        <td style={tdStyle}>
                          {report.operation_name || report.operation_type || ""}
                        </td>
                        <td style={tdStyle}>
                          {report.machine_name || report.machine_id || ""}
                        </td>
                        <td style={tdStyle}>
                          {formatDateTime(report.started_at)}
                        </td>
                        <td style={tdStyle}>
                          {formatDateTime(report.ended_at)}
                        </td>
                        <td style={tdStyle}>
                          {Number(report.good_quantity || 0)}
                        </td>
                        <td style={tdStyle}>
                          {Number(report.defect_quantity || 0)}
                        </td>
                        <td style={tdStyle}>{report.comment || ""}</td>
                        <td style={tdStyle}>
                          {isCorrection && report.corrected_report_id
                            ? `Исправляет запись #${report.corrected_report_id}. `
                            : ""}
                          {report.correction_reason || ""}
                        </td>
                        <td style={tdStyle}>{report.reported_by || ""}</td>
                        <td style={tdStyle}>
                          {formatDateTime(report.created_at)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
        )}
      </div>
      )}
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
