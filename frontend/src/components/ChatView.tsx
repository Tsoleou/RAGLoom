import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Send, X, Database, ChevronDown, ChevronUp, Trash2, RotateCw } from "lucide-react";
import { Avatar } from "./avatar/Avatar";
import type { AvatarState } from "./avatar/types";
import { useConfirm } from "./ui/ConfirmDialog";
import {
  parseChatbotOutput,
  emotionToAvatarState,
  getEmotionTheme,
} from "../utils/chatbotOutput";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  blocked?: boolean;
  emotion?: string;
  /** Marks an assistant message that reports a failure, so the bubble can
   *  render a retry affordance that re-sends `retryText`. */
  error?: boolean;
  retryText?: string;
  /** Images of the products this reply names, resolved server-side. */
  productImages?: ProductImage[];
}

interface ProductImage {
  product_id: string;
  url: string;
}

interface RetrievalRow {
  source: string;
  score: number;
  distance: number;
  passed: boolean;
  preview: string;
}

type GuardStatus = "pass" | "block" | "skip";

interface GuardRow {
  name: string;
  status: GuardStatus;
  detail?: string | null;
}

interface CritiqueRow {
  verdict: string;
  reason: string;
  revised: boolean;
  grounded?: boolean;
}

interface RerankVerdict {
  i: number;
  keep: boolean;
  reason: string;
  source: string;
  score: number;
}

interface RerankTrace {
  kept: number;
  total: number;
  verdicts: RerankVerdict[];
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
  guards?: GuardRow[];
  rerank?: RerankTrace | null;
  critique?: CritiqueRow | null;
  product_images?: ProductImage[];
}

// Chat now executes the active profile's full graph; we just need the names.
type ProfileMap = Record<string, unknown>;

interface ProfilesResponse {
  active: string;
  profiles: ProfileMap;
}

const RAG_LOOM_ASCII = `\
██████╗  █████╗  ██████╗ ██╗      ██████╗  ██████╗ ███╗   ███╗
██╔══██╗██╔══██╗██╔════╝ ██║     ██╔═══██╗██╔═══██╗████╗ ████║
██████╔╝███████║██║  ███╗██║     ██║   ██║██║   ██║██╔████╔██║
██╔══██╗██╔══██║██║   ██║██║     ██║   ██║██║   ██║██║╚██╔╝██║
██║  ██║██║  ██║╚██████╔╝███████╗╚██████╔╝╚██████╔╝██║ ╚═╝ ██║
╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝ ╚═════╝  ╚═════╝ ╚═╝     ╚═╝`;

const SUGGESTED_QUESTIONS = [
  "What cooling system does the StarForge X1 use?",
  "Which laptop do you recommend for creators?",
  "What's the difference between NovaPad Pro and NovaPad Ultra?",
  "What are the display specs of the VisionBook?",
];

const chipVariants = {
  hidden: { opacity: 0, x: -12 },
  visible: { opacity: 1, x: 0 },
};

const containerVariants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.07 } },
};

/** `admin` (default true) keeps the full operator surface — Load-KB button,
 *  profile switcher/delete, ingest receipt. The chat-only kiosk entry passes
 *  `admin={false}`: those controls are hidden and the KB is treated as already
 *  loaded (the served backend auto-initialises chat_pipe at startup), so the
 *  send button isn't gated behind a Load-KB click a visitor can't see. */
