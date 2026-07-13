import { useCallback, useEffect, useRef, useState } from "react";
import type { FlowNode, FlowEdge, NodeStatus, WsMessage } from "../types/pipeline";

const API_BASE = "";
const WS_BASE = `ws://${window.location.host}`;

export interface ExecutionState {
  isRunning: boolean;
  nodeStatuses: Record<string, { status: NodeStatus; preview: string }>;
}

interface UseExecutionOptions {
  /** Called when execution fails (backend error or dropped/failed socket)
   *  so the UI can surface it instead of only logging to the console. */
  onError?: (message: string) => void;
}

export function useExecution(options: UseExecutionOptions = {}) {
  const [state, setState] = useState<ExecutionState>({
    isRunning: false,
    nodeStatuses: {},
  });
  const wsRef = useRef<WebSocket | null>(null);
  // Keep the latest callback without re-creating `execute` (which many
  // memoized handlers depend on) every render.
  const onErrorRef = useRef(options.onError);
  useEffect(() => {
    onErrorRef.current = options.onError;
  }, [options.onError]);
  // Distinguishes an intentional close (complete / cancel) from a dropped
  // connection so `onclose` only reports the latter.
  const expectedCloseRef = useRef(false);

  const execute = useCallback((nodes: FlowNode[], edges: FlowEdge[]) => {
    // 清除舊狀態
    expectedCloseRef.current = false;
    setState({ isRunning: true, nodeStatuses: {} });

    // 準備送給後端的 graph 資料
    const graphNodes = nodes.map((n) => ({
      id: n.id,
      type: n.data.typeId,
      params: n.data.params,
    }));

    const graphEdges = edges.map((e) => ({
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle || "",
      targetHandle: e.targetHandle || "",
    }));

    // 建立 WebSocket 連線
    const ws = new WebSocket(`${WS_BASE}/api/ws/execute`);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ nodes: graphNodes, edges: graphEdges }));
    };

    ws.onmessage = (event) => {
      const msg: WsMessage = JSON.parse(event.data);

      if ("type" in msg && msg.type === "complete") {
        expectedCloseRef.current = true;
        setState((prev) => ({ ...prev, isRunning: false }));
        ws.close();
        return;
      }

      if ("type" in msg && msg.type === "error") {
        console.error("[Execution] Error:", msg.message);
        expectedCloseRef.current = true;
        setState((prev) => ({ ...prev, isRunning: false }));
        onErrorRef.current?.(msg.message || "Pipeline execution failed");
        ws.close();
        return;
      }

      // Status update for a single node
      if ("nodeId" in msg) {
        setState((prev) => ({
          ...prev,
          nodeStatuses: {
            ...prev.nodeStatuses,
            [msg.nodeId]: { status: msg.status, preview: msg.preview },
          },
        }));
      }
    };

    ws.onerror = (err) => {
      console.error("[Execution] WebSocket error:", err);
      expectedCloseRef.current = true;
      setState((prev) => ({ ...prev, isRunning: false }));
      onErrorRef.current?.("Cannot reach the execution server — make sure the backend is running");
    };

    ws.onclose = () => {
      // A drop mid-run (server crash, network) never sends a complete/error
      // frame — report it so the run doesn't just silently stop.
      if (!expectedCloseRef.current) {
        onErrorRef.current?.("Execution connection dropped; the pipeline did not finish");
      }
      setState((prev) => ({ ...prev, isRunning: false }));
    };
  }, []);

  const cancel = useCallback(() => {
    expectedCloseRef.current = true;
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setState((prev) => ({ ...prev, isRunning: false }));
  }, []);

  return { ...state, execute, cancel };
}

/** Fallback: use REST API instead of WebSocket */
export async function executeRest(nodes: FlowNode[], edges: FlowEdge[]) {
  const graphNodes = nodes.map((n) => ({
    id: n.id,
    type: n.data.typeId,
    params: n.data.params,
  }));
  const graphEdges = edges.map((e) => ({
    source: e.source,
    target: e.target,
    sourceHandle: e.sourceHandle || "",
    targetHandle: e.targetHandle || "",
  }));

  const res = await fetch(`${API_BASE}/api/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nodes: graphNodes, edges: graphEdges }),
  });

  return res.json();
}
