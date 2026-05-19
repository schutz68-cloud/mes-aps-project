import { useEffect, useRef } from "react";
import { Timeline } from "vis-timeline/standalone";
import { DataSet } from "vis-data";
import "vis-timeline/styles/vis-timeline-graph2d.css";

const FREEZE_LINE_ID = "freeze-horizon-line";
const MACHINE_GROUP_ORDER = {
  COIL_A: 10,
  COIL_B: 11,
  BEND: 20,
  FACE: 30,
  HEAT: 40,
  COAT_A: 50,
  COAT_B: 51,
};
const ORDER_COLORS = [
  { setup: "#82b7f0", work: "#d8ebff", border: "#3f7fbd" },
  { setup: "#90d4a0", work: "#ddf5e3", border: "#4d9b60" },
  { setup: "#f1bd72", work: "#fff0d8", border: "#c47d21" },
  { setup: "#c7a0ee", work: "#efe2ff", border: "#8a5ec2" },
  { setup: "#ee9ca2", work: "#ffe0e3", border: "#bf5962" },
];
const MES_SCHEDULE_STATUS_LABELS = {
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

const MES_LOCKED_OPERATION_STATUSES = new Set([
  "released",
  "in_work",
  "partially_completed",
  "completed",
]);

function getOrderColors(orderId, isFrozen) {
  if (isFrozen) {
    return {
      setup: "#9fb0c3",
      work: "#d9e2ec",
      border: "#6b7c93",
    };
  }

  const index = Math.abs(Number(orderId ?? 1) - 1) % ORDER_COLORS.length;
  return ORDER_COLORS[index];
}

function buildActiveOverlayTitle(op, start, end) {
  const orderLabel =
    op.order_no || (op.order_id ? `Заказ ${op.order_id}` : "Заказ не указан");
  const productLabel = op.product_name || "Изделие не указано";
  const operationLabel =
    op.operation_name || op.operation_type || "Операция не указана";

  return `Активный план: ${orderLabel} — ${productLabel} — ${operationLabel}, ${start}-${end}`;
}

function buildMesTitle(op) {
  if (!op.mes_schedule_run_id) {
    return "";
  }

  const scheduleStatus =
    MES_SCHEDULE_STATUS_LABELS[op.mes_schedule_status] ||
    op.mes_schedule_status ||
    "не указан";
  const operationStatus =
    MES_OPERATION_STATUS_LABELS[op.mes_operation_status] ||
    op.mes_operation_status ||
    "не указан";

  return `\nMES-задание #${op.mes_schedule_run_id}: ${scheduleStatus}, операция: ${operationStatus}`;
}

function isLockedMesOperation(item) {
  if (item?.mes_schedule_status === "cancelled") {
    return false;
  }

  return MES_LOCKED_OPERATION_STATUSES.has(item?.mes_operation_status);
}

function buildSelectionClassName(baseClassName, opOrderItemId, selectedOrderItemId) {
  const classes = String(baseClassName || "")
    .split(" ")
    .filter(
      (name) =>
        name &&
        name !== "selected-order-item-operation" &&
        name !== "dimmed-by-order-selection"
    );

  if (selectedOrderItemId && opOrderItemId) {
    if (String(opOrderItemId) === String(selectedOrderItemId)) {
      classes.push("selected-order-item-operation");
    } else {
      classes.push("dimmed-by-order-selection");
    }
  }

  return classes.join(" ");
}

function getMachineGroupClass(machineGroupId) {
  return `machine-group-${String(machineGroupId || "unknown")
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, "-")}`;
}

function getEquipmentSectionKey(machineGroupId) {
  if (machineGroupId === "COIL_A" || machineGroupId === "COIL_B") {
    return "COILING";
  }

  if (machineGroupId === "COAT_A" || machineGroupId === "COAT_B") {
    return "COATING";
  }

  return machineGroupId || "unknown";
}

function setMachineGroups(
  groupMap,
  machineId,
  machineName,
  machineGroupId,
  machineOrderIndex,
  includeActiveGroup,
  addEquipmentSeparator
) {
  const draftGroupId = `${machineId}::draft`;
  const activeGroupId = `${machineId}::active`;
  const existingDraftGroup = groupMap.get(draftGroupId);
  const existingActiveGroup = groupMap.get(activeGroupId);
  const groupOrder = MACHINE_GROUP_ORDER[machineGroupId] ?? 999;
  const normalizedMachineOrderIndex = Number(machineOrderIndex ?? 0);
  const safeMachineOrderIndex = Number.isFinite(normalizedMachineOrderIndex)
    ? Math.max(normalizedMachineOrderIndex, 0)
    : 0;
  const machineBaseOrder =
    existingDraftGroup?.order ?? groupOrder * 1000 + safeMachineOrderIndex * 10;
  const displayName =
    machineName || existingDraftGroup?.content || String(machineId);
  const resolvedMachineGroupId = machineGroupId || existingDraftGroup?.machineGroupId;
  const machineGroupClass = getMachineGroupClass(resolvedMachineGroupId);
  const hasEquipmentSeparator =
    addEquipmentSeparator ||
    existingDraftGroup?.className?.includes("machine-equipment-group-start");
  const overlayStateClass = includeActiveGroup
    ? "machine-draft-group-with-overlay"
    : "machine-draft-group-without-overlay";

  groupMap.set(draftGroupId, {
    id: draftGroupId,
    machineId,
    machineGroupId: resolvedMachineGroupId,
    content: displayName,
    order: machineBaseOrder,
    className: `machine-draft-group ${overlayStateClass} ${machineGroupClass}${
      hasEquipmentSeparator ? " machine-equipment-group-start" : ""
    }`,
  });

  if (includeActiveGroup) {
    groupMap.set(activeGroupId, {
      id: activeGroupId,
      machineId,
      machineGroupId: resolvedMachineGroupId,
      content: "",
      order: existingActiveGroup?.order ?? machineBaseOrder + 1,
      className: `machine-active-group ${machineGroupClass}`,
    });
  }
}

function applyBackendOperationUpdates(items, updates) {
  if (!items || !Array.isArray(updates)) return;

  for (const update of updates) {
    if (!update?.id || !items.get(update.id)) {
      continue;
    }

    const machine = update.machine;
    items.update({
      id: update.id,
      group: `${machine}::draft`,
      machine,
      start: Number(update.start) * 60000,
      end: Number(update.end) * 60000,
    });
  }
}

export default function Gantt({
  data,
  machines,
  backgroundIntervals,
  activeOverlayData,
  showActiveOverlay,
  onMove,
  freezeHorizonMinutes,
  canEdit,
  enableOrderSelection,
  selectedOrderItemId,
  onSelectOperation,
  onClearSelection,
}) {
  const containerRef = useRef(null);
  const timelineRef = useRef(null);
  const itemsRef = useRef(new DataSet([]));
  const groupsRef = useRef(new DataSet([]));
  const onMoveRef = useRef(onMove);
  const selectedOrderItemIdRef = useRef(selectedOrderItemId);
  const onSelectOperationRef = useRef(onSelectOperation);
  const onClearSelectionRef = useRef(onClearSelection);
  const freezeHorizonRef = useRef(freezeHorizonMinutes ?? 0);
  const canEditRef = useRef(canEdit);
  const enableOrderSelectionRef = useRef(enableOrderSelection);
  const isMovingRef = useRef(false);

  useEffect(() => {
    onMoveRef.current = onMove;
  }, [onMove]);

  useEffect(() => {
    selectedOrderItemIdRef.current = selectedOrderItemId;
  }, [selectedOrderItemId]);

  useEffect(() => {
    onSelectOperationRef.current = onSelectOperation;
  }, [onSelectOperation]);

  useEffect(() => {
    onClearSelectionRef.current = onClearSelection;
  }, [onClearSelection]);

  useEffect(() => {
    canEditRef.current = Boolean(canEdit);
  }, [canEdit]);

  useEffect(() => {
    enableOrderSelectionRef.current = Boolean(enableOrderSelection);
  }, [enableOrderSelection]);

  useEffect(() => {
    if (!timelineRef.current) return;

    timelineRef.current.setOptions({
      editable: {
        add: false,
        updateTime: Boolean(canEdit),
        updateGroup: Boolean(canEdit),
        remove: false,
      },
    });
  }, [canEdit]);

  useEffect(() => {
    freezeHorizonRef.current = Number(freezeHorizonMinutes ?? 0);

    if (timelineRef.current) {
      timelineRef.current.setCustomTime(
        Number(freezeHorizonMinutes ?? 0) * 60000,
        FREEZE_LINE_ID
      );
    }
  }, [freezeHorizonMinutes]);

  useEffect(() => {
    if (!itemsRef.current) return;

    const updates = itemsRef.current
      .get()
      .filter((item) => item && item.type !== "background" && item.order_item_id)
      .map((item) => ({
        id: item.id,
        className: buildSelectionClassName(
          item.baseClassName,
          item.order_item_id,
          selectedOrderItemId
        ),
      }));

    if (updates.length > 0) {
      itemsRef.current.update(updates);
    }
  }, [selectedOrderItemId]);

  useEffect(() => {
    if (!containerRef.current || timelineRef.current) return;

    const timeline = new Timeline(
      containerRef.current,
      itemsRef.current,
      groupsRef.current,
      {
        groupOrder: (a, b) =>
          (a.order ?? 999) - (b.order ?? 999) ||
          String(a.content).localeCompare(String(b.content), "ru"),
        stack: true,
        selectable: true,
        editable: {
          add: false,
          updateTime: Boolean(canEditRef.current),
          updateGroup: Boolean(canEditRef.current),
          remove: false,
        },
        snap: (date) => {
          const step = 5 * 60 * 1000;
          return new Date(Math.round(date.getTime() / step) * step);
        },
        onMoving: (item, callback) => {
          isMovingRef.current = true;
          const prev = itemsRef.current.get(item.id);

          if (!prev || String(item.id).startsWith("active-")) {
            callback(item);
            return;
          }

          if (isLockedMesOperation(prev)) {
            callback(prev);
            return;
          }

          if (String(item.group).endsWith("::active")) {
            item.group = String(item.group).replace("::active", "::draft");
          }

          const previousStart = Number(new Date(prev.start));
          const previousEnd = Number(new Date(prev.end));
          const previousDuration = Math.max(previousEnd - previousStart, 60000);
          const newStart = Number(new Date(item.start));

          item.end = newStart + previousDuration;
          item.machine = String(item.group).replace("::draft", "");

          callback(item);
        },
        onMove: (item, callback) => {
          isMovingRef.current = true;
          const releaseMovingFlag = () => {
            setTimeout(() => {
              isMovingRef.current = false;
            }, 0);
          };
          const prev = itemsRef.current.get(item.id);

          if (String(item.id).startsWith("active-")) {
            callback(prev || null);
            releaseMovingFlag();
            return;
          }

          if (!canEditRef.current) {
            callback(prev || null);
            alert("Редактировать можно только черновую версию плана");
            releaseMovingFlag();
            return;
          }

          if (!prev) {
            callback(null);
            releaseMovingFlag();
            return;
          }

          if (isLockedMesOperation(prev)) {
            callback(prev);
            alert(
              `Операция уже передана в MES или выполнена и не может быть изменена в APS${
                prev.mes_schedule_run_id
                  ? `: производственное задание №${prev.mes_schedule_run_id}`
                  : ""
              }`
            );
            releaseMovingFlag();
            return;
          }

          if (String(item.group).endsWith("::active")) {
            item.group = String(item.group).replace("::active", "::draft");
          }

          const targetMachine = String(item.group).replace("::draft", "");
          item.machine = targetMachine;

          const originalStart = Math.floor(new Date(prev.start).getTime() / 60000);

          if (originalStart < freezeHorizonRef.current) {
            callback(prev);

            alert(
              "Операция находится в замороженной зоне плана и не может быть перемещена"
            );

            releaseMovingFlag();
            return;
          }

          const previousStart = Number(new Date(prev.start));
          const previousEnd = Number(new Date(prev.end));
          const previousDuration = Math.max(previousEnd - previousStart, 60000);
          const newStart = Number(new Date(item.start));
          const correctedItem = {
            ...item,
            end: newStart + previousDuration,
          };

          callback(correctedItem);

          const payload = {
            id: correctedItem.id,
            machine:
              correctedItem.machine ||
              String(correctedItem.group).replace("::draft", ""),
            start: Math.floor(Number(new Date(correctedItem.start)) / 60000),
            end: Math.floor(Number(new Date(correctedItem.end)) / 60000),
          };

          Promise.resolve(onMoveRef.current?.(payload))
            .then((result) => {
              if (result?.aborted || result?.superseded) {
                return;
              }

              const data = result?.data;
              const updates = Array.isArray(data?.changed_operations)
                ? data.changed_operations
                : data?.operation
                ? [data.operation]
                : [];

              applyBackendOperationUpdates(itemsRef.current, updates);
            })
            .catch((error) => {
              if (prev) itemsRef.current.update(prev);

              alert(
                "Нельзя переместить операцию: " +
                  (error?.message || "неизвестная ошибка")
              );
            })
            .finally(releaseMovingFlag);
        },
      }
    );

    timeline.addCustomTime(
      Number(freezeHorizonRef.current ?? 0) * 60000,
      FREEZE_LINE_ID
    );

    timeline.setCustomTimeTitle(
      "Граница замороженной зоны",
      FREEZE_LINE_ID
    );

    timeline.on("click", (properties) => {
      if (isMovingRef.current) {
        isMovingRef.current = false;
        return;
      }

      if (!enableOrderSelectionRef.current) {
        return;
      }

      if (!properties?.item) {
        onClearSelectionRef.current?.();
        return;
      }

      if (properties?.item) {
        const item = itemsRef.current.get(properties.item);

        if (!item || String(properties.item).startsWith("bg-")) {
          onClearSelectionRef.current?.();
          return;
        }

        onSelectOperationRef.current?.(item);
        return;
      }

      if (!canEditRef.current && properties?.item) {
        alert("Редактировать можно только черновую версию плана");
      }
    });

    timelineRef.current = timeline;

    return () => {
      timeline.destroy();
      timelineRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!timelineRef.current) return;
    if (!Array.isArray(data)) return;

    const freezeHorizon = Number(freezeHorizonMinutes ?? 0);
    const groupMap = new Map();
    const items = [];
    const machineOrderMap = new Map();
    const seenEquipmentSections = new Set();
    const sortedMachines = [...(machines || [])].sort((a, b) => {
      const orderA = MACHINE_GROUP_ORDER[a.group_id] ?? 999;
      const orderB = MACHINE_GROUP_ORDER[b.group_id] ?? 999;

      return (
        orderA - orderB ||
        String(a.name || a.id).localeCompare(String(b.name || b.id), "ru")
      );
    });
    let fallbackMachineOrderIndex = sortedMachines.length;

    const shouldAddEquipmentSeparator = (machineGroupId) => {
      const sectionKey = getEquipmentSectionKey(machineGroupId);

      if (seenEquipmentSections.has(sectionKey)) {
        return false;
      }

      seenEquipmentSections.add(sectionKey);
      return seenEquipmentSections.size > 1;
    };

    const getMachineOrderIndex = (machineId) => {
      const key = String(machineId);

      if (!machineOrderMap.has(key)) {
        machineOrderMap.set(key, fallbackMachineOrderIndex);
        fallbackMachineOrderIndex += 1;
      }

      return machineOrderMap.get(key);
    };

    for (const [machineOrderIndex, machine] of sortedMachines.entries()) {
      machineOrderMap.set(String(machine.id), machineOrderIndex);
      setMachineGroups(
        groupMap,
        machine.id,
        machine.name,
        machine.group_id,
        machineOrderIndex,
        showActiveOverlay,
        shouldAddEquipmentSeparator(machine.group_id)
      );
    }

    for (const [index, interval] of (backgroundIntervals || []).entries()) {
      const start = Number(interval.start);
      const end = Number(interval.end);

      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
        continue;
      }

      const isBetweenShifts = interval.kind === "between_shifts";

      items.push({
        id: `${isBetweenShifts ? "bg-between" : "bg-break"}-${index}-${start}-${end}`,
        start: start * 60000,
        end: end * 60000,
        type: "background",
        className: isBetweenShifts
          ? "calendar-between-shifts"
          : "calendar-shift-break",
        title: isBetweenShifts ? "Межсменное время" : "Нерабочий период смены",
      });
    }

    if (showActiveOverlay && Array.isArray(activeOverlayData)) {
      for (const op of activeOverlayData) {
        const start = Number(op.start);
        const end = Number(op.end);
        const machineOrderIndex = getMachineOrderIndex(op.machine);
        const isMesCreated =
          op.mes_schedule_status === "created" &&
          op.mes_operation_status !== "excluded";
        const isMesReleased = MES_LOCKED_OPERATION_STATUSES.has(
          op.mes_operation_status
        );
        const isMesExcluded = op.mes_operation_status === "excluded";
        const baseClassName = [
          "active-overlay-operation",
          isMesCreated ? "mes-created-operation" : "",
          isMesReleased ? "mes-released-operation" : "",
          isMesExcluded ? "mes-excluded-operation" : "",
        ]
          .filter(Boolean)
          .join(" ");
        setMachineGroups(
          groupMap,
          op.machine,
          op.machine_name,
          op.machine_group_id,
          machineOrderIndex,
          showActiveOverlay,
          shouldAddEquipmentSeparator(op.machine_group_id)
        );

        items.push({
          id: `active-${op.id}`,
          group: `${op.machine}::active`,
          machine: op.machine,
          content: op.mes_schedule_run_id ? "MES" : "",
          title: `${buildActiveOverlayTitle(op, start, end)}${buildMesTitle(op)}`,
          start: start * 60000,
          end: end * 60000,
          type: "range",
          editable: false,
          baseClassName,
          className: buildSelectionClassName(
            baseClassName,
            op.order_item_id,
            selectedOrderItemIdRef.current
          ),
          mes_schedule_operation_id: op.mes_schedule_operation_id,
          order_item_id: op.order_item_id,
          order_id: op.order_id,
          order_no: op.order_no,
          product_name: op.product_name,
          quantity: op.quantity,
          operation_name: op.operation_name,
          operation_type: op.operation_type,
          mes_schedule_run_id: op.mes_schedule_run_id,
          mes_schedule_status: op.mes_schedule_status,
          mes_operation_status: op.mes_operation_status,
        });
      }
    }

    for (const op of data) {
      const start = Number(op.start);
      const end = Number(op.end);
      const duration = Math.max(end - start, 1);
      const setupMinutes = Math.max(Number(op.setup_minutes ?? 0), 0);
      const runMinutes = Math.max(duration - setupMinutes, 0);
      const setupPercent = Math.min((setupMinutes / duration) * 100, 100);
      const isFrozen = start < freezeHorizon;
      const colors = getOrderColors(op.order_id, isFrozen);
      const machineOrderIndex = getMachineOrderIndex(op.machine);
      const isMesCreated =
        op.mes_schedule_status === "created" &&
        op.mes_operation_status !== "excluded";
      const isMesReleased = MES_LOCKED_OPERATION_STATUSES.has(
        op.mes_operation_status
      );
      const isMesExcluded = op.mes_operation_status === "excluded";
      const baseClassName = [
        isFrozen ? "frozen-operation" : "normal-operation",
        isMesCreated ? "mes-created-operation" : "",
        isMesReleased ? "mes-released-operation" : "",
        isMesExcluded ? "mes-excluded-operation" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const setupTitlePart =
        setupMinutes > 0 ? `РЅР°Р»Р°РґРєР° ${setupMinutes} РјРёРЅ., ` : "";
      const baseTitle = `${
        op.operation_name || op.operation_type || ""
      }, наладка ${setupMinutes} мин., выполнение ${runMinutes} мин.`;

      setMachineGroups(
        groupMap,
        op.machine,
        op.machine_name,
        op.machine_group_id,
        machineOrderIndex,
        showActiveOverlay,
        shouldAddEquipmentSeparator(op.machine_group_id)
      );

      items.push({
        id: op.id,
        group: `${op.machine}::draft`,
        machine: op.machine,
        content: `${op.label || String(op.id)}${
          op.mes_schedule_run_id ? " · MES" : ""
        }`,
        title: `${
          setupMinutes > 0
            ? baseTitle
            : `${op.operation_name || op.operation_type || ""}, выполнение ${runMinutes} мин.`
        }${buildMesTitle(op)}`,
        start: start * 60000,
        end: end * 60000,
        type: "range",
        style: `background: linear-gradient(to right, ${colors.setup} 0%, ${colors.setup} ${setupPercent}%, ${colors.work} ${setupPercent}%, ${colors.work} 100%); border-color: ${colors.border};`,
        baseClassName,
        className: buildSelectionClassName(
          baseClassName,
          op.order_item_id,
          selectedOrderItemIdRef.current
        ),
        order_item_id: op.order_item_id,
        order_id: op.order_id,
        order_no: op.order_no,
        product_name: op.product_name,
        quantity: op.quantity,
        operation_name: op.operation_name,
        operation_type: op.operation_type,
        mes_schedule_run_id: op.mes_schedule_run_id,
        mes_schedule_operation_id: op.mes_schedule_operation_id,
        mes_schedule_status: op.mes_schedule_status,
        mes_operation_status: op.mes_operation_status,
      });
    }

    const groups = Array.from(groupMap.values());
    const nextGroups = new DataSet(groups);
    const nextItems = new DataSet(items);

    groupsRef.current = nextGroups;
    itemsRef.current = nextItems;

    timelineRef.current.setGroups(nextGroups);
    timelineRef.current.setItems(nextItems);

    timelineRef.current.setCustomTime(
      freezeHorizon * 60000,
      FREEZE_LINE_ID
    );

    if (items.length > 0) {
      const minStart = Math.min(...items.map((i) => Number(new Date(i.start))));
      const maxEnd = Math.max(...items.map((i) => Number(new Date(i.end))));
      const pad = 60 * 60000;

      timelineRef.current.setWindow(minStart - pad, maxEnd + pad, {
        animation: false,
      });
    }
  }, [
    data,
    machines,
    backgroundIntervals,
    activeOverlayData,
    showActiveOverlay,
    freezeHorizonMinutes,
  ]);

  return (
    <>
      <style>
        {`
          .vis-item.frozen-operation {
            background-color: #d9e2ec;
            border-color: #6b7c93;
            color: #1f2933;
          }

          .vis-item.normal-operation {
            background-color: #d7f5d7;
            border-color: #4caf50;
          }

          .vis-timeline {
            border: none;
            box-shadow: none;
            outline: none;
          }

          .vis-item.calendar-between-shifts {
            background-image: repeating-linear-gradient(
              135deg,
              rgba(0, 0, 0, 0.05) 0,
              rgba(0, 0, 0, 0.05) 4px,
              rgba(0, 0, 0, 0.015) 4px,
              rgba(0, 0, 0, 0.015) 10px
            );
            background-color: rgba(0, 0, 0, 0.015);
            border: none;
            z-index: 0;
          }

          .vis-item.calendar-shift-break {
            background-image: repeating-linear-gradient(
              135deg,
              rgba(42, 130, 75, 0.08) 0,
              rgba(42, 130, 75, 0.08) 4px,
              rgba(42, 130, 75, 0.02) 4px,
              rgba(42, 130, 75, 0.02) 10px
            );
            background-color: rgba(42, 130, 75, 0.02);
            border: none;
            z-index: 0;
          }

          .vis-foreground .vis-group.machine-group-coil_a,
          .vis-labelset .vis-label.machine-group-coil_a,
          .vis-foreground .vis-group.machine-group-coil_b,
          .vis-labelset .vis-label.machine-group-coil_b {
            background-color: rgba(130, 183, 240, 0.06);
          }

          .vis-foreground .vis-group.machine-group-bend,
          .vis-labelset .vis-label.machine-group-bend {
            background-color: rgba(144, 212, 160, 0.06);
          }

          .vis-foreground .vis-group.machine-group-face,
          .vis-labelset .vis-label.machine-group-face {
            background-color: rgba(241, 189, 114, 0.06);
          }

          .vis-foreground .vis-group.machine-group-heat,
          .vis-labelset .vis-label.machine-group-heat {
            background-color: rgba(199, 160, 238, 0.06);
          }

          .vis-foreground .vis-group.machine-group-coat_a,
          .vis-labelset .vis-label.machine-group-coat_a,
          .vis-foreground .vis-group.machine-group-coat_b,
          .vis-labelset .vis-label.machine-group-coat_b {
            background-color: rgba(238, 156, 162, 0.06);
          }

          .vis-labelset .vis-label.machine-active-group {
            height: 18px;
            min-height: 18px;
            font-size: 0;
            border-top: none;
            border-bottom: 1px solid #d0d0d0;
          }

          .vis-labelset .vis-label.machine-draft-group {
            height: 26px;
            min-height: 26px;
          }

          .vis-foreground .vis-group.machine-active-group {
            height: 18px;
            min-height: 18px;
            border-top: none;
            border-bottom: 1px solid #d0d0d0;
          }

          .vis-foreground .vis-group.machine-draft-group {
            height: 26px;
            min-height: 26px;
          }

          .vis-foreground .vis-group.machine-draft-group-with-overlay,
          .vis-labelset .vis-label.machine-draft-group-with-overlay {
            border-bottom-color: transparent;
          }

          .vis-foreground .vis-group.machine-draft-group-without-overlay,
          .vis-labelset .vis-label.machine-draft-group-without-overlay {
            border-bottom: 1px solid #d0d0d0;
          }

          .vis-labelset .vis-label.machine-active-group .vis-inner {
            display: none;
          }

          .vis-foreground .vis-group.machine-equipment-group-start,
          .vis-labelset .vis-label.machine-equipment-group-start {
            border-top: 1px solid #bdbdbd;
          }

          .vis-item.active-overlay-operation {
            background-color: rgba(120, 120, 120, 0.08);
            border-color: #666;
            border-style: dashed;
            color: transparent;
            height: 12px;
            min-height: 12px;
            z-index: 1;
          }

          .vis-item.normal-operation,
          .vis-item.frozen-operation {
            z-index: 2;
          }

          .vis-item.mes-created-operation {
            box-shadow: inset 0 0 0 2px #9e9e9e;
          }

          .vis-item.mes-released-operation {
            box-shadow: inset 0 0 0 3px #2e7d32;
          }

          .vis-item.mes-excluded-operation {
            opacity: 0.55;
          }

          .vis-item.dimmed-by-order-selection {
            opacity: 0.25;
          }

          .vis-item.selected-order-item-operation {
            opacity: 1;
            filter: saturate(1.2) brightness(1.04);
            z-index: 20;
          }

          .vis-item.selected-order-item-operation.vis-selected {
            filter: saturate(1.2) brightness(1.04);
          }

          .vis-custom-time {
            background-color: #d32f2f;
            width: 3px;
          }

          .vis-custom-time > div {
            color: #d32f2f;
            font-weight: bold;
            white-space: nowrap;
          }
        `}
      </style>
      {showActiveOverlay && (
        <div
          style={{
            marginBottom: "8px",
            padding: "8px",
            border: "1px dashed #999",
            background: "#f7f7f7",
          }}
        >
          Включено наложение активного плана: серый пунктир показывает исходное положение операций.
        </div>
      )}
      <div
        ref={containerRef}
        style={{ height: "500px" }}
      />
    </>
  );
}