export function ChatView({ admin = true }: { admin?: boolean } = {}) {
  const confirm = useConfirm();
  const [avatarState, setAvatarState] = useState<AvatarState>("idle");
  const [avatarMessage, setAvatarMessage] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  type KBStatus = "idle" | "loading" | "loaded" | "error";
  const [kbStatus, setKbStatus] = useState<KBStatus>(admin ? "idle" : "loaded");
  const [kbChunks, setKbChunks] = useState<number | null>(null);
  const [kbError, setKbError] = useState<string | null>(null);
  const [chatLoading, setChatLoading] = useState(false);
  const loaded = kbStatus === "loaded";
  const [retrieval, setRetrieval] = useState<RetrievalRow[]>([]);
  const [guards, setGuards] = useState<GuardRow[]>([]);
  const [rerank, setRerank] = useState<RerankTrace | null>(null);
  const [critique, setCritique] = useState<CritiqueRow | null>(null);
  const [threshold, setThreshold] = useState<number | null>(null);
  const [topK, setTopK] = useState<number | null>(null);
  const [showRetrieval, setShowRetrieval] = useState(false);
  // URL of a product image opened full-screen (click a thumbnail to enlarge).
  const [zoomedImage, setZoomedImage] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ProfileMap>({ default: {} });
  const [activeProfile, setActiveProfile] = useState<string>("default");
  const scrollRef = useRef<HTMLDivElement>(null);
  // Holds the pending avatar-transition timer so a new turn (or unmount) can
  // cancel it — otherwise overlapping turns leave stale avatar state behind.
  const avatarTimerRef = useRef<number | null>(null);
  // Auto-expand the inspection panel only on the first answer of a session;
  // after that, respect whatever the user toggles.
  const hasAutoExpandedRef = useRef(false);
  // Drives the typing indicator's honest, time-based phase copy. No real
  // sub-step signal exists (single blocking response), so this is generic.
  const [loadingPhase, setLoadingPhase] = useState<"search" | "generate">("search");
  // Per-tab conversation id. The server keys each visitor's history / dialogue
  // stage / intent on this, so concurrent booth visitors never overwrite each
  // other's state. Generated once on mount; rotated on Clear to start a fresh
  // server-side session. Older browsers without crypto.randomUUID fall back to
  // a timestamp+random id (uniqueness across tabs is all we need).
  const sessionIdRef = useRef<string>(
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : `s-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  );

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, chatLoading]);

  // Clear any pending avatar timer when the component unmounts.
  useEffect(() => () => {
    if (avatarTimerRef.current !== null) window.clearTimeout(avatarTimerRef.current);
  }, []);

  // Time-based loading phase: "searching" first, then "generating" a beat
  // later. Honest generic copy — there's no real per-step signal to track.
  useEffect(() => {
    if (!chatLoading) return;
    setLoadingPhase("search");
    const id = window.setTimeout(() => setLoadingPhase("generate"), 700);
    return () => window.clearTimeout(id);
  }, [chatLoading]);

  // Load profiles on mount
  useEffect(() => {
    fetch("/api/profiles")
      .then((r) => r.json())
      .then((data: ProfilesResponse) => {
        setProfiles(data.profiles);
        setActiveProfile(data.active);
      })
      .catch(() => {});
  }, []);

  async function handleActivateProfile(name: string) {
    await fetch("/api/profiles/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    setActiveProfile(name);
  }

  async function handleDeleteProfile(name: string) {
    const ok = await confirm({
      title: `刪除 Profile "${name}"?`,
      message: "此 Profile 會被永久移除,無法復原。",
      confirmLabel: "刪除",
    });
    if (!ok) return;
    await fetch(`/api/profiles/${name}`, { method: "DELETE" });
    const updated = { ...profiles };
    delete updated[name];
    setProfiles(updated);
    if (activeProfile === name) {
      setActiveProfile("default");
    }
  }

  async function handleIngest() {
    setKbStatus("loading");
    setKbError(null);
    setKbChunks(null);
    try {
      const res = await fetch("/api/chat/ingest", { method: "POST" });
      const data = await res.json();
      if (data.status === "ok") {
        setKbStatus("loaded");
        setKbChunks(data.chunks ?? null);
      } else {
        setKbStatus("error");
        setKbError(data.message || "Ingest failed");
      }
    } catch (e) {
      setKbStatus("error");
      setKbError(String(e));
    }
  }

  async function handleSendText(text: string) {
    if (!text || chatLoading || !loaded) return;
    // Cancel a still-pending avatar transition from a previous turn.
    if (avatarTimerRef.current !== null) {
      window.clearTimeout(avatarTimerRef.current);
      avatarTimerRef.current = null;
    }
    setMessages((m) => [...m, { role: "user", content: text }]);
    setInput("");
    setChatLoading(true);
    setAvatarState("think");
    setAvatarMessage("Searching...");
    try {
      const res = await fetch("/api/chat/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, session_id: sessionIdRef.current }),
      });
      const data: QueryResponse = await res.json();
      if (data.status === "ok" && data.reply !== undefined) {
        let replyContent = data.reply;
        let emotion: string | undefined;

        // The graph's SystemPrompt decides output format. Try parsing
        // chatbot-style JSON regardless of who picked it; if it isn't JSON,
        // parseChatbotOutput returns null and we just render the raw reply.
        const parsed = parseChatbotOutput(data.reply);
        if (parsed) {
          replyContent = parsed.reply;
          emotion = parsed.emotion;
        }

        setMessages((m) => [
          ...m,
          {
            role: "assistant",
            content: replyContent,
            blocked: data.blocked,
            emotion,
            productImages: data.product_images,
          },
        ]);
        setRetrieval(data.retrieval || []);
        setGuards(data.guards || []);
        setRerank(data.rerank ?? null);
        setCritique(data.critique ?? null);
        setThreshold(data.threshold ?? null);
        setTopK(data.top_k ?? null);

        // First answer of the session pops the inspection panel open so the
        // pipeline detail is discoverable; later turns respect the user's toggle.
        if (
          !hasAutoExpandedRef.current &&
          ((data.retrieval?.length ?? 0) > 0 || (data.guards?.length ?? 0) > 0)
        ) {
          hasAutoExpandedRef.current = true;
          setShowRetrieval(true);
        }

        if (data.blocked) {
          setAvatarState("error");
          setAvatarMessage("Blocked by guardrail");
        } else if (emotion) {
          const theme = getEmotionTheme(emotion);
          setAvatarState("talk");
          setAvatarMessage("");
          // A short "talk" beat, then reveal the emotion — reactive rather than
          // a fixed 1.5s wait that lagged behind fast responses.
          avatarTimerRef.current = window.setTimeout(() => {
            setAvatarState(emotionToAvatarState(emotion));
            setAvatarMessage(`Feeling: ${theme.label.toLowerCase()}`);
          }, 600);
        } else {
          setAvatarState("talk");
          setAvatarMessage("");
          avatarTimerRef.current = window.setTimeout(() => {
            setAvatarState("idle");
            setAvatarMessage("");
          }, 600);
        }
      } else {
        const reason = data.message || "伺服器回傳未知錯誤";
        setMessages((m) => [
          ...m,
          { role: "assistant", content: `⚠ 查詢失敗:${reason}`, error: true, retryText: text },
        ]);
        setAvatarState("error");
        setAvatarMessage(reason);
      }
    } catch {
      // Almost always a network/fetch failure (backend down, proxy error).
      // Show actionable copy instead of a raw `TypeError: Failed to fetch`.
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: "⚠ 無法連線到伺服器,請確認後端是否運行後再試一次。",
          error: true,
          retryText: text,
        },
      ]);
      setAvatarState("error");
      setAvatarMessage("無法連線到伺服器");
    } finally {
      setChatLoading(false);
    }
  }

  async function handleSend() {
    await handleSendText(input.trim());
  }

  async function handleClear() {
    await fetch("/api/chat/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionIdRef.current }),
    });
    // Rotate to a fresh server-side session so the next turn starts clean even
    // if the just-dropped id were somehow reused.
    sessionIdRef.current =
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setMessages([]);
    setRetrieval([]);
    setGuards([]);
    setRerank(null);
    setCritique(null);
    setShowRetrieval(false);
    hasAutoExpandedRef.current = false;
    // Kiosk mode has no Load-KB button, so dropping back to "idle" would
    // permanently disable send. Keep it "loaded" there.
    setKbStatus(admin ? "idle" : "loaded");
    setKbChunks(null);
    setKbError(null);
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
          <Avatar state={avatarState} message={avatarMessage} size={128} />
        </div>

        {admin && (
          <button
            onClick={handleIngest}
            disabled={kbStatus === "loading"}
            className="flex items-center justify-center gap-2 px-3 py-2 bg-[#e07830] hover:bg-[#f08840] disabled:opacity-50 text-white text-sm rounded transition-colors"
          >
            <Database size={14} />
            {loaded ? "Reload KB" : "Load KB"}
          </button>
        )}

        {/* Profile selector — operator-only */}
        {admin && (
          <div>
            <div className="text-[10px] uppercase tracking-widest text-[#555] mb-2">Profile</div>
            <div className="flex flex-col gap-1">
              {Object.keys(profiles).map((name) => (
                <div
                  key={name}
                  className={`flex items-center justify-between px-2 py-1.5 rounded text-xs cursor-pointer transition-colors ${
                    name === activeProfile
                      ? "bg-[#0d2a25] border border-[#00ccaa]/40 text-[#00ccaa]"
                      : "border border-transparent text-[#555] hover:text-[#888] hover:bg-[#1a1a1a]"
                  }`}
                  onClick={() => handleActivateProfile(name)}
                >
                  <span className="truncate">{name}</span>
                  {name !== "default" && (
                    <button
                      onClick={(e) => { e.stopPropagation(); handleDeleteProfile(name); }}
                      className="ml-1 text-[#333] hover:text-[#888] transition-colors flex-shrink-0"
                      title={`Delete "${name}"`}
                    >
                      <Trash2 size={11} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {threshold !== null && (
          <div className="text-[10px] text-[#777] mt-auto font-mono">
            threshold: {threshold} · top_k: {topK}
          </div>
        )}
      </aside>

      {/* Right: Chat + retrieval */}
      <section className="flex-1 flex flex-col">
        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto p-6 space-y-4">
          {/* CLI Title — always first, pushed up as messages accumulate */}
          <div className="flex flex-col items-center py-8">
            <pre
              className="text-[9px] leading-[1.25] select-none overflow-x-auto"
              style={{
                color: "rgba(0,204,170,0.75)",
                textShadow: "0 0 10px rgba(0,204,170,0.55), 0 0 24px rgba(0,204,170,0.2)",
                fontFamily: '"Courier New", Courier, monospace',
                letterSpacing: 0,
              }}
            >
              {RAG_LOOM_ASCII}
            </pre>
            <div
              className="mt-3 font-mono text-[10px] tracking-[0.25em] uppercase"
              style={{ color: "rgba(0,204,170,0.35)" }}
            >
              Local RAG Pipeline
            </div>
            <div className="mt-1 font-mono text-[10px] text-[#2a2a2a] flex gap-2">
              <span>ollama/gemma3:4b</span>
              <span>·</span>
              <span>nomic-embed-text</span>
              <span>·</span>
              <span>chromadb</span>
            </div>
            <div className="mt-3 font-mono text-[11px]" style={{ color: "rgba(0,204,170,0.45)" }}>
              {">"}{" "}
              {loaded ? "Knowledge base loaded. Ready." : "Awaiting knowledge base..."}
            </div>
          </div>

          {/* KB Status Block — CLI terminal receipt (operator-only; the kiosk
              starts pre-loaded with no ingest action, so the receipt is noise) */}
          {admin && kbStatus !== "idle" && (
            <div className="flex justify-center">
              <div
                className="w-full max-w-md font-mono text-[11px] border border-[#1a3030] rounded px-4 py-3 space-y-1"
                style={{ background: "rgba(0,20,18,0.6)" }}
              >
                <div className="text-[#00ccaa]/40 mb-2">{"─".repeat(3)} kb ingest {"─".repeat(28)}</div>
                {kbStatus === "loading" && (
                  <div className="flex items-center gap-2 text-[#00ccaa]/70">
                    <span>{">"} loading knowledge base</span>
                    <span className="flex items-center gap-0.5">
                      {[0, 150, 300].map((delay) => (
                        <span
                          key={delay}
                          className="w-1 h-1 rounded-full bg-[#00ccaa] animate-bounce"
                          style={{ animationDelay: `${delay}ms`, boxShadow: "0 0 4px rgba(0,204,170,0.6)" }}
                        />
                      ))}
                    </span>
                  </div>
                )}
                {kbStatus === "loaded" && (
                  <>
                    <div className="text-[#00ccaa]/70">{">"} indexing complete</div>
                    {kbChunks !== null && (
                      <div className="text-[#00ccaa]">{"  "}[OK] {kbChunks} chunks loaded</div>
                    )}
                    <div className="text-[#00ccaa]/40">{">"} ready for queries</div>
                  </>
                )}
                {kbStatus === "error" && (
                  <>
                    <div className="text-[#ff5566]/80">{">"} ingest failed</div>
                    {kbError && (
                      <div className="text-[#ff5566] border-l-2 border-[#ff5566]/40 pl-2">[FAIL] {kbError}</div>
                    )}
                    <div className="text-[#555]">{">"} check data directory and retry</div>
                  </>
                )}
              </div>
            </div>
          )}

          {messages.length === 0 && (
            <div className="flex flex-col items-center gap-4 px-6">
              {loaded ? (
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
              ) : (
                kbStatus !== "loading" && (
                  // KB not loaded yet → point at the left "Load KB" button instead
                  // of leaving the panel blank (suggested questions are hidden).
                  <div className="max-w-md rounded-lg border border-dashed border-[#00ccaa]/25 bg-[#0d1a1f]/50 px-5 py-4 text-center">
                    <div className="text-sm text-[#c0e0d8]">
                      {kbStatus === "error" ? "知識庫載入失敗" : "尚未載入知識庫"}
                    </div>
                    <div className="mt-1.5 text-xs leading-relaxed text-[#6a8a84]">
                      請先點左側的 <span className="text-[#e07830]">Load KB</span>{" "}
                      按鈕載入產品資料,{kbStatus === "error" ? "再試一次。" : "才能開始提問。"}
                    </div>
                  </div>
                )
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
                        : m.error
                        ? "bg-[#2a1212] text-[#ffb3bd] border border-[#ff5566]/30 border-l-2 border-l-[#ff5566]"
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
                    {m.productImages && m.productImages.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        {m.productImages.map((img) => (
                          <button
                            key={img.product_id}
                            type="button"
                            onClick={() => setZoomedImage(img.url)}
                            className="rounded-md border border-[#1a3540] transition-shadow hover:border-[#00ccaa]/60 hover:shadow-[0_0_14px_rgba(0,204,170,0.2)] cursor-zoom-in"
                          >
                            <img
                              src={img.url}
                              alt={img.product_id}
                              loading="lazy"
                              onError={(e) => {
                                const btn = e.currentTarget.parentElement;
                                if (btn) btn.style.display = "none";
                              }}
                              className="h-28 w-auto rounded-md object-contain"
                            />
                          </button>
                        ))}
                      </div>
                    )}
                    {m.error && m.retryText && (
                      <button
                        onClick={() => handleSendText(m.retryText!)}
                        disabled={chatLoading || !loaded}
                        className="mt-2 flex items-center gap-1.5 rounded border border-[#ff5566]/40 px-2 py-1 text-[11px] text-[#ffb3bd] transition-colors hover:bg-[#ff5566]/10 disabled:opacity-40"
                      >
                        <RotateCw size={11} />
                        重試
                      </button>
                    )}
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>

          {/* Typing indicator */}
          {chatLoading && messages.some((m) => m.role === "user") && (
            <motion.div
              initial={{ opacity: 0, x: -12 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex justify-start"
            >
              <div className="bg-[#0a1a1f] border border-[#1a3540] border-l-2 border-l-[#00ccaa] px-4 py-3 rounded-lg flex items-center gap-2.5 shadow-[0_0_18px_rgba(0,204,170,0.06)]">
                <span className="flex items-center gap-1.5">
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
                </span>
                <span className="text-[11px] font-mono text-[#00ccaa]/60">
                  {loadingPhase === "search" ? "檢索知識庫…" : "生成回覆…"}
                </span>
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
            rows={3}
            className="flex-1 bg-[#1a1a1a] border border-[#2a2a2a] rounded px-3 py-2 text-sm resize-none focus:outline-none focus:border-[#00ccaa] transition-all"
            style={{ boxShadow: "none" }}
            onFocus={(e) => (e.currentTarget.style.boxShadow = "0 0 8px rgba(0,204,170,0.2)")}
            onBlur={(e) => (e.currentTarget.style.boxShadow = "none")}
          />
          <button
            onClick={handleSend}
            disabled={chatLoading || !input.trim()}
            className="px-4 py-2 bg-[#e07830] hover:bg-[#f08840] hover:shadow-[0_0_10px_rgba(224,120,48,0.4)] disabled:opacity-40 text-white text-sm rounded self-stretch transition-all flex items-center justify-center"
          >
            <Send size={15} />
          </button>
          <button
            onClick={handleClear}
            disabled={chatLoading}
            title="Clear chat history and reset memory"
            aria-label="Clear chat history and reset memory"
            className="px-3 py-2 border border-[#2a2a2a] hover:bg-[#1a1a1a] hover:border-[#00ccaa]/30 hover:text-[#888] disabled:opacity-40 text-[#555] text-sm rounded self-stretch transition-colors flex items-center gap-1.5 whitespace-nowrap"
          >
            <X size={13} />
            Clear
          </button>
        </div>

        {/* Inspection panel — guards + rerank + critique + retrieval */}
        {(retrieval.length > 0 || guards.length > 0 || rerank || critique) && (
          <div className="border-t border-[#00ccaa]/10 bg-[#141414]">
            <button
              onClick={() => setShowRetrieval((v) => !v)}
              aria-expanded={showRetrieval}
              aria-controls="inspection-panel-body"
              className="w-full flex items-center justify-between px-4 py-2 text-[#444] hover:text-[#00ccaa]/60 hover:bg-[#0d1a1f] transition-colors text-xs font-mono"
            >
              <span className="flex items-center gap-3">
                {guards.length > 0 && (
                  <span>
                    Guards ·{" "}
                    {(() => {
                      const passN = guards.filter((g) => g.status === "pass").length;
                      const blockN = guards.filter((g) => g.status === "block").length;
                      const skipN = guards.filter((g) => g.status === "skip").length;
                      const parts = [`${passN}✓`];
                      if (blockN > 0) parts.push(`${blockN}⊘`);
                      if (skipN > 0) parts.push(`${skipN}–`);
                      return parts.join(" ");
                    })()}
                  </span>
                )}
                {rerank && (
                  <span>
                    Rerank · {rerank.kept}/{rerank.total} kept
                  </span>
                )}
                {critique && (
                  <span>
                    Critic ·{" "}
                    {critique.verdict === "skip"
                      ? "–"
                      : critique.verdict === "pass"
                      ? "✓"
                      : "⊘"}
                  </span>
                )}
                {retrieval.length > 0 && <span>Retrieval · {retrieval.length} chunks</span>}
              </span>
              {showRetrieval ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </button>
            {showRetrieval && (
              <div id="inspection-panel-body" className="px-4 pb-4 max-h-72 overflow-y-auto space-y-4">
                {guards.length > 0 && (
                  <div>
                    <div className="text-[10px] uppercase tracking-widest text-[#555] mb-1.5">
                      Guards
                    </div>
                    <div className="text-[11px] font-mono space-y-0.5">
                      {guards.map((g, i) => {
                        const symbol =
                          g.status === "pass" ? "✓" : g.status === "block" ? "⊘" : "–";
                        const statusColor =
                          g.status === "block"
                            ? "text-[#f0a040]"
                            : g.status === "skip"
                            ? "text-[#555]"
                            : "text-[#00ccaa]/80";
                        return (
                          <div key={i} className="flex items-baseline gap-2">
                            <span className={`w-3 ${statusColor}`}>{symbol}</span>
                            <span className="w-24 text-[#888]">{g.name}</span>
                            <span className={`uppercase tracking-wider ${statusColor}`}>
                              {g.status}
                            </span>
                            {g.detail && (
                              <span className="text-[#555]">— {g.detail}</span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
                {rerank && (
                  <div>
                    <div className="text-[10px] uppercase tracking-widest text-[#555] mb-1.5">
                      Rerank · {rerank.kept}/{rerank.total} kept
                    </div>
                    <div className="text-[11px] font-mono space-y-0.5">
                      {rerank.verdicts.map((v) => {
                        const symbol = v.keep ? "✓" : "⊘";
                        const color = v.keep ? "text-[#00ccaa]/80" : "text-[#f0a040]";
                        return (
                          <div key={v.i} className="flex items-baseline gap-2">
                            <span className={`w-3 ${color}`}>{symbol}</span>
                            <span className="w-6 text-[#444]">#{v.i + 1}</span>
                            <span className="w-44 text-[#00ccaa]/60 truncate">{v.source}</span>
                            <span className="w-12 text-right text-[#555]">{v.score.toFixed(2)}</span>
                            <span className="text-[#555] flex-1 truncate">— {v.reason}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
                {critique && (
                  <div>
                    <div className="text-[10px] uppercase tracking-widest text-[#555] mb-1.5">
                      Critic{critique.grounded ? " · grounded" : ""}
                    </div>
                    <div className="text-[11px] font-mono">
                      {(() => {
                        const symbol =
                          critique.verdict === "pass"
                            ? "✓"
                            : critique.verdict === "skip"
                            ? "–"
                            : "⊘";
                        const statusColor =
                          critique.verdict === "pass"
                            ? "text-[#00ccaa]/80"
                            : critique.verdict === "skip"
                            ? "text-[#555]"
                            : "text-[#f0a040]";
                        return (
                          <div className="flex items-baseline gap-2">
                            <span className={`w-3 ${statusColor}`}>{symbol}</span>
                            <span className={`w-24 uppercase tracking-wider ${statusColor}`}>
                              {critique.verdict || "—"}
                            </span>
                            {critique.reason && (
                              <span className="text-[#555] flex-1">{critique.reason}</span>
                            )}
                            {critique.revised && (
                              <span className="text-[#a070d0] uppercase text-[10px]">revised</span>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                  </div>
                )}
                {retrieval.length > 0 && (
                  <div>
                    <div className="text-[10px] uppercase tracking-widest text-[#555] mb-1.5">
                      Retrieval
                    </div>
                    <table className="w-full text-[11px] font-mono">
                      <caption className="sr-only">檢索結果:每個 chunk 的來源、分數、距離與是否通過門檻</caption>
                      <thead className="text-[#444]">
                        <tr className="border-b border-[#1a3540]">
                          <th scope="col" className="text-left py-1 pr-2">#</th>
                          <th scope="col" className="text-left py-1 pr-2">Source</th>
                          <th scope="col" className="text-right py-1 pr-2">Score</th>
                          <th scope="col" className="text-right py-1 pr-2">Dist</th>
                          <th scope="col" className="text-center py-1 pr-2">Pass</th>
                          <th scope="col" className="text-left py-1">Chunk</th>
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
          </div>
        )}
      </section>

      {/* Full-screen product-image viewer. Click anywhere (or Esc-less tap on
          mobile) to dismiss; the image itself stops propagation so tapping it
          doesn't close. */}
      <AnimatePresence>
        {zoomedImage && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={() => setZoomedImage(null)}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6 cursor-zoom-out backdrop-blur-sm"
          >
            <motion.img
              key={zoomedImage}
              src={zoomedImage}
              alt="product"
              initial={{ scale: 0.9 }}
              animate={{ scale: 1 }}
              exit={{ scale: 0.9 }}
              transition={{ duration: 0.15, ease: "easeOut" }}
              onClick={(e) => e.stopPropagation()}
              className="max-h-full max-w-full rounded-lg border border-[#1a3540] object-contain shadow-2xl cursor-default"
            />
            <button
              type="button"
              onClick={() => setZoomedImage(null)}
              aria-label="Close"
              className="absolute right-5 top-5 flex h-9 w-9 items-center justify-center rounded-full bg-[#0a1a1f]/80 text-[#d0e8e0] border border-[#1a3540] transition-colors hover:bg-[#00ccaa]/20"
            >
              <X size={18} />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
