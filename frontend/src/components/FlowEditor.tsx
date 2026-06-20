import { useCallback, useRef, useState, useEffect, useMemo } from "react";
import type { CSSProperties } from "react";
import {
  ReactFlow,
  Controls,
  MiniMap,
  Background,
  BackgroundVariant,
  addEdge,
  reconnectEdge,
  useNodesState,
  useEdgesState,
  type Connection,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { EditableNode } from "./EditableNode";
import { DataEdge } from "./DataEdge";
import { NodePalette } from "./NodePalette";
import { NodeConfigPanel } from "./NodeConfigPanel";
import { ExecutionBar } from "./ExecutionBar";
import { Avatar } from "./avatar/Avatar";
import { BatchEvalModal } from "./BatchEvalModal";
import { useExecution } from "../hooks/useExecution";
import { useNodeTypes } from "../hooks/useNodeTypes";
import {
  parseChatbotOutput,
  emotionToAvatarState,
  getEmotionTheme,
} from "../utils/chatbotOutput";
import type {
  FlowNode,
  FlowEdge,
  EditableNodeData,
  NodeTypeDef,
} from "../types/pipeline";

const nodeTypes = { editable: EditableNode };
const edgeTypes = { data: DataEdge };

// ── Edge visual layering ───────────────────────────────────────
//
// The default graph has two broadcast hubs (query fanned to every gate,
// format_hint/system_prompt fanned from SystemPrompt) that draw long lines
// across the canvas. Painting every edge the same bold animated orange makes
// the graph read as a tangle. So we split edges into two tiers:
//   • primary data spine (documents→chunks→results→prompt→answer): bold,
//     bright, animated — this is the flow the eye should follow.
//   • context / control lines (query, format_hint, system_prompt, refs,
//     product_id): thin, faint, static — present but recessive.
const CONTEXT_DATA_TYPES = new Set([
  "query",
  "format_hint",
  "system_prompt",
  "reference",
  "product_id",
]);

function edgeAppearance(dataType?: string): { style: CSSProperties; animated: boolean } {
  if (dataType === "metric") {
    return { style: { strokeWidth: 2, stroke: "#a070d0" }, animated: true };
  }
  if (dataType && CONTEXT_DATA_TYPES.has(dataType)) {
    return { style: { strokeWidth: 1.5, stroke: "#7a6a55", opacity: 0.4 }, animated: false };
  }
  return { style: { strokeWidth: 2.5, stroke: "#e07830" }, animated: true };
}

let nodeIdCounter = 0;
function nextNodeId() {
  return `node_${++nodeIdCounter}`;
}

function createNodeFromDef(typeDef: NodeTypeDef, position: { x: number; y: number }): FlowNode {
  const defaultParams: Record<string, string | number | boolean> = {};
  typeDef.params.forEach((p) => {
    defaultParams[p.name] = p.default;
  });

  return {
    id: nextNodeId(),
    type: "editable",
    position,
    data: {
      typeId: typeDef.typeId,
      label: typeDef.label,
      labelEn: typeDef.labelEn,
      inputs: typeDef.inputs,
      outputs: typeDef.outputs,
      params: defaultParams,
      status: "idle",
      preview: "",
    },
  };
}

// ── Server-default graph materialization ──────────────────────
//
// Single source of truth for the default pipeline lives on the server
// (`GET /api/default-graph`). The editor fetches it on mount, the chat
// path uses the same builder. No more "editor default vs chat default"
// drift.

type SerializedGraphNode = {
  id: string;
  type: string;
  position: { x: number; y: number };
  params: Record<string, string | number | boolean>;
};
type SerializedGraphEdge = {
  source: string;
  target: string;
  sourceHandle: string;
  targetHandle: string;
};
type SerializedGraph = { nodes: SerializedGraphNode[]; edges: SerializedGraphEdge[] };

function materializeServerGraph(
  g: SerializedGraph,
  byTypeId: Record<string, NodeTypeDef>,
): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const nodes: FlowNode[] = g.nodes
    .map((sn) => {
      const def = byTypeId[sn.type];
      if (!def) return null;
      return {
        id: sn.id,
        type: "editable",
        position: sn.position,
        data: {
          typeId: def.typeId,
          label: def.label,
          labelEn: def.labelEn,
          inputs: def.inputs,
          outputs: def.outputs,
          params: { ...sn.params },
          status: "idle",
          preview: "",
        },
      } as FlowNode;
    })
    .filter((n): n is FlowNode => n !== null);

  // Resolve each edge's data-type from its source port so the DataEdge can
  // surface it on hover (and metric edges keep their distinct color).
  const typeIdById: Record<string, string> = {};
  g.nodes.forEach((sn) => {
    typeIdById[sn.id] = sn.type;
  });

  const edges: FlowEdge[] = g.edges.map((se, i) => {
    const srcDef = byTypeId[typeIdById[se.source]];
    const dataType = srcDef?.outputs.find((p) => p.name === se.sourceHandle)?.dataType;
    const { style, animated } = edgeAppearance(dataType);
    return {
      id: `e-${se.source}-${se.target}-${i}`,
      source: se.source,
      target: se.target,
      sourceHandle: se.sourceHandle,
      targetHandle: se.targetHandle,
      type: "data",
      animated,
      data: { dataType },
      style,
    };
  });

  return { nodes, edges };
}

