import { useState, useRef, useEffect } from "react";
import { RobotAvatar } from "./RobotAvatar";
import {
  parseChatbotOutput,
  emotionToAvatarState,
  getEmotionTheme,
} from "../utils/chatbotOutput";

type AvatarState = "idle" | "think" | "talk" | "happy" | "error";
type Mode = "professional" | "chatbot";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  blocked?: boolean;
  emotion?: string;
}

interface RetrievalRow {
  source: string;
  score: number;
  distance: number;
  passed: boolean;
  preview: string;
}

interface QueryResponse {
  status: "ok" | "error";
  message?: string;
  reply?: string;
  retrieval?: RetrievalRow[];
  threshold?: number;
  top_k?: number;
  blocked?: boolean;
  blocked_reason?: string;
}

export function ChatView() {
  const [avatarState, setAvatarState] = useState<AvatarState>("idle");
  const [avatarMessage, setAvatarMessage] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<Mode>("professional");
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [retrieval, setRetrieval] = useState<RetrievalRow[]>([]);
  const [threshold, setThreshold] = useState<number | null>(null);
  const [topK, setTopK] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  async function handleIngest() {
    setLoading(true);
    setAvatarState("think");
    setAvatarMessage("Loading KB...");
    try {
      const res = await fetch("/api/chat/ingest", { method: "POST" });
      const data = await res.json();
      if (data.status === "ok") {
        setLoaded(true);
        setAvatarState("happy");
        setAvatarMessage(`Loaded ${data.chunks} chunks`);
      } else {
        setAvatarState("error");
        setAvatarMessage(data.message || "Failed");
      }
    } catch (e) {
      setAvatarState("error");
      setAvatarMessage(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || loading) return;
    if (!loaded) {
      setAvatarState("error");
      setAvatarMessage("Load KB first");
      return;
    }
    setMessages((m) => [...m, { role: "user", content: text }]);
    setInput("");
    setLoading(true);
    setAvatarState("think");
    setAvatarMessage("Searching...");
    try {
      const res = await fetch("/api/chat/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, mode }),
      });
      const data: QueryResponse = await res.json();
      if (data.status === "ok" && data.reply !== undefined) {
        let replyContent = data.reply;
        let emotion: string | undefined;

        // Chatbot mode: extract {reply, emotion} from the LLM's JSON output
        if (!data.blocked && mode === "chatbot") {
          const parsed = parseChatbotOutput(data.reply);
          if (parsed) {
            replyContent = parsed.reply;
            emotion = parsed.emotion;
          }
        }

        setMessages((m) => [
          ...m,
          { role: "assistant", content: replyContent, blocked: data.blocked, emotion },
        ]);
        setRetrieval(data.retrieval || []);
        setThreshold(data.threshold ?? null);
        setTopK(data.top_k ?? null);

        if (data.blocked) {
          setAvatarState("error");
          setAvatarMessage("Blocked by guardrail");
        } else if (emotion) {
          // Chatbot mode: avatar reflects the LLM's self-reported emotion
          const theme = getEmotionTheme(emotion);
          setAvatarState("talk");
          setAvatarMessage("");
          setTimeout(() => {
            setAvatarState(emotionToAvatarState(emotion));
            setAvatarMessage(`Feeling: ${theme.label.toLowerCase()}`);
          }, 1500);
        } else {
          // Professional mode: brief talk animation then neutral idle.
          // No "happy" fallback — that would be a fake emotion.
          setAvatarState("talk");
          setAvatarMessage("");
          setTimeout(() => {
            setAvatarState("idle");
            setAvatarMessage("");
          }, 1500);
        }
      } else {
        setMessages((m) => [
          ...m,
          { role: "assistant", content: `Error: ${data.message || "unknown"}` },
        ]);
        setAvatarState("error");
        setAvatarMessage(data.message || "Error");
      }
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: "assistant", content: `Error: ${e}` },
      ]);
      setAvatarState("error");
      setAvatarMessage(String(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleClear() {
    await fetch("/api/chat/reset", { method: "POST" });
    setMessages([]);
    setRetrieval([]);
    setAvatarState("idle");
    setAvatarMessage("");
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <div className="h-full flex bg-[#1a1a1a] text-[#e0e0e0]">
      {/* Left: Avatar + controls */}
      <aside className="w-72 border-r border-[#2a2a2a] p-4 flex flex-col gap-4">
        <div className="flex justify-center">
          <RobotAvatar state={avatarState} message={avatarMessage} size={128} />
        </div>

        <button
          onClick={handleIngest}
          disabled={loading}
          className="px-3 py-2 bg-[#e07830] hover:bg-[#f08840] disabled:opacity-50 text-white text-sm rounded"
        >
          {loaded ? "Reload Knowledge Base" : "Load Knowledge Base"}
        </button>

        <div className="text-xs text-[#888]">
          <div className="mb-1.5 text-[#aaa]">Output Mode</div>
          <div className="flex gap-2">
            {(["professional", "chatbot"] as Mode[]).map((m) => (
              <label key={m} className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="radio"
                  checked={mode === m}
                  onChange={() => setMode(m)}
                  className="accent-[#e07830]"
                />
                {m}
              </label>
            ))}
          </div>
        </div>


        {threshold !== null && (
          <div className="text-[10px] text-[#666] mt-auto font-mono">
            threshold: {threshold} | top_k: {topK}
          </div>
        )}
      </aside>

      {/* Right: Chat + retrieval */}
      <section className="flex-1 flex flex-col">
        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto p-6 space-y-4">
          {messages.length === 0 && (
            <div className="text-[#555] text-sm text-center mt-10">
              {loaded
                ? "Ask a question about the knowledge base..."
                : "Load the knowledge base to start chatting."}
            </div>
          )}
          {messages.map((m, i) => {
            const emTheme = m.emotion ? getEmotionTheme(m.emotion) : null;
            return (
              <div
                key={i}
                className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[75%] px-4 py-2.5 rounded-lg text-sm whitespace-pre-wrap ${
                    m.role === "user"
                      ? "bg-[#e07830] text-white"
                      : m.blocked
                      ? "bg-[#2a2218] text-[#f0c070] border border-[#f0a040]/50"
                      : "bg-[#252525] text-[#e0e0e0] border border-[#333]"
                  }`}
                >
                  {m.blocked && (
                    <div className="text-[10px] uppercase tracking-wider text-[#f0a040] mb-1 font-mono">
                      ⊘ Blocked by Guardrail
                    </div>
                  )}
                  {emTheme && (
                    <div className="mb-1.5">
                      <span
                        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-bold tracking-wide"
                        style={{
                          backgroundColor: emTheme.bg,
                          border: `1px solid ${emTheme.border}`,
                          color: emTheme.text,
                        }}
                      >
                        <span
                          className="w-1.5 h-1.5 rounded-full"
                          style={{ backgroundColor: emTheme.dot }}
                        />
                        {emTheme.label}
                      </span>
                    </div>
                  )}
                  {m.content}
                </div>
              </div>
            );
          })}
        </div>

        {/* Input */}
        <div className="border-t border-[#2a2a2a] p-4 flex gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="e.g. What cooling tech does StarForge X1 use?"
            rows={2}
            className="flex-1 bg-[#252525] border border-[#333] rounded px-3 py-2 text-sm resize-none focus:outline-none focus:border-[#e07830]"
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="px-4 py-2 bg-[#e07830] hover:bg-[#f08840] disabled:opacity-50 text-white text-sm rounded self-stretch"
          >
            Send
          </button>
          <button
            onClick={handleClear}
            disabled={loading}
            title="Clear chat history and reset memory"
            className="px-3 py-2 border border-[#333] hover:bg-[#2a2a2a] hover:text-[#aaa] disabled:opacity-40 text-[#666] text-sm rounded self-stretch transition-colors whitespace-nowrap"
          >
            Clear History
          </button>
        </div>

        {/* Retrieval table */}
        {retrieval.length > 0 && (
          <div className="border-t border-[#2a2a2a] p-4 max-h-56 overflow-y-auto">
            <div className="text-xs text-[#aaa] mb-2">Retrieval Details</div>
            <table className="w-full text-[11px] font-mono">
              <thead className="text-[#666]">
                <tr className="border-b border-[#2a2a2a]">
                  <th className="text-left py-1 pr-2">#</th>
                  <th className="text-left py-1 pr-2">Source</th>
                  <th className="text-right py-1 pr-2">Score</th>
                  <th className="text-right py-1 pr-2">Dist</th>
                  <th className="text-center py-1 pr-2">Pass</th>
                  <th className="text-left py-1">Chunk</th>
                </tr>
              </thead>
              <tbody>
                {retrieval.map((r, i) => (
                  <tr key={i} className="border-b border-[#222]">
                    <td className="py-1 pr-2 text-[#666]">{i + 1}</td>
                    <td className="py-1 pr-2 text-[#aaa]">{r.source}</td>
                    <td className="py-1 pr-2 text-right">{r.score}</td>
                    <td className="py-1 pr-2 text-right text-[#666]">{r.distance}</td>
                    <td className="py-1 pr-2 text-center">
                      {r.passed ? (
                        <span className="text-[#5fbf5f]">Y</span>
                      ) : (
                        <span className="text-[#666]">N</span>
                      )}
                    </td>
                    <td className="py-1 text-[#888] truncate max-w-[300px]">
                      {r.preview.slice(0, 80)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
