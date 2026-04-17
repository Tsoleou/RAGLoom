import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Send, X, Database, ChevronDown, ChevronUp } from "lucide-react";
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

const SUGGESTED_QUESTIONS = [
  "StarForge X1 的散熱系統是什麼？",
  "推薦一台最適合創作者的筆電",
  "NovaPad Pro 跟 NovaPad Ultra 有什麼差別？",
  "VisionBook 的螢幕規格是什麼？",
];

const chipVariants = {
  hidden: { opacity: 0, x: -12 },
  visible: { opacity: 1, x: 0 },
};

const containerVariants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.07 } },
};

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
  const [showRetrieval, setShowRetrieval] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, loading]);

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

  async function handleSendText(text: string) {
    if (!text || loading || !loaded) return;
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
          const theme = getEmotionTheme(emotion);
          setAvatarState("talk");
          setAvatarMessage("");
          setTimeout(() => {
            setAvatarState(emotionToAvatarState(emotion));
            setAvatarMessage(`Feeling: ${theme.label.toLowerCase()}`);
          }, 1500);
        } else {
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

  async function handleSend() {
    await handleSendText(input.trim());
  }

  async function handleClear() {
    await fetch("/api/chat/reset", { method: "POST" });
    setMessages([]);
    setRetrieval([]);
    setShowRetrieval(false);
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
      <aside className="w-64 border-r border-[#00ccaa]/10 bg-[#141414] p-4 flex flex-col gap-4">
        <div className="flex justify-center">
          <RobotAvatar state={avatarState} message={avatarMessage} size={128} />
        </div>

        <button
          onClick={handleIngest}
          disabled={loading}
          style={loading ? { animation: "glow-pulse 1.5s ease-in-out infinite" } : undefined}
          className="flex items-center justify-center gap-2 px-3 py-2 bg-[#e07830] hover:bg-[#f08840] disabled:opacity-50 text-white text-sm rounded transition-colors"
        >
          <Database size={14} />
          {loaded ? "Reload KB" : "Load KB"}
        </button>

        {/* Pill mode toggle */}
        <div>
          <div className="text-[10px] uppercase tracking-widest text-[#555] mb-2">Output Mode</div>
          <div className="flex rounded-md border border-[#2a2a2a] overflow-hidden text-xs">
            {(["professional", "chatbot"] as Mode[]).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`flex-1 py-1.5 capitalize transition-colors ${
                  mode === m
                    ? "bg-[#1a3040] text-[#00ccaa] border-b border-b-[#00ccaa]"
                    : "bg-[#1a1a1a] text-[#555] hover:text-[#888]"
                }`}
              >
                {m}
              </button>
            ))}
          </div>
        </div>

        {threshold !== null && (
          <div className="text-[10px] text-[#444] mt-auto font-mono">
            threshold: {threshold} · top_k: {topK}
          </div>
        )}
      </aside>

      {/* Right: Chat + retrieval */}
      <section className="flex-1 flex flex-col">
        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto p-6 space-y-4">
          {messages.length === 0 && (
            <div className="flex flex-col items-center gap-4 mt-16 px-6">
              <div className="text-[#444] text-sm tracking-wide">
                {loaded ? "Try asking..." : "Load the knowledge base to start."}
              </div>
              {loaded && (
                <motion.div
                  className="flex flex-col gap-2 w-full max-w-md"
                  variants={containerVariants}
                  initial="hidden"
                  animate="visible"
                >
                  {SUGGESTED_QUESTIONS.map((q) => (
                    <motion.button
                      key={q}
                      variants={chipVariants}
                      whileHover={{ x: 5 }}
                      transition={{ type: "spring", stiffness: 300, damping: 20 }}
                      onClick={() => handleSendText(q)}
                      className="text-left px-4 py-2.5 rounded-lg border border-[#2a2a2a] border-l-2 border-l-[#00ccaa]/30 bg-[#141414] hover:bg-[#1a2a2f] hover:border-l-[#00ccaa] text-sm text-[#888] hover:text-[#c0e0d8] transition-colors"
                    >
                      {q}
                    </motion.button>
                  ))}
                </motion.div>
              )}
            </div>
          )}

          <AnimatePresence initial={false}>
            {messages.map((m, i) => {
              const emTheme = m.emotion ? getEmotionTheme(m.emotion) : null;
              return (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, x: m.role === "user" ? 20 : -20, y: 6 }}
                  animate={{ opacity: 1, x: 0, y: 0 }}
                  transition={{ duration: 0.22, ease: "easeOut" }}
                  className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`max-w-[75%] px-4 py-2.5 rounded-lg text-sm whitespace-pre-wrap ${
                      m.role === "user"
                        ? "bg-[#e07830] text-white shadow-[0_0_14px_rgba(224,120,48,0.25)]"
                        : m.blocked
                        ? "bg-[#2a1a10] text-[#f0c070] border border-[#f0a040]/30 border-l-2 border-l-[#f0a040]"
                        : "bg-[#0a1a1f] text-[#d0e8e0] border border-[#1a3540] border-l-2 border-l-[#00ccaa] shadow-[0_0_18px_rgba(0,204,170,0.06)]"
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
                </motion.div>
              );
            })}
          </AnimatePresence>

          {/* Typing indicator */}
          {loading && messages.some((m) => m.role === "user") && (
            <motion.div
              initial={{ opacity: 0, x: -12 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex justify-start"
            >
              <div className="bg-[#0a1a1f] border border-[#1a3540] border-l-2 border-l-[#00ccaa] px-4 py-3 rounded-lg flex items-center gap-1.5 shadow-[0_0_18px_rgba(0,204,170,0.06)]">
                {[0, 150, 300].map((delay) => (
                  <span
                    key={delay}
                    className="w-1.5 h-1.5 rounded-full bg-[#00ccaa] animate-bounce"
                    style={{
                      animationDelay: `${delay}ms`,
                      boxShadow: "0 0 4px rgba(0,204,170,0.6)",
                    }}
                  />
                ))}
              </div>
            </motion.div>
          )}
        </div>

        {/* Input */}
        <div className="border-t border-[#00ccaa]/10 p-4 flex gap-2 bg-[#141414]">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="e.g. What cooling tech does StarForge X1 use?"
            rows={2}
            className="flex-1 bg-[#1a1a1a] border border-[#2a2a2a] rounded px-3 py-2 text-sm resize-none focus:outline-none focus:border-[#00ccaa] transition-all"
            style={{ boxShadow: "none" }}
            onFocus={(e) => (e.currentTarget.style.boxShadow = "0 0 8px rgba(0,204,170,0.2)")}
            onBlur={(e) => (e.currentTarget.style.boxShadow = "none")}
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="px-4 py-2 bg-[#e07830] hover:bg-[#f08840] hover:shadow-[0_0_10px_rgba(224,120,48,0.4)] disabled:opacity-40 text-white text-sm rounded self-stretch transition-all flex items-center justify-center"
          >
            <Send size={15} />
          </button>
          <button
            onClick={handleClear}
            disabled={loading}
            title="Clear chat history and reset memory"
            className="px-3 py-2 border border-[#2a2a2a] hover:bg-[#1a1a1a] hover:border-[#00ccaa]/30 hover:text-[#888] disabled:opacity-40 text-[#555] text-sm rounded self-stretch transition-colors flex items-center gap-1.5 whitespace-nowrap"
          >
            <X size={13} />
            Clear
          </button>
        </div>

        {/* Retrieval panel */}
        {retrieval.length > 0 && (
          <div className="border-t border-[#00ccaa]/10 bg-[#141414]">
            <button
              onClick={() => setShowRetrieval((v) => !v)}
              className="w-full flex items-center justify-between px-4 py-2 text-[#444] hover:text-[#00ccaa]/60 hover:bg-[#0d1a1f] transition-colors text-xs font-mono"
            >
              <span>Retrieval · {retrieval.length} chunks</span>
              {showRetrieval ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </button>
            {showRetrieval && (
              <div className="px-4 pb-4 max-h-52 overflow-y-auto">
                <table className="w-full text-[11px] font-mono">
                  <thead className="text-[#444]">
                    <tr className="border-b border-[#1a3540]">
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
                      <tr key={i} className="border-b border-[#0d1a1f]">
                        <td className="py-1 pr-2 text-[#444]">{i + 1}</td>
                        <td className="py-1 pr-2 text-[#00ccaa]/60">{r.source}</td>
                        <td className="py-1 pr-2 text-right text-[#888]">{r.score}</td>
                        <td className="py-1 pr-2 text-right text-[#444]">{r.distance}</td>
                        <td className="py-1 pr-2 text-center">
                          {r.passed ? (
                            <span className="text-[#00ccaa]">Y</span>
                          ) : (
                            <span className="text-[#444]">N</span>
                          )}
                        </td>
                        <td className="py-1 text-[#555] truncate max-w-[300px]">
                          {r.preview.slice(0, 80)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
