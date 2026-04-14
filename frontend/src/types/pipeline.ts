import type { Node, Edge } from "@xyflow/react";

export type NodeStatus = "idle" | "waiting" | "running" | "done" | "error" | "blocked";

export interface PortDef {
  name: string;
  dataType: string;
  label: string;
}

export interface ParamDef {
  name: string;
  label: string;
  type: "string" | "number" | "select" | "textarea";
  default: string | number;
  options?: string[];
}

export interface NodeTypeDef {
  typeId: string;
  label: string;
  labelEn: string;
  description: string;
  category: string;
  inputs: PortDef[];
  outputs: PortDef[];
  params: ParamDef[];
}

export interface EditableNodeData {
  typeId: string;
  label: string;
  labelEn: string;
  inputs: PortDef[];
  outputs: PortDef[];
  params: Record<string, string | number>;
  status: NodeStatus;
  preview: string;
}

export type FlowNode = Node<EditableNodeData>;
export type FlowEdge = Edge;

export type WsMessage =
  | { type: "complete" }
  | { type: "error"; message: string }
  | { nodeId: string; status: NodeStatus; preview: string };
