import { useCallback, useRef, useState, useEffect, useMemo } from "react";
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
import { NodePalette } from "./NodePalette";
import { NodeConfigPanel } from "./NodeConfigPanel";
import { ExecutionBar } from "./ExecutionBar";
import { RobotAvatar } from "./RobotAvatar";
import { useExecution } from "../hooks/useExecution";
import { NODE_DEF_MAP, NODE_DEFINITIONS } from "../data/nodeDefinitions";
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

let nodeIdCounter = 0;
function nextNodeId() {
  return `node_${++nodeIdCounter}`;
}

function createNodeFromDef(typeDef: NodeTypeDef, position: { x: number; y: number }): FlowNode {
  const defaultParams: Record<string, string | number> = {};
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

// ── Default Pipeline ──────────────────────────────────────────

function buildDefaultPipeline(): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const GAP_X = 280;
  const ROW_Y_INGEST = 80;
  const ROW_Y_QUERY = 340;

  const defs: Record<string, NodeTypeDef> = {};
  NODE_DEFINITIONS.forEach((d) => { defs[d.typeId] = d; });

  // Ingest row: Loader → Chunker → Embedder → VectorStore
  const loader   = createNodeFromDef(defs["loader"],      { x: 0,          y: ROW_Y_INGEST });
  const chunker  = createNodeFromDef(defs["chunker"],     { x: GAP_X,      y: ROW_Y_INGEST });
  const embedder = createNodeFromDef(defs["embedder"],    { x: GAP_X * 2,  y: ROW_Y_INGEST });
  const vstore   = createNodeFromDef(defs["vectorstore"], { x: GAP_X * 3,  y: ROW_Y_INGEST });

  // Query row: QueryInput → Guardrail → Retriever → PromptBuilder → Generator → OutputCritic → ResultDisplay
  // QueryInput is 360px wide (inline question editor), so guardrail starts further right
  // to maintain a similar visual gap as the rest of the row.
  const QUERY_OFFSET = 420;
  const qinput    = createNodeFromDef(defs["query_input"],    { x: 0,                       y: ROW_Y_QUERY });
  const guardrail = createNodeFromDef(defs["guardrail"],      { x: QUERY_OFFSET,            y: ROW_Y_QUERY });
  const retriever = createNodeFromDef(defs["retriever"],      { x: QUERY_OFFSET + GAP_X,    y: ROW_Y_QUERY });
  const scopegate = createNodeFromDef(defs["scope_gate"],     { x: QUERY_OFFSET + GAP_X * 2, y: ROW_Y_QUERY });
  const pbuilder  = createNodeFromDef(defs["prompt_builder"], { x: QUERY_OFFSET + GAP_X * 3, y: ROW_Y_QUERY });
  const generator = createNodeFromDef(defs["generator"],      { x: QUERY_OFFSET + GAP_X * 4, y: ROW_Y_QUERY });
  const critic    = createNodeFromDef(defs["output_critic"],  { x: QUERY_OFFSET + GAP_X * 5, y: ROW_Y_QUERY });
  const display   = createNodeFromDef(defs["result_display"], { x: QUERY_OFFSET + GAP_X * 6, y: ROW_Y_QUERY });

  // System Prompt — below Generator, connects directly to it
  const sysprompt = createNodeFromDef(defs["system_prompt"],  { x: QUERY_OFFSET + GAP_X * 4, y: ROW_Y_QUERY + 200 });

  // Reference Loader — below PromptBuilder, always-on product reference
  const refloader = createNodeFromDef(defs["reference_loader"], { x: QUERY_OFFSET + GAP_X * 3, y: ROW_Y_QUERY + 200 });

  // Product Selector — below Retriever; default mode='rule' (string match against
  // collection metadata, zero LLM latency). Both collection and reference_data
  // are pre-wired so the user can flip mode to 'llm' with no re-wiring.
  const pselector = createNodeFromDef(defs["product_selector"], { x: QUERY_OFFSET + GAP_X, y: ROW_Y_QUERY + 200 });

  const nodes = [loader, chunker, embedder, vstore, qinput, guardrail, retriever, scopegate, pbuilder, generator, critic, display, sysprompt, refloader, pselector];

  const edgeStyle = { strokeWidth: 2, stroke: "#e07830" };
  const sysEdgeStyle = { strokeWidth: 2, stroke: "#70b0d0" };
  const guardEdgeStyle = { strokeWidth: 2, stroke: "#f0a040" };
  const criticEdgeStyle = { strokeWidth: 2, stroke: "#a070d0" };
  const refEdgeStyle = { strokeWidth: 2, stroke: "#60c080" };
  const selectorEdgeStyle = { strokeWidth: 2, stroke: "#d0a060" };
  // Cyan = scope gate (semantic-relevance threshold). Distinct from amber
  // brand-keyword guardrail so the two safety layers read at a glance.
  const scopeEdgeStyle = { strokeWidth: 2, stroke: "#40b0c0" };
  const edges: FlowEdge[] = [
    // Ingest chain
    { id: `e-${loader.id}-${chunker.id}`,   source: loader.id,   target: chunker.id,   sourceHandle: "documents",  targetHandle: "documents",  animated: true, style: edgeStyle },
    { id: `e-${chunker.id}-${embedder.id}`, source: chunker.id,  target: embedder.id,  sourceHandle: "chunks",     targetHandle: "chunks",     animated: true, style: edgeStyle },
    { id: `e-${chunker.id}-${vstore.id}`,   source: chunker.id,  target: vstore.id,    sourceHandle: "chunks",     targetHandle: "chunks",     animated: true, style: edgeStyle },
    { id: `e-${embedder.id}-${vstore.id}`,  source: embedder.id, target: vstore.id,    sourceHandle: "embeddings", targetHandle: "embeddings", animated: true, style: edgeStyle },
    // Query chain — query flows through guardrail first, retrieval through scope gate
    { id: `e-${qinput.id}-${guardrail.id}`,    source: qinput.id,    target: guardrail.id, sourceHandle: "query",      targetHandle: "query_in",   animated: true, style: guardEdgeStyle },
    { id: `e-${guardrail.id}-${retriever.id}`, source: guardrail.id, target: retriever.id, sourceHandle: "query_out",  targetHandle: "query",      animated: true, style: edgeStyle },
    { id: `e-${vstore.id}-${retriever.id}`,    source: vstore.id,    target: retriever.id, sourceHandle: "collection", targetHandle: "collection", animated: true, style: edgeStyle },
    // Scope Gate (cyan): retriever results + query in, results pass-through (or block)
    { id: `e-${retriever.id}-${scopegate.id}`, source: retriever.id, target: scopegate.id, sourceHandle: "results",    targetHandle: "results_in", animated: true, style: scopeEdgeStyle },
    { id: `e-${guardrail.id}-${scopegate.id}`, source: guardrail.id, target: scopegate.id, sourceHandle: "query_out",  targetHandle: "query",      animated: true, style: scopeEdgeStyle },
    { id: `e-${guardrail.id}-${pbuilder.id}`,  source: guardrail.id, target: pbuilder.id,  sourceHandle: "query_out",  targetHandle: "query",      animated: true, style: edgeStyle },
    { id: `e-${scopegate.id}-${pbuilder.id}`,  source: scopegate.id, target: pbuilder.id,  sourceHandle: "results_out", targetHandle: "results",    animated: true, style: edgeStyle },
    { id: `e-${pbuilder.id}-${generator.id}`,  source: pbuilder.id,  target: generator.id, sourceHandle: "prompt",     targetHandle: "prompt",     animated: true, style: edgeStyle },
    // Generator → OutputCritic → ResultDisplay (purple edges = self-critique loop)
    { id: `e-${generator.id}-${critic.id}`,    source: generator.id, target: critic.id,    sourceHandle: "answer",     targetHandle: "answer_in",  animated: true, style: criticEdgeStyle },
    { id: `e-${critic.id}-${display.id}`,      source: critic.id,    target: display.id,   sourceHandle: "answer_out", targetHandle: "answer",     animated: true, style: criticEdgeStyle },
    // System Prompt → Generator (light blue to distinguish): persona text + format hint
    { id: `e-${sysprompt.id}-${generator.id}`,     source: sysprompt.id, target: generator.id, sourceHandle: "system_prompt", targetHandle: "system_prompt", animated: true, style: sysEdgeStyle },
    { id: `e-${sysprompt.id}-${generator.id}-fmt`, source: sysprompt.id, target: generator.id, sourceHandle: "format_hint",   targetHandle: "format_hint",   animated: true, style: sysEdgeStyle },
    // Reference Loader → PromptBuilder (green = always-on product reference)
    { id: `e-${refloader.id}-${pbuilder.id}`, source: refloader.id, target: pbuilder.id, sourceHandle: "reference_data", targetHandle: "reference_data", animated: true, style: refEdgeStyle },
    // Product Selector wiring (tan): query + collection + reference_data into selector, product_id out into retriever
    { id: `e-${guardrail.id}-${pselector.id}`,  source: guardrail.id, target: pselector.id, sourceHandle: "query_out",      targetHandle: "query",          animated: true, style: selectorEdgeStyle },
    { id: `e-${vstore.id}-${pselector.id}`,     source: vstore.id,    target: pselector.id, sourceHandle: "collection",     targetHandle: "collection",     animated: true, style: selectorEdgeStyle },
    { id: `e-${refloader.id}-${pselector.id}`,  source: refloader.id, target: pselector.id, sourceHandle: "reference_data", targetHandle: "reference_data", animated: true, style: selectorEdgeStyle },
    { id: `e-${pselector.id}-${retriever.id}`,  source: pselector.id, target: retriever.id, sourceHandle: "product_id",     targetHandle: "product_id",     animated: true, style: selectorEdgeStyle },
  ];

  return { nodes, edges };
}