/** 檢查連線的 port 型別是否相容 */
function isConnectionValid(
  connection: Connection,
  nodes: FlowNode[],
): boolean {
  const sourceNode = nodes.find((n) => n.id === connection.source);
  const targetNode = nodes.find((n) => n.id === connection.target);
  if (!sourceNode || !targetNode) return false;

  const sourcePort = sourceNode.data.outputs.find(
    (p) => p.name === connection.sourceHandle
  );
  const targetPort = targetNode.data.inputs.find(
    (p) => p.name === connection.targetHandle
  );
  if (!sourcePort || !targetPort) return false;

  return sourcePort.dataType === targetPort.dataType;
}

/** Enforce one source per input port. The backend resolves each input by
 *  `inputs[targetHandle] = ...` (api/engine.py), so a second edge into the same
 *  input port silently overwrites the first at run time. Mirror that on the
 *  canvas: drop any edge already landing on (target, targetHandle) so a connect
 *  / rewire replaces rather than stacks. `keepId` spares the edge being
 *  reconnected onto its own handle. Output (source) ports are untouched —
 *  fan-out (one output → many inputs) is legitimate. */
function pruneTargetHandle(
  edges: FlowEdge[],
  target: string | null,
  targetHandle: string | null | undefined,
  keepId?: string,
): FlowEdge[] {
  return edges.filter(
    (e) =>
      e.id === keepId ||
      !(e.target === target && (e.targetHandle ?? "") === (targetHandle ?? "")),
  );
}

