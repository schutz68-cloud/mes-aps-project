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

export default function Gantt({
  data,
  machines,
  onMove,
  freezeHorizonMinutes,
  canEdit,
}) {
  const containerRef = useRef(null);
  const timelineRef = useRef(null);
  const itemsRef = useRef(new DataSet([]));
  const groupsRef = useRef(new DataSet([]));
  const onMoveRef = useRef(onMove);
  const freezeHorizonRef = useRef(freezeHorizonMinutes ?? 0);
  const canEditRef = useRef(canEdit);

  useEffect(() => {
    onMoveRef.current = onMove;
  }, [onMove]);

  useEffect(() => {
    canEditRef.current = Boolean(canEdit);
  }, [canEdit]);

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
    if (!containerRef.current || timelineRef.current) return;

    const timeline = new Timeline(
      containerRef.current,
      itemsRef.current,
      groupsRef.current,
      {
        groupOrder: (a, b) =>
          (a.order ?? 999) - (b.order ?? 999) ||
          String(a.content).localeCompare(String(b.content), "ru"),
        selectable: true,
        editable: {
          add: false,
          updateTime: Boolean(canEditRef.current),
          updateGroup: Boolean(canEditRef.current),
          remove: false,
        },
        onMove: (item, callback) => {
          const prev = itemsRef.current.get(item.id);

          if (!canEditRef.current) {
            callback(prev || null);
            alert("Редактировать можно только черновую версию плана");
            return;
          }

          if (!prev) {
            callback(null);
            return;
          }

          const originalStart = Math.floor(new Date(prev.start).getTime() / 60000);

          if (originalStart < freezeHorizonRef.current) {
            callback(prev);

            alert(
              "Операция находится в замороженной зоне плана и не может быть перемещена"
            );

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
            machine: correctedItem.group,
            start: Math.floor(Number(new Date(correctedItem.start)) / 60000),
            end: Math.floor(Number(new Date(correctedItem.end)) / 60000),
          };

          Promise.resolve(onMoveRef.current?.(payload)).catch((error) => {
            if (prev) itemsRef.current.update(prev);

            alert(
              "Нельзя переместить операцию: " +
                (error?.message || "неизвестная ошибка")
            );
          });
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
      if (!canEditRef.current && properties?.item) {
        alert(
          "Active-план доступен только для просмотра. Создайте черновую копию для редактирования."
        );
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

    for (const machine of machines || []) {
      const groupOrder = MACHINE_GROUP_ORDER[machine.group_id] ?? 999;

      groupMap.set(machine.id, {
        id: machine.id,
        content: machine.name || String(machine.id),
        order: groupOrder,
      });
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
      const groupOrder = MACHINE_GROUP_ORDER[op.machine_group_id] ?? 999;

      groupMap.set(op.machine, {
        id: op.machine,
        content: op.machine_name || String(op.machine),
        order: groupOrder,
      });

      items.push({
        id: op.id,
        group: op.machine,
        content: op.label || String(op.id),
        title: `${
          op.operation_name || op.operation_type || ""
        }, наладка ${setupMinutes} мин., выполнение ${runMinutes} мин.`,
        start: start * 60000,
        end: end * 60000,
        type: "range",
        style: `background: linear-gradient(to right, ${colors.setup} 0%, ${colors.setup} ${setupPercent}%, ${colors.work} ${setupPercent}%, ${colors.work} 100%); border-color: ${colors.border};`,
        className: isFrozen ? "frozen-operation" : "normal-operation",
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
  }, [data, machines, freezeHorizonMinutes]);

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
      {!canEdit && (
        <div
          style={{
            marginBottom: "8px",
            padding: "8px",
            border: "1px solid #ccc",
            background: "#f7f7f7",
          }}
        >
          Режим просмотра: active-план нельзя изменять. Создайте черновую копию для редактирования.
        </div>
      )}
      <div
        ref={containerRef}
        style={{ height: "500px", border: "1px solid gray" }}
      />
    </>
  );
}

