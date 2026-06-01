import { X } from "lucide-react";
import type { EditableNodeData, ParamDef, PortDef } from "../types/pipeline";
import { useNodeTypes } from "../hooks/useNodeTypes";
import { NodeIcon } from "../utils/nodeIcons";

const HANDLE_COLORS: Record<string, string> = {
  documents: "#e07830",
  chunks: "#c06828",
  embeddings: "#d09050",
  collection: "#50a070",
  query: "#d08060",
  results: "#a08060",
  prompt: "#80a070",
  system_prompt: "#70b0d0",
  answer: "#e06040",
};

interface Props {
  nodeId: string | null;
  data: EditableNodeData | null;
  onParamChange: (nodeId: string, paramName: string, value: string | number | boolean) => void;
  onClose: () => void;
}

function PortRow({ port }: { port: PortDef }) {
  const color = HANDLE_COLORS[port.dataType] || "#888";
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: color }} />
      <span className="text-[#bbb] truncate">{port.label}</span>
      <span className="text-[#555] font-mono ml-auto">{port.dataType}</span>
    </div>
  );
}

export function NodeConfigPanel({ nodeId, data, onParamChange, onClose }: Props) {
  const { byTypeId } = useNodeTypes();
  if (!nodeId || !data) return null;

  const def = byTypeId[data.typeId];
  const paramDefs: ParamDef[] = def?.params || [];

  return (
    <div className="w-80 flex-shrink-0 bg-[#252525] border-l border-[#2a2a2a] flex flex-col overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 bg-[#202020] border-b border-[#2a2a2a] flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <NodeIcon typeId={data.typeId} className="w-4 h-4 text-[#e07830] flex-shrink-0" />
          <h3 className="font-bold text-[#d0d0d0] truncate">{data.label}</h3>
        </div>
        <button
          onClick={onClose}
          className="text-[#555] hover:text-[#e07830] transition-colors flex-shrink-0"
          aria-label="Close"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Description */}
        {def?.description && (
          <div className="px-5 py-3 border-b border-[#2a2a2a]">
            <p className="text-xs text-[#888] leading-relaxed">{def.description}</p>
          </div>
        )}

        {/* Ports */}
        {(data.inputs.length > 0 || data.outputs.length > 0) && (
          <div className="px-5 py-3 border-b border-[#2a2a2a] space-y-2">
            {data.inputs.length > 0 && (
              <div className="space-y-1">
                <div className="text-[10px] font-medium text-[#666] uppercase tracking-wider">Inputs</div>
                {data.inputs.map((p) => (
                  <PortRow key={`in-${p.name}`} port={p} />
                ))}
              </div>
            )}
            {data.outputs.length > 0 && (
              <div className="space-y-1">
                <div className="text-[10px] font-medium text-[#666] uppercase tracking-wider">Outputs</div>
                {data.outputs.map((p) => (
                  <PortRow key={`out-${p.name}`} port={p} />
                ))}
              </div>
            )}
          </div>
        )}

        {/* Params */}
        {paramDefs.length > 0 && (
          <div className="px-5 py-4 space-y-3">
            <div className="text-xs font-medium text-[#666]">Parameters</div>
            {paramDefs.map((p) => {
              const value = data.params[p.name] ?? p.default;

              if (p.type === "select" && p.options) {
                return (
                  <div key={p.name}>
                    <label className="text-xs text-[#999] block mb-1">{p.label}</label>
                    <select
                      value={String(value)}
                      onChange={(e) => onParamChange(nodeId, p.name, e.target.value)}
                      className="w-full text-xs border border-[#333] rounded px-2 py-1.5 bg-[#1a1a1a] text-[#c0c0c0] focus:outline-none focus:ring-1 focus:ring-[#e07830]"
                    >
                      {p.options.map((opt) => (
                        <option key={opt} value={opt}>
                          {opt || "(none)"}
                        </option>
                      ))}
                    </select>
                  </div>
                );
              }

              if (p.type === "number") {
                return (
                  <div key={p.name}>
                    <label className="text-xs text-[#999] block mb-1">{p.label}</label>
                    <input
                      type="number"
                      value={String(value)}
                      onChange={(e) => onParamChange(nodeId, p.name, parseFloat(e.target.value) || 0)}
                      className="w-full text-xs border border-[#333] rounded px-2 py-1.5 bg-[#1a1a1a] text-[#c0c0c0] focus:outline-none focus:ring-1 focus:ring-[#e07830] font-mono"
                    />
                  </div>
                );
              }

              if (p.type === "textarea") {
                return (
                  <div key={p.name}>
                    <label className="text-xs text-[#999] block mb-1">{p.label}</label>
                    <textarea
                      value={String(value)}
                      onChange={(e) => onParamChange(nodeId, p.name, e.target.value)}
                      rows={6}
                      className="w-full text-xs border border-[#333] rounded px-2 py-1.5 bg-[#1a1a1a] text-[#c0c0c0] focus:outline-none focus:ring-1 focus:ring-[#e07830] font-mono resize-y leading-relaxed"
                    />
                  </div>
                );
              }

              if (p.type === "boolean") {
                const checked = value === true || value === "true";
                return (
                  <div key={p.name}>
                    <label className="flex items-center gap-2 text-xs text-[#999] cursor-pointer">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => onParamChange(nodeId, p.name, e.target.checked)}
                        className="accent-[#e07830]"
                      />
                      <span>{p.label}</span>
                    </label>
                  </div>
                );
              }

              // string
              return (
                <div key={p.name}>
                  <label className="text-xs text-[#999] block mb-1">{p.label}</label>
                  <input
                    type="text"
                    value={String(value)}
                    onChange={(e) => onParamChange(nodeId, p.name, e.target.value)}
                    className="w-full text-xs border border-[#333] rounded px-2 py-1.5 bg-[#1a1a1a] text-[#c0c0c0] focus:outline-none focus:ring-1 focus:ring-[#e07830] font-mono"
                  />
                </div>
              );
            })}
          </div>
        )}

        {/* Preview */}
        {data.preview && (
          <div className="px-5 py-3 border-t border-[#2a2a2a]">
            <div className="text-xs font-medium text-[#666] mb-1">Output</div>
            <pre className="text-[10px] text-[#999] font-mono whitespace-pre-wrap bg-[#1a1a1a] rounded p-2 max-h-40 overflow-y-auto">
              {data.preview}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
