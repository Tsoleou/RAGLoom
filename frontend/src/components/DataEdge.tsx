import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  useReactFlow,
  type EdgeProps,
} from "@xyflow/react";
import { X } from "lucide-react";

// Bright gold reserved for the selected edge — distinct from the orange data
// spine and the faint grey context lines, so a clicked edge reads instantly.
const SELECTED_STROKE = "#ffcf5c";

/**
 * Edge that reveals the data-type flowing through it on hover, and surfaces a
 * one-click × button to remove the connection. A fat, transparent overlay path
 * widens the hover hit-area so the thin visible edge is easy to land on; the
 * label + remove button only show while hovered to avoid permanently
 * cluttering the graph. Uses orthogonal (smooth-step) routing — right-angle
 * segments read tidier than beziers across the row-aligned pipeline.
 *
 * When selected, the edge is repainted bright gold and wrapped in a soft halo
 * so you can trace where it connects through a tangle of other lines (paired
 * with `elevateEdgesOnSelect` on the canvas, which floats it above the rest).
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
  selected,
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
  const { setEdges } = useReactFlow();
  const dataType = (data as { dataType?: string } | undefined)?.dataType;

  // Selected edge: thicken + repaint gold, drop the dimming the context lines
  // carry so even a faint line pops to the foreground when clicked.
  const baseWidth =
    typeof style?.strokeWidth === "number" ? style.strokeWidth : 2.5;
  const visibleStyle: CSSProperties = selected
    ? { ...style, stroke: SELECTED_STROKE, strokeWidth: baseWidth + 1.5, opacity: 1 }
    : (style as CSSProperties);
  const haloWidth = baseWidth + 1.5 + 7;

  // Hover hand-off: moving the cursor from the SVG hit-path onto the HTML
  // remove button fires mouseleave on the path BEFORE mouseenter on the button.
  // Hiding immediately would unmount the button mid-handoff (it'd flicker and
  // be unclickable). So leaving only *schedules* a hide; entering the button
  // cancels it. Same pattern as a menu staying open while you reach a submenu.
  const hideTimer = useRef<number | null>(null);
  const show = () => {
    if (hideTimer.current !== null) {
      clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
    setHover(true);
  };
  const scheduleHide = () => {
    if (hideTimer.current !== null) clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => setHover(false), 80);
  };
  useEffect(
    () => () => {
      if (hideTimer.current !== null) clearTimeout(hideTimer.current);
    },
    []
  );

  return (
    <>
      {selected && (
        <path
          d={edgePath}
          fill="none"
          stroke={SELECTED_STROKE}
          strokeWidth={haloWidth}
          strokeOpacity={0.22}
          strokeLinecap="round"
          style={{ pointerEvents: "none" }}
        />
      )}
      <BaseEdge id={id} path={edgePath} style={visibleStyle} markerEnd={markerEnd} />
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={22}
        style={{ pointerEvents: "stroke", cursor: "grab" }}
        onMouseEnter={show}
        onMouseLeave={scheduleHide}
      />
      {hover && (
        <EdgeLabelRenderer>
          {/* Mid-edge cluster: data-type chip + remove button. pointerEvents
              are enabled here (the chip/button sit above the SVG path), and the
              wrapper keeps `hover` alive while the cursor is on the button so it
              doesn't vanish before the click lands. */}
          <div
            className="flex items-center gap-1.5 nodrag nopan"
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: "all",
            }}
            onMouseEnter={show}
            onMouseLeave={scheduleHide}
          >
            {dataType && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-[#1a1a1a] border border-[#333] text-[#c8c8c8] shadow-md shadow-black/30">
                {dataType}
              </span>
            )}
            <button
              type="button"
              aria-label="Remove connection"
              title="Remove connection"
              className="flex items-center justify-center w-4 h-4 rounded-full bg-[#d04040] hover:bg-[#e05050] text-white shadow-md shadow-black/40 transition-colors cursor-pointer"
              onClick={(e) => {
                e.stopPropagation();
                setEdges((eds) => eds.filter((edge) => edge.id !== id));
              }}
            >
              <X className="w-2.5 h-2.5" strokeWidth={3} />
            </button>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
