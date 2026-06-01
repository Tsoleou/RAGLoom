import { useState } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type EdgeProps,
} from "@xyflow/react";

/**
 * Edge that reveals the data-type flowing through it on hover. A fat,
 * transparent overlay path widens the hover hit-area so the thin visible
 * edge is easy to land on; the label only shows while hovered to avoid
 * permanently cluttering the graph. Uses orthogonal (smooth-step) routing —
 * right-angle segments read tidier than beziers across the row-aligned
 * pipeline.
 */
export function DataEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  data,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 8,
  });
  const [hover, setHover] = useState(false);
  const dataType = (data as { dataType?: string } | undefined)?.dataType;

  return (
    <>
      <BaseEdge id={id} path={edgePath} style={style} markerEnd={markerEnd} />
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={22}
        style={{ pointerEvents: "stroke" }}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
      />
      {hover && dataType && (
        <EdgeLabelRenderer>
          <div
            className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-[#1a1a1a] border border-[#333] text-[#c8c8c8] shadow-md shadow-black/30"
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: "none",
            }}
          >
            {dataType}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