const DEFAULT_PIPELINE = buildDefaultPipeline();

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

export function FlowEditor() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [nodes, setNodes, onNodesChange] = useNodesState<any>(DEFAULT_PIPELINE.nodes);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>(DEFAULT_PIPELINE.edges);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance<any> | null>(null);
  const draggedTypeRef = useRef<NodeTypeDef | null>(null);

  const { isRunning, nodeStatuses, execute, cancel } = useExecution();

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
      const typeDef = NODE_DEF_MAP[typeId];
      if (!typeDef || !rfInstance || !reactFlowWrapper.current) return;

      const bounds = reactFlowWrapper.current.getBoundingClientRect();
      const position = rfInstance.screenToFlowPosition({
        x: event.clientX - bounds.left,
        y: event.clientY - bounds.top,
      });

      const newNode = createNodeFromDef(typeDef, position);
      setNodes((nds) => [...nds, newNode]);
    },
    [rfInstance, setNodes]
  );

  // ── Connections ─────────────────────────────────────────────

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!isConnectionValid(connection, nodes)) {
        console.warn("[FlowEditor] Invalid connection: port types don't match");
        return;
      }
      setEdges((eds) =>
        addEdge(
          {
            ...connection,
            animated: true,
            style: { strokeWidth: 2, stroke: "#e07830" },
          },
          eds
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
      setEdges((els) => reconnectEdge(oldEdge, newConnection, els));
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
    (nodeId: string, paramName: string, value: string | number) => {
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

  const [savedProfiles, setSavedProfiles] = useState<Record<string, { preset: string; custom_text: string }>>({});

  useEffect(() => {
    fetch("/api/profiles")
      .then((r) => r.json())
      .then((data) => setSavedProfiles(data.profiles ?? {}))
      .catch(() => {});
  }, []);

  const handleSaveProfile = useCallback(async (name: string) => {
    const sysNode = nodes.find((n) => n.data.typeId === "system_prompt");
    const preset = String(sysNode?.data.params.preset ?? "professional");
    const custom_text = String(sysNode?.data.params.text ?? "");
    await fetch("/api/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, preset, custom_text }),
    });
    setSavedProfiles((prev) => ({ ...prev, [name]: { preset, custom_text } }));
  }, [nodes]);

  const handleLoadProfile = useCallback((name: string) => {
    const profile = savedProfiles[name];
    if (!profile) return;
    setNodes((nds) =>
      nds.map((n) =>
        n.data.typeId === "system_prompt"
          ? {
              ...n,
              data: {
                ...n.data,
                params: {
                  ...n.data.params,
                  preset: profile.preset,
                  text: profile.custom_text,
                },
              },
            }
          : n
      )
    );
  }, [savedProfiles, setNodes]);

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
      <div className="flex-1 flex flex-col">
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
        />

        <div className="flex-1 relative" ref={reactFlowWrapper}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
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
            <RobotAvatar state={avatarState} message={avatarMessage} size={100} />
          </div>

          {/* Right: Config Panel */}
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
