import { useEffect, useRef } from "react";
import { Timeline } from "vis-timeline/standalone";
import { DataSet } from "vis-data";
import "vis-timeline/styles/vis-timeline-graph2d.css";
export default function Gantt({ data, onMove }) {
  const containerRef = useRef(null);
  const timelineRef = useRef(null);
  const itemsRef = useRef(new DataSet([]));
  const groupsRef = useRef(new DataSet([]));
  const onMoveRef = useRef(onMove);
  useEffect(() => {
    onMoveRef.current = onMove;
  }, [onMove]);
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
          callback(item);
          const payload = {
            id: item.id,
            machine: item.group,
            start: Math.floor(new Date(item.start).getTime() / 60000),
            end: Math.floor(new Date(item.end).getTime() / 60000),
          };
          Promise.resolve(onMoveRef.current?.(payload)).catch(() => {
            if (prev) itemsRef.current.update(prev);
          });
        },
      }
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
    const groupMap = new Map();
    const items = [];
    for (const op of data) {
      groupMap.set(op.machine, { id: op.machine, content: String(op.machine) });
      items.push({
        id: op.id,
        group: op.machine,
        content: `Op ${op.id}`,
        start: Number(op.start) * 60000,
        end: Number(op.end) * 60000,
        type: "range",
      });
    }
    const groups = Array.from(groupMap.values());
    const nextGroups = new DataSet(groups);
    const nextItems = new DataSet(items);
    groupsRef.current = nextGroups;
    itemsRef.current = nextItems;
    timelineRef.current.setGroups(nextGroups);
    timelineRef.current.setItems(nextItems);
    if (items.length > 0) {
      const minStart = Math.min(...items.map((i) => Number(new Date(i.start))));
      const maxEnd = Math.max(...items.map((i) => Number(new Date(i.end))));
      const pad = 60 * 60000;
      timelineRef.current.setWindow(minStart - pad, maxEnd + pad, {
        animation: false,
      });
    }
  }, [data]);
  return (
    <div
      ref={containerRef}
      style={{ height: "500px", border: "1px solid gray" }}
    />
  );
}


