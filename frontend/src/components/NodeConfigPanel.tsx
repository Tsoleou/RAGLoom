import type { EditableNodeData, ParamDef } from "../types/pipeline";
import { NODE_DEF_MAP } from "../data/nodeDefinitions";

interface Props {
  nodeId: string | null;
  data: EditableNodeData | null;
  onParamChange: (nodeId: string, paramName: string, value: string | number) => void;
  onClose: () => void;
}

export function NodeConfigPanel({ nodeId, data, onParamChange, onClose }: Props) {
  if (!nodeId || !data) return null;

  const def = NODE_DEF_MAP[data.typeId];
  const paramDefs: ParamDef[] = def?.params || [];

  return (
    <div className="absolute right-4 top-4 w-80 bg-[#252525] rounded-lg shadow-xl shadow-black/30 border border-[#333] z-50 overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 bg-[#202020] border-b border-[#2a2a2a] flex items-start justify-between">
        <div>
          <h3 className="font-bold text-[#d0d0d0]">{data.label}</h3>
        </div>
        <button
          onClick={onClose}
          className="text-[#555] hover:text-[#e07830] text-lg leading-none transition-colors"
        >
          &times;
        </button>
      </div>

      {/* Description */}
      {def?.description && (
        <div className="px-5 py-3 border-b border-[#2a2a2a]">
          <p className="text-xs text-[#888] leading-relaxed">{def.description}</p>
        </div>
      )}

      {/* Params */}
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
                  value={value}
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
  );
}
