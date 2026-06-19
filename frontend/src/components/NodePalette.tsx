import { useMemo, useState } from "react";
import { Search, RotateCw } from "lucide-react";
import { useNodeTypes } from "../hooks/useNodeTypes";
import { NodeIcon } from "../utils/nodeIcons";
import type { NodeTypeDef } from "../types/pipeline";

const CATEGORY_LABELS: Record<string, string> = {
  ingest: "Ingest",
  query: "Query",
  shared: "Shared",
  eval: "Eval",
};

const CATEGORY_ORDER = ["ingest", "shared", "query", "eval"];

interface Props {
  onDragStart: (typeDef: NodeTypeDef) => void;
}

export function NodePalette({ onDragStart }: Props) {
  const { nodeTypes, loading, error, reload } = useNodeTypes();
  const [query, setQuery] = useState("");

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matches = (d: NodeTypeDef) =>
      !q ||
      d.label.toLowerCase().includes(q) ||
      d.labelEn.toLowerCase().includes(q) ||
      d.description.toLowerCase().includes(q) ||
      d.typeId.toLowerCase().includes(q);
    return CATEGORY_ORDER.map((cat) => ({
      category: cat,
      label: CATEGORY_LABELS[cat] || cat,
      nodes: nodeTypes.filter((d) => d.category === cat && matches(d)),
    })).filter((g) => g.nodes.length > 0);
  }, [nodeTypes, query]);

  const noResults = !loading && !error && nodeTypes.length > 0 && grouped.length === 0;

  return (
    <div className="w-56 bg-[#202020] border-r border-[#2a2a2a] flex flex-col">
      <div className="px-4 py-3 border-b border-[#2a2a2a]">
        <h2 className="text-sm font-bold text-[#d0d0d0]">Node Palette</h2>
        <p className="text-[10px] text-[#555] mt-0.5">Drag nodes to canvas</p>
      </div>

      {/* Search */}
      <div className="px-3 pt-3">
        <div className="relative">
          <Search className="w-3.5 h-3.5 text-[#555] absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search nodes…"
            className="w-full text-xs bg-[#1a1a1a] border border-[#333] rounded pl-8 pr-2 py-1.5 text-[#c0c0c0] placeholder-[#555] focus:outline-none focus:border-[#e07830]"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {loading && (
          <div className="text-[10px] text-[#555] py-2">Loading node types…</div>
        )}
        {error && (
          <div className="py-2 space-y-1.5">
            <div className="text-[10px] text-[#f0a040]">無法載入節點類型:{error}</div>
            <button
              onClick={reload}
              className="flex items-center gap-1.5 rounded border border-[#f0a040]/40 px-2 py-1 text-[10px] text-[#f0a040] transition-colors hover:bg-[#f0a040]/10"
            >
              <RotateCw size={11} />
              重試
            </button>
          </div>
        )}
        {noResults && (
          <div className="text-[10px] text-[#555] py-2">No nodes match “{query}”.</div>
        )}
        {grouped.map((group) => (
          <div key={group.category}>
            <div className="text-[10px] font-medium text-[#666] uppercase tracking-wider mb-2">
              {group.label}
            </div>
            <div className="space-y-1.5">
              {group.nodes.map((def) => (
                <div
                  key={def.typeId}
                  draggable
                  title={def.description}
                  onDragStart={(e) => {
                    e.dataTransfer.setData("application/rag-node-type", def.typeId);
                    e.dataTransfer.effectAllowed = "move";
                    onDragStart(def);
                  }}
                  className="
                    px-3 py-2 rounded-md border border-[#333] bg-[#252525]
                    cursor-grab active:cursor-grabbing
                    hover:border-[#e07830]/50 hover:bg-[#2a2a2a] transition-colors
                  "
                >
                  <div className="flex items-center gap-2">
                    <NodeIcon typeId={def.typeId} className="w-3.5 h-3.5 text-[#e07830] flex-shrink-0" />
                    <div className="text-xs font-medium text-[#c0c0c0] truncate">
                      {def.label}
                    </div>
                  </div>
                  {def.description && (
                    <div className="text-[10px] text-[#666] mt-1 line-clamp-2 leading-snug">
                      {def.description}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
