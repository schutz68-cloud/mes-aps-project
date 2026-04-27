import { useEffect, useRef } from "react";
import { Timeline } from "vis-timeline/standalone";
import { DataSet } from "vis-data";
import "vis-timeline/styles/vis-timeline-graph2d.css";

const FREEZE_LINE_ID = "freeze-horizon-line";

export default function Gantt({ data, onMove, freezeHorizonMinutes }) {
  const containerRef = useRef(null);
  const timelineRef = useRef(null);
  const itemsRef = useRef(new DataSet([]));
  const groupsRef = useRef(new DataSet([]));
  const onMoveRef = useRef(onMove);
  const freezeHorizonRef = useRef(freezeHorizonMinutes ?? 0);

  useEffect(() => {
    onMoveRef.current = onMove;
  }, [onMove]);

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
        groupOrder: "content",
        selectable: true,
        editable: {
          add: false,
          updateTime: true,
          updateGroup: true,
          remove: false,
        },
        onMove: (item, callback) => {
          const prev = itemsRef.current.get(item.id);

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

          callback(item);

          const payload = {
            id: item.id,
            machine: item.group,
            start: Math.floor(new Date(item.start).getTime() / 60000),
            end: Math.floor(new Date(item.end).getTime() / 60000),
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

    for (const op of data) {
      const start = Number(op.start);
      const end = Number(op.end);
      const isFrozen = start < freezeHorizon;

      groupMap.set(op.machine, { id: op.machine, content: String(op.machine) });

      items.push({
        id: op.id,
        group: op.machine,
        content: isFrozen ? `🔒 Op ${op.id}` : `Op ${op.id}`,
        start: start * 60000,
        end: end * 60000,
        type: "range",
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
  }, [data, freezeHorizonMinutes]);

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

      {/* <div
        style={{
          marginBottom: "8px",
          fontSize: "14px",
          color: "#555",
        }}
      >
        Красная вертикальная линия — граница замороженной зоны:{" "}
        {Number(freezeHorizonMinutes ?? 0)} мин.
      </div> */}

      <div
        ref={containerRef}
        style={{ height: "500px", border: "1px solid gray" }}
      />
    </>
  );
}

