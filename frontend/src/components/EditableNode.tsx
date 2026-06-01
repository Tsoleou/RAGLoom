import { Handle, Position, useReactFlow } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import type { CSSProperties } from "react";
import type { EditableNodeData, FlowNode, PortDef } from "../types/pipeline";
import { parseChatbotOutput, getEmotionTheme } from "../utils/chatbotOutput";
import { parseCriticOutput, getCriticTheme } from "../utils/criticOutput";
import { parseJudgeTrace } from "../utils/judgeTraceOutput";
import { NodeIcon } from "../utils/nodeIcons";

const STATUS_STYLES: Record<
  string,
  { ring: string; dot: string; label: string; chip: string }
> = {
  idle: { ring: "", dot: "bg-[#555]", label: "", chip: "" },
  waiting: { ring: "", dot: "bg-[#666] animate-pulse", label: "queued", chip: "text-[#888] bg-[#888]/10" },
  running: { ring: "ring-2 ring-[#e07830]", dot: "bg-[#e07830] animate-pulse", label: "running", chip: "text-[#e07830] bg-[#e07830]/12" },
  done: { ring: "ring-2 ring-emerald-600", dot: "bg-emerald-500", label: "done", chip: "text-emerald-400 bg-emerald-500/12" },
  error: { ring: "ring-2 ring-red-600", dot: "bg-red-500", label: "error", chip: "text-red-400 bg-red-500/12" },
  blocked: { ring: "ring-2 ring-amber-500", dot: "bg-amber-400", label: "blocked", chip: "text-amber-300 bg-amber-400/12" },
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

/** Inline-relative handle so the connection dot sits on the card edge of the
 *  same row as its label — no more percentage-positioned dots drifting away
 *  from the IN/OUT text they belong to. */
function handleStyle(dataType: string, side: "left" | "right"): CSSProperties {
  return {
    position: "relative",
    transform: "none",
    top: "auto",
    left: "auto",
    right: "auto",
    width: 12,
    height: 12,
    minWidth: 12,
    flexShrink: 0,
    background: HANDLE_COLORS[dataType] || "#555",
    border: "2px solid #2a2a2a",
    [side === "left" ? "marginLeft" : "marginRight"]: -18,
  };
}

export function EditableNode({
  id,
  data,
  selected,
}: NodeProps & { data: EditableNodeData }) {
  const { setNodes } = useReactFlow<FlowNode>();
  const style = STATUS_STYLES[data.status] || STATUS_STYLES.idle;
  const isResultDisplay = data.typeId === "result_display";
  const isOutputCritic = data.typeId === "output_critic";
  const isQueryInput = data.typeId === "query_input";
  const isJudgeTrace = data.typeId === "judge_trace_inspector";
  const chatbotOutput = isResultDisplay ? parseChatbotOutput(data.preview) : null;
  const criticOutput = isOutputCritic ? parseCriticOutput(data.preview) : null;
  const judgeTrace = isJudgeTrace ? parseJudgeTrace(data.preview) : null;
  const nodeCategory =
    data.typeId === "embedder" ? "shared" :
    ["loader", "chunker", "vectorstore"].includes(data.typeId) ? "ingest" : "query";
  const categoryColor = CATEGORY_COLORS[nodeCategory] || "border-l-[#444]";
  const nodeWidth =
    isResultDisplay || isQueryInput || isJudgeTrace ? "w-[360px]" : "w-[220px]";

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

  const renderPortLabel = (port: PortDef) => (
    <span
      className="text-[10px] leading-none truncate"
      style={{ color: HANDLE_COLORS[port.dataType] || "#9a9a9a" }}
      title={`${port.label} (${port.dataType})`}
    >
      {port.label}
    </span>
  );

  return (
    <div
      className={`
        bg-[#252525] rounded-lg border border-[#333] border-l-4 ${categoryColor}
        shadow-md shadow-black/20 ${nodeWidth} text-left transition-all
        ${style.ring}
        ${selected
          ? "outline outline-2 outline-offset-2 outline-[#e07830] shadow-lg shadow-black/30"
          : "hover:shadow-lg hover:shadow-black/25"}
      `}
    >
      {/* Header */}
      <div className="px-3 py-2 border-b border-[#2a2a2a] flex items-center gap-2">
        <NodeIcon typeId={data.typeId} className="w-3.5 h-3.5 text-[#e07830] flex-shrink-0" />
        <div className="font-semibold text-[#d0d0d0] text-xs truncate min-w-0 flex-1">
          {data.label}
        </div>
        {style.label && (
          <span
            className={`text-[9px] font-mono px-1.5 py-0.5 rounded uppercase tracking-wide ${style.chip}`}
          >
            {style.label}
          </span>
        )}
        <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${style.dot}`} />
      </div>

      {/* Ports — each handle sits on its own row, aligned with its label */}
      {(data.inputs.length > 0 || data.outputs.length > 0) && (
        <div className="px-3 py-2 space-y-1">
          {data.inputs.map((port) => (
            <div key={`in-${port.name}`} className="flex items-center gap-1.5">
              <Handle
                type="target"
                position={Position.Left}
                id={port.name}
                style={handleStyle(port.dataType, "left")}
                title={`${port.label} (${port.dataType})`}
              />
              {renderPortLabel(port)}
            </div>
          ))}
          {data.outputs.map((port) => (
            <div
              key={`out-${port.name}`}
              className="flex items-center justify-end gap-1.5"
            >
              {renderPortLabel(port)}
              <Handle
                type="source"
                position={Position.Right}
                id={port.name}
                style={handleStyle(port.dataType, "right")}
                title={`${port.label} (${port.dataType})`}
              />
            </div>
          ))}
        </div>
      )}

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
          ) : judgeTrace ? (
            <>
              <div className="flex items-center gap-1.5 mb-1.5">
                <span className="text-[9px] text-[#555] font-mono uppercase tracking-wider">
                  judge verdicts
                </span>
                <span className="text-[10px] font-mono font-bold text-[#c8c8c8] ml-auto">
                  kept {judgeTrace.kept}/{judgeTrace.total}
                </span>
              </div>
              <div className="space-y-1 max-h-60 overflow-y-auto">
                {judgeTrace.verdicts.map((v) => (
                  <div
                    key={v.i}
                    className="flex items-start gap-1.5 text-[10px] leading-snug"
                  >
                    <span
                      className="w-1.5 h-1.5 rounded-full flex-shrink-0 mt-1"
                      style={{ backgroundColor: v.keep ? "#50d090" : "#e06060" }}
                      title={v.keep ? "kept" : "dropped"}
                    />
                    <span className="text-[#666] font-mono flex-shrink-0">
                      [{v.i}]
                    </span>
                    <span className="text-[#555] font-mono flex-shrink-0">
                      {v.score.toFixed(2)}
                    </span>
                    <span className={v.keep ? "text-[#c8c8c8]" : "text-[#9a9a9a]"}>
                      {v.reason || (v.keep ? "(kept)" : "(dropped)")}
                    </span>
                  </div>
                ))}
              </div>
            </>
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
  );
}
