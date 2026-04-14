import { NODE_DEFINITIONS } from "../data/nodeDefinitions";
import type { NodeTypeDef } from "../types/pipeline";

const CATEGORY_LABELS: Record<string, string> = {
  ingest: "Ingest",
  query: "Query",
  shared: "Shared",
};

const CATEGORY_ORDER = ["ingest", "shared", "query"];

interface Props {
  onDragStart: (typeDef: NodeTypeDef) => void;
}

export function NodePalette({ onDragStart }: Props) {
  const grouped = CATEGORY_ORDER.map((cat) => ({
    category: cat,
    label: CATEGORY_LABELS[cat] || cat,
    nodes: NODE_DEFINITIONS.filter((d) => d.category === cat),
  })).filter((g) => g.nodes.length > 0);

  return (
    <div className="w-56 bg-[#202020] border-r border-[#2a2a2a] flex flex-col">
      <div className="px-4 py-3 border-b border-[#2a2a2a]">
        <h2 className="text-sm font-bold text-[#d0d0d0]">Node Palette</h2>
        <p className="text-[10px] text-[#555] mt-0.5">Drag nodes to canvas</p>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
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
                  <div className="text-xs font-medium text-[#c0c0c0]">
                    {def.label}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

    </div>
  );
}