export function FlowEditor() {
  // Canvas starts empty; populated from /api/default-graph below.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [nodes, setNodes, onNodesChange] = useNodesState<any>([]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance<any> | null>(null);
  const draggedTypeRef = useRef<NodeTypeDef | null>(null);

  const { isRunning, nodeStatuses, execute, cancel } = useExecution();
  const { byTypeId, loading: nodeTypesLoading } = useNodeTypes();

  // Fetch the server-side default graph on mount — single source of truth
  // shared with the chat path. Waits for node-types to load first so the
  // materializer can resolve labels / ports / params for each node.
  useEffect(() => {
    if (nodeTypesLoading) return;
    let cancelled = false;
    fetch("/api/default-graph")
      .then((r) => r.json())
      .then((g: SerializedGraph) => {
        if (cancelled) return;
        const materialized = materializeServerGraph(g, byTypeId);
        setNodes(materialized.nodes);
        setEdges(materialized.edges);
      })
      .catch((e) => console.error("[FlowEditor] default graph fetch failed:", e));
    return () => {
      cancelled = true;
    };
    // Re-run if byTypeId reference changes (i.e., node-types finish loading)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeTypesLoading]);

  // 同步 nodeStatuses 到 nodes 的 data
  useEffect(() => {
    if (Object.keys(nodeStatuses).length === 0) return;

    setNodes((nds) =>
      nds.map((node) => {
        const status = nodeStatuses[node.id];
        if (!status) return node;
        return {
          ...node,
          data: {
            ...node.data,
            status: status.status,
            preview: status.preview || node.data.preview,
          },
        };
      })
    );
  }, [nodeStatuses, setNodes]);

  // ── Drag & Drop ─────────────────────────────────────────────

  const onDragStart = useCallback((typeDef: NodeTypeDef) => {
    draggedTypeRef.current = typeDef;
  }, []);

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      const typeId = event.dataTransfer.getData("application/rag-node-type");
      const typeDef = byTypeId[typeId];
      if (!typeDef || !rfInstance || !reactFlowWrapper.current) return;

      const bounds = reactFlowWrapper.current.getBoundingClientRect();
      const position = rfInstance.screenToFlowPosition({
        x: event.clientX - bounds.left,
        y: event.clientY - bounds.top,
      });

      const newNode = createNodeFromDef(typeDef, position);
      setNodes((nds) => [...nds, newNode]);
    },
    [rfInstance, setNodes, byTypeId]
  );

  // ── Connections ─────────────────────────────────────────────

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!isConnectionValid(connection, nodes)) {
        console.warn("[FlowEditor] Invalid connection: port types don't match");
        return;
      }
      // Style new edges by their source-port dataType so the primary data
      // spine, context/control lines, and the eval (metric) family each read
      // distinctly — same tiering as the materialized default graph.
      const sourceNode = nodes.find((n) => n.id === connection.source);
      const sourcePort = sourceNode?.data.outputs.find(
        (p: { name: string; dataType: string }) => p.name === connection.sourceHandle
      );
      const { style, animated } = edgeAppearance(sourcePort?.dataType);
      // Replace any edge already on this input port (single-source rule).
      setEdges((eds) =>
        addEdge(
          {
            ...connection,
            type: "data",
            animated,
            data: { dataType: sourcePort?.dataType },
            style,
          },
          pruneTargetHandle(eds, connection.target, connection.targetHandle)
        )
      );
    },
    [nodes, setEdges]
  );

  // Reconnect: drag an edge endpoint to reroute, or to empty space to delete.
  // The ref tracks whether the drag landed on a valid handle — if not,
  // onReconnectEnd removes the edge.
  const edgeReconnectSuccessful = useRef(true);

  const onReconnectStart = useCallback(() => {
    edgeReconnectSuccessful.current = false;
  }, []);

  const onReconnect = useCallback(
    (oldEdge: FlowEdge, newConnection: Connection) => {
      if (!isConnectionValid(newConnection, nodes)) {
        console.warn("[FlowEditor] Invalid reconnect: port types don't match");
        return;
      }
      edgeReconnectSuccessful.current = true;
      // Recompute the edge's appearance + data-type from the NEW source port so
      // a rewired edge reflects the data flow it now carries — not the stale
      // styling/label inherited from the old endpoint. reconnectEdge keeps the
      // same edge id, so we re-style that one edge in place afterwards.
      const sourceNode = nodes.find((n) => n.id === newConnection.source);
      const sourcePort = sourceNode?.data.outputs.find(
        (p: { name: string; dataType: string }) => p.name === newConnection.sourceHandle
      );
      const { style, animated } = edgeAppearance(sourcePort?.dataType);
      // shouldReplaceId:false keeps the edge's id stable across the reconnect —
      // reconnectEdge otherwise mints a fresh id from the new connection, which
      // would make the restyle .map below match nothing and silently no-op.
      // Drop any *other* edge already on the new target handle before the
      // reconnect, so rewiring onto an occupied input replaces it (single
      // source per input port) — keepId spares the edge being dragged.
      setEdges((els) => {
        const pruned = pruneTargetHandle(
          els,
          newConnection.target,
          newConnection.targetHandle,
          oldEdge.id
        );
        return reconnectEdge(oldEdge, newConnection, pruned, { shouldReplaceId: false }).map((e) =>
          e.id === oldEdge.id
            ? { ...e, type: "data", animated, data: { ...e.data, dataType: sourcePort?.dataType }, style }
            : e
        );
      });
    },
    [nodes, setEdges]
  );

  const onReconnectEnd = useCallback(
    (_: unknown, edge: FlowEdge) => {
      if (!edgeReconnectSuccessful.current) {
        setEdges((eds) => eds.filter((e) => e.id !== edge.id));
      }
    },
    [setEdges]
  );

  // ── Node Selection ──────────────────────────────────────────

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: FlowNode) => {
      setSelectedNodeId(node.id);
    },
    []
  );

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null);
  }, []);

  // ── Param Change ────────────────────────────────────────────

  const onParamChange = useCallback(
    (nodeId: string, paramName: string, value: string | number | boolean) => {
      setNodes((nds) =>
        nds.map((n) =>
          n.id === nodeId
            ? {
                ...n,
                data: {
                  ...n.data,
                  params: { ...n.data.params, [paramName]: value },
                },
              }
            : n
        )
      );
    },
    [setNodes]
  );

  // ── Execution ───────────────────────────────────────────────

  const handleRun = useCallback(() => {
    // Reset statuses
    setNodes((nds) =>
      nds.map((n) => ({
        ...n,
        data: { ...n.data, status: "idle" as const, preview: "" },
      }))
    );
    execute(nodes, edges);
  }, [nodes, edges, execute, setNodes]);

  const handleClear = useCallback(() => {
    setNodes([]);
    setEdges([]);
    setSelectedNodeId(null);
  }, [setNodes, setEdges]);

  // Profile state: each profile carries the full {nodes, edges} so chat and
  // editor see the same setup. Same shape as /api/default-graph.
  const [savedProfiles, setSavedProfiles] = useState<Record<string, { graph?: SerializedGraph }>>({});
  const [batchEvalOpen, setBatchEvalOpen] = useState(false);

  useEffect(() => {
    fetch("/api/profiles")
      .then((r) => r.json())
      .then((data) => setSavedProfiles(data.profiles ?? {}))
      .catch(() => {});
  }, []);

  const handleSaveProfile = useCallback(async (name: string) => {
    const graph: SerializedGraph = {
      nodes: nodes.map((n) => ({
        id: n.id,
        type: n.data.typeId,
        position: n.position,
        params: n.data.params,
      })),
      edges: edges.map((e) => ({
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceHandle ?? "",
        targetHandle: e.targetHandle ?? "",
      })),
    };
    await fetch("/api/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, graph }),
    });
    setSavedProfiles((prev) => ({ ...prev, [name]: { graph } }));
  }, [nodes, edges]);

  const handleLoadProfile = useCallback((name: string) => {
    const graph = savedProfiles[name]?.graph;
    if (!graph) return;
    const { nodes: restoredNodes, edges: restoredEdges } = materializeServerGraph(graph, byTypeId);
    setNodes(restoredNodes);
    setEdges(restoredEdges);
  }, [savedProfiles, setNodes, setEdges, byTypeId]);

  // Build the SerializedGraph for the batch-eval modal. Same shape we send
  // when saving a profile, computed on demand.
  const buildSerializedGraph = useCallback((): SerializedGraph => ({
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.data.typeId,
      position: n.position,
      params: n.data.params,
    })),
    edges: edges.map((e) => ({
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle ?? "",
      targetHandle: e.targetHandle ?? "",
    })),
  }), [nodes, edges]);

  const hasEvalCaseLoader = useMemo(
    () => nodes.some((n) => n.data.typeId === "eval_case_loader"),
    [nodes]
  );

  // ── Selected node data ──────────────────────────────────────

  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === selectedNodeId),
    [nodes, selectedNodeId]
  );

  // ── Robot avatar state ─────────────────────────────────────

  const { avatarState, avatarMessage } = useMemo(() => {
    if (!isRunning && Object.keys(nodeStatuses).length === 0) {
      return { avatarState: "idle" as const, avatarMessage: "Ready when you are!" };
    }

    // Check for errors
    const errorEntry = Object.entries(nodeStatuses).find(([, v]) => v.status === "error");
    if (errorEntry) {
      const errorNode = nodes.find((n) => n.id === errorEntry[0]);
      const label = errorNode?.data?.label || errorEntry[0];
      return { avatarState: "error" as const, avatarMessage: `${label} failed` };
    }

    // Find currently running node
    const runningEntry = Object.entries(nodeStatuses).find(([, v]) => v.status === "running");
    if (runningEntry) {
      const runningNode = nodes.find((n) => n.id === runningEntry[0]);
      const typeId = runningNode?.data?.typeId || "";
      const label = runningNode?.data?.label || runningEntry[0];

      if (typeId === "generator") {
        return { avatarState: "talk" as const, avatarMessage: `${label}...` };
      }
      return { avatarState: "think" as const, avatarMessage: `${label}...` };
    }

    // All done
    if (!isRunning) {
      const allDone = Object.values(nodeStatuses).every((v) => v.status === "done");
      if (allDone && Object.keys(nodeStatuses).length > 0) {
        // Only chatbot mode carries an LLM emotion — let it drive the avatar.
        // Professional mode stays neutral ("idle") because "happy" would be a
        // fake emotion: nothing in the pipeline actually produced a feeling.
        for (const n of nodes) {
          if (n.data?.typeId !== "result_display") continue;
          const parsed = parseChatbotOutput(n.data?.preview || "");
          if (parsed) {
            const theme = getEmotionTheme(parsed.emotion);
            return {
              avatarState: emotionToAvatarState(parsed.emotion),
              avatarMessage: `Feeling: ${theme.label.toLowerCase()}`,
            };
          }
        }
        return { avatarState: "idle" as const, avatarMessage: "Done." };
      }
    }

    return { avatarState: "think" as const, avatarMessage: "Processing..." };
  }, [isRunning, nodeStatuses, nodes]);

  return (
    <div className="flex h-full">
      {/* Left: Node Palette */}
      <NodePalette onDragStart={onDragStart} />

      {/* Center: Canvas + Toolbar */}
      <div className="flex-1 flex flex-col min-w-0">
        <ExecutionBar
          isRunning={isRunning}
          nodeCount={nodes.length}
          edgeCount={edges.length}
          onRun={handleRun}
          onCancel={cancel}
          onClear={handleClear}
          onSaveProfile={handleSaveProfile}
          profiles={savedProfiles}
          onLoadProfile={handleLoadProfile}
          canRunBatch={hasEvalCaseLoader}
          onRunBatch={() => setBatchEvalOpen(true)}
        />
        <BatchEvalModal
          open={batchEvalOpen}
          graph={buildSerializedGraph()}
          onClose={() => setBatchEvalOpen(false)}
        />

        {/* Canvas + docked config panel share a row so the panel never
            occludes nodes (it used to be an absolute overlay). */}
        <div className="flex-1 flex min-h-0">
        <div className="flex-1 relative min-w-0" ref={reactFlowWrapper}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onReconnect={onReconnect}
            onReconnectStart={onReconnectStart}
            onReconnectEnd={onReconnectEnd}
            onInit={setRfInstance}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            onDragOver={onDragOver}
            onDrop={onDrop}
            isValidConnection={(c) => isConnectionValid(c as Connection, nodes)}
            connectionRadius={38}
            elevateEdgesOnSelect
            fitView
            fitViewOptions={{ padding: 0.3 }}
            nodesDraggable
            nodesConnectable
            elementsSelectable
            deleteKeyCode={["Backspace", "Delete"]}
            minZoom={0.2}
            maxZoom={3}
          >
            <Controls position="bottom-left" />
            <MiniMap
              position="bottom-right"
              maskColor="rgba(0,0,0,0.6)"
              nodeColor={(node) => {
                const data = node.data as EditableNodeData;
                switch (data.status) {
                  case "running": return "#e07830";
                  case "done": return "#50a070";
                  case "error": return "#d04040";
                  case "blocked": return "#f0a040";
                  default: return "#444";
                }
              }}
            />
            <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="#333" />
          </ReactFlow>

          {/* Avatar — bottom-right, to the left of MiniMap */}
          <div className="absolute bottom-3 right-[220px] z-[5] pointer-events-none">
            <Avatar state={avatarState} message={avatarMessage} size={100} />
          </div>
        </div>

        {/* Right: Config Panel — docked column, renders nothing when no
            node is selected so the canvas reclaims the full width. */}
        <NodeConfigPanel
          nodeId={selectedNodeId}
          data={selectedNode?.data ?? null}
          onParamChange={onParamChange}
          onClose={() => setSelectedNodeId(null)}
        />
        </div>
      </div>
    </div>
  );
}
