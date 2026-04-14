import { Handle, Position, useReactFlow } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import type { EditableNodeData } from "../types/pipeline";
import { parseChatbotOutput, getEmotionTheme } from "../utils/chatbotOutput";
import { parseCriticOutput, getCriticTheme } from "../utils/criticOutput";

const STATUS_STYLES: Record<string, { ring: string; dot: string }> = {
  idle: { ring: "", dot: "bg-[#555]" },
  waiting: { ring: "", dot: "bg-[#666] animate-pulse" },
  running: { ring: "ring-2 ring-[#e07830]", dot: "bg-[#e07830] animate-pulse" },
  done: { ring: "ring-2 ring-emerald-600", dot: "bg-emerald-500" },
  error: { ring: "ring-2 ring-red-600", dot: "bg-red-500" },
  blocked: { ring: "ring-2 ring-amber-500", dot: "bg-amber-400" },
};

const CATEGORY_COLORS: Record<string, string> = {
  ingest: "border-l-[#e07830]",
  query: "border-l-[#c06020]",
  shared: "border-l-[#d09050]",
};

/** data-type 對應的 handle 顏色（柔和暖色調） */
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

export function EditableNode({
  id,
  data,
  selected,
}: NodeProps & { data: EditableNodeData }) {
  const { setNodes } = useReactFlow();
  const style = STATUS_STYLES[data.status] || STATUS_STYLES.idle;
  const isResultDisplay = data.typeId === "result_display";
  const isOutputCritic = data.typeId === "output_critic";
  const isQueryInput = data.typeId === "query_input";
  const chatbotOutput = isResultDisplay ? parseChatbotOutput(data.preview) : null;
  const criticOutput = isOutputCritic ? parseCriticOutput(data.preview) : null;
  const nodeCategory =
    data.typeId === "embedder" ? "shared" :
    ["loader", "chunker", "vectorstore"].includes(data.typeId) ? "ingest" : "query";
  const categoryColor = CATEGORY_COLORS[nodeCategory] || "border-l-[#444]";
  const nodeWidth = isResultDisplay || isQueryInput ? "w-[360px]" : "w-[220px]";

  const handleQuestionChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, params: { ...n.data.params, question: value } } }
          : n
      )
    );
  };

  return (
    <>
      {/* Input handles */}
      {data.inputs.map((port, i) => (
        <Handle
          key={`in-${port.name}`}
          type="target"
          position={Position.Left}
          id={port.name}
          style={{
            top: `${30 + i * 24}%`,
            background: HANDLE_COLORS[port.dataType] || "#555",
            width: 10,
            height: 10,
            border: "2px solid #2a2a2a",
          }}
          title={`${port.label} (${port.dataType})`}
        />
      ))}

      <div
        className={`
          bg-[#252525] rounded-lg border border-[#333] border-l-4 ${categoryColor}
          shadow-md shadow-black/20 ${nodeWidth} text-left transition-all
          ${style.ring}
          ${selected ? "shadow-lg shadow-black/30 ring-2 ring-[#e07830]/50" : "hover:shadow-lg hover:shadow-black/25"}
        `}
      >
        {/* Header */}
        <div className="px-3 py-2 border-b border-[#2a2a2a] flex items-center justify-between">
          <div className="min-w-0">
            <div className="font-semibold text-[#d0d0d0] text-xs truncate">
              {data.label}
            </div>
          </div>
          <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${style.dot}`} />
        </div>

        {/* Ports summary */}
        <div className="px-3 py-1.5 text-[10px] text-[#666] space-y-0.5">
          {data.inputs.length > 0 && (
            <div className="flex gap-1 flex-wrap">
              <span className="text-[#555]">IN:</span>
              {data.inputs.map((p) => (
                <span
                  key={p.name}
                  className="px-1 rounded"
                  style={{
                    backgroundColor: (HANDLE_COLORS[p.dataType] || "#555") + "18",
                    color: HANDLE_COLORS[p.dataType] || "#888",
                  }}
                >
                  {p.label}
                </span>
              ))}
            </div>
          )}
          {data.outputs.length > 0 && (
            <div className="flex gap-1 flex-wrap">
              <span className="text-[#555]">OUT:</span>
              {data.outputs.map((p) => (
                <span
                  key={p.name}
                  className="px-1 rounded"
                  style={{
                    backgroundColor: (HANDLE_COLORS[p.dataType] || "#555") + "18",
                    color: HANDLE_COLORS[p.dataType] || "#888",
                  }}
                >
                  {p.label}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Inline question editor (QueryInput only) */}
        {isQueryInput && (
          <div className="px-3 py-2 border-t border-[#2a2a2a]">
            <div className="text-[9px] text-[#555] font-mono uppercase tracking-wider mb-1">
              question
            </div>
            <textarea
              className="nodrag nowheel w-full text-xs bg-[#1a1a1a] border border-[#333] rounded px-2 py-1.5 text-[#e0e0e0] resize-none focus:outline-none focus:border-[#e07830] font-mono"
              rows={3}
              value={String(data.params.question || "")}
              onChange={handleQuestionChange}
              onClick={(e) => e.stopPropagation()}
              onMouseDown={(e) => e.stopPropagation()}
              placeholder="Type a question..."
            />
          </div>
        )}

        {/* Preview (shown after execution) */}
        {data.preview && (
          <div className="px-3 py-1.5 border-t border-[#2a2a2a]">
            {criticOutput ? (
              (() => {
                const theme = getCriticTheme(criticOutput);
                return (
                  <>
                    <div className="flex items-center gap-1.5 mb-1.5">
                      <span className="text-[9px] text-[#555] font-mono uppercase tracking-wider">
                        verdict
                      </span>
                      <span
                        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-bold tracking-wide"
                        style={{
                          backgroundColor: theme.bg,
                          border: `1px solid ${theme.border}`,
                          color: theme.text,
                        }}
                      >
                        <span
                          className="w-1.5 h-1.5 rounded-full"
                          style={{ backgroundColor: theme.dot }}
                        />
                        {theme.label}
                      </span>
                      <span className="text-[9px] text-[#555] font-mono ml-auto">
                        mode: {criticOutput.mode}
                      </span>
                    </div>
                    {criticOutput.reason && (
                      <div className="text-[10px] text-[#999] leading-relaxed">
                        {criticOutput.reason}
                      </div>
                    )}
                  </>
                );
              })()
            ) : chatbotOutput ? (
              (() => {
                const theme = getEmotionTheme(chatbotOutput.emotion);
                return (
                  <>
                    <div className="flex items-center gap-1.5 mb-1.5">
                      <span className="text-[9px] text-[#555] font-mono uppercase tracking-wider">
                        emotion
                      </span>
                      <span
                        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-bold tracking-wide"
                        style={{
                          backgroundColor: theme.bg,
                          border: `1px solid ${theme.border}`,
                          color: theme.text,
                        }}
                      >
                        <span
                          className="w-1.5 h-1.5 rounded-full"
                          style={{ backgroundColor: theme.dot }}
                        />
                        {theme.label}
                      </span>
                    </div>
                    <div className="text-[9px] text-[#555] font-mono uppercase tracking-wider mb-0.5">
                      reply
                    </div>
                    <div className="text-[#c8c8c8] whitespace-pre-wrap text-xs leading-relaxed max-h-52 overflow-y-auto">
                      {chatbotOutput.reply}
                    </div>
                  </>
                );
              })()
            ) : (
              <div className={`text-[#999] font-mono whitespace-pre-wrap ${
                isResultDisplay
                  ? "text-xs leading-relaxed max-h-60 overflow-y-auto"
                  : "text-[10px] line-clamp-3"
              }`}>
                {data.preview}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Output handles */}
      {data.outputs.map((port, i) => (
        <Handle
          key={`out-${port.name}`}
          type="source"
          position={Position.Right}
          id={port.name}
          style={{
            top: `${30 + i * 24}%`,
            background: HANDLE_COLORS[port.dataType] || "#555",
            width: 10,
            height: 10,
            border: "2px solid #2a2a2a",
          }}
          title={`${port.label} (${port.dataType})`}
        />
      ))}
    </>
  );
}
