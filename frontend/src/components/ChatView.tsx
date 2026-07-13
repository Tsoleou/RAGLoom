import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Send, X, Database, ChevronDown, ChevronUp, Trash2, RotateCw } from "lucide-react";
import { SilkAvatar } from "./avatar/SilkAvatar";
import { SILK_STATUS_TEXT, silkStatusColor } from "./avatar/silkTheme";
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
      title: `Delete profile "${name}"?`,
      message: "This profile will be permanently removed and cannot be recovered.",
      confirmLabel: "Delete",
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
        const reason = data.message || "Server returned an unknown error";
        setMessages((m) => [
          ...m,
          { role: "assistant", content: `⚠ Query failed: ${reason}`, error: true, retryText: text },
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
          content: "⚠ Cannot reach the server. Make sure the backend is running, then try again.",
          error: true,
          retryText: text,
        },
      ]);
      setAvatarState("error");
      setAvatarMessage("Cannot reach the server");
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

  // ── Right-rail pipeline summary derivations ──────────────────────────────
  const hasPipeline =
    retrieval.length > 0 || guards.length > 0 || !!rerank || !!critique;
  const guardPass = guards.filter((g) => g.status === "pass").length;
  const guardBlock = guards.filter((g) => g.status === "block").length;
  const guardSkip = guards.filter((g) => g.status === "skip").length;

  return (
    <div
      className="h-full flex min-h-0 gap-6 p-7 font-hud text-[#e6ede9]"
      style={{
        background:
          "radial-gradient(120% 90% at 82% -5%, rgba(0,204,170,0.09), transparent 52%), #101312",
      }}
    >
      {/* ░ chat column ░ */}
      <section className="flex-1 flex flex-col min-w-0 overflow-hidden rounded-[18px] border border-[#1c2320] bg-white/[0.02]">
        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-8 py-8 flex flex-col gap-5">
          {/* Wordmark hero — always first, pushed up as messages accumulate */}
          <div className="flex flex-col items-center gap-2 pt-1.5 pb-0.5">
            <div
              className="text-[30px] font-bold tracking-[0.15em] text-[#00ccaa]"
              style={{ textShadow: "0 0 26px rgba(0,204,170,0.45)" }}
            >
              RAGLOOM
            </div>
            <div className="text-[12px] tracking-[0.3em] uppercase text-[#4a5a56]">
              Local RAG Pipeline
            </div>
            <div className="font-hud-mono text-[11px] text-[#33413d] flex gap-2">
              <span>ollama/gemma3:4b</span>
              <span>·</span>
              <span>nomic-embed-text</span>
              <span>·</span>
              <span>chromadb</span>
            </div>
            <div
              className="font-hud-mono text-[12px] mt-1.5"
              style={{ color: "rgba(0,204,170,0.5)" }}
            >
              {">"}{" "}
              {loaded
                ? `Knowledge base loaded${
                    kbChunks !== null ? ` · ${kbChunks} chunks` : ""
                  } · Ready.`
                : "Awaiting knowledge base…"}
            </div>
          </div>

          {/* KB ingest receipt — operator-only (kiosk starts pre-loaded) */}
          {admin && kbStatus !== "idle" && (
            <div className="flex justify-center">
              <div
                className="w-full max-w-md font-hud-mono text-[11px] border border-[#1a3030] rounded-[10px] px-4 py-3 space-y-1"
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
                    <div className="text-[#5c6a66]">{">"} check data directory and retry</div>
                  </>
                )}
              </div>
            </div>
          )}

          {/* Empty state: suggestion pills, or a Load-KB hint when not loaded */}
          {messages.length === 0 && (
            <div className="flex flex-col items-center gap-4 px-4">
              {loaded ? (
                <motion.div
                  className="flex flex-wrap justify-center gap-2.5 max-w-xl"
                  variants={containerVariants}
                  initial="hidden"
                  animate="visible"
                >
                  {SUGGESTED_QUESTIONS.map((q) => (
                    <motion.button
                      key={q}
                      variants={chipVariants}
                      whileHover={{ y: -2 }}
                      transition={{ type: "spring", stiffness: 300, damping: 20 }}
                      onClick={() => handleSendText(q)}
                      className="px-4 py-2.5 text-[13px] rounded-[24px] border border-[#24302c] bg-white/[0.03] text-[#8a9a96] hover:border-[#00ccaa]/40 hover:text-[#cfeee2] transition-colors"
                    >
                      {q}
                    </motion.button>
                  ))}
                </motion.div>
              ) : (
                kbStatus !== "loading" && (
                  <div className="max-w-md rounded-[14px] border border-dashed border-[#00ccaa]/25 bg-[#0d1a1f]/50 px-5 py-4 text-center">
                    <div className="text-sm text-[#c0e0d8]">
                      {kbStatus === "error" ? "Knowledge base failed to load" : "Knowledge base not loaded yet"}
                    </div>
                    <div className="mt-1.5 text-xs leading-relaxed text-[#6a8a84]">
                      Click the <span className="text-[#e07830]">Load KB</span>{" "}
                      button on the right to load the product data{kbStatus === "error" ? " and try again." : " before you can ask questions."}
                    </div>
                  </div>
                )
              )}
            </div>
          )}

          <AnimatePresence initial={false}>
            {messages.map((m, i) => {
              const emTheme = m.emotion ? getEmotionTheme(m.emotion) : null;

              // User turn — right-aligned orange bubble.
              if (m.role === "user") {
                return (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, x: 20, y: 6 }}
                    animate={{ opacity: 1, x: 0, y: 0 }}
                    transition={{ duration: 0.22, ease: "easeOut" }}
                    className="flex justify-end"
                  >
                    <div className="max-w-[68%] px-[18px] py-3.5 text-[15px] leading-[1.55] text-white bg-[#e07830] rounded-[16px_16px_4px_16px] shadow-[0_8px_24px_rgba(224,120,48,0.25)] whitespace-pre-wrap">
                      {m.content}
                    </div>
                  </motion.div>
                );
              }

              // Assistant turn — silk mini glyph + HUD bubble.
              const miniState: AvatarState = m.error || m.blocked ? "error" : "happy";
              const bubbleClass = m.error
                ? "bg-[#2a1212] text-[#ffb3bd] border border-[#ff5566]/30"
                : m.blocked
                ? "bg-[#2a1a10] text-[#f0c070] border border-[#f0a040]/30"
                : "bg-[rgba(0,204,170,0.06)] text-[#cfeee2] border border-[rgba(0,204,170,0.18)]";
              return (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, x: -20, y: 6 }}
                  animate={{ opacity: 1, x: 0, y: 0 }}
                  transition={{ duration: 0.22, ease: "easeOut" }}
                  className="flex gap-3 items-start justify-start"
                >
                  <div className="w-10 h-10 flex-shrink-0 mt-0.5 rounded-[11px] overflow-hidden bg-[#0e1614] border border-[#1c2a26]">
                    <SilkAvatar state={miniState} message="" bare size={40} />
                  </div>
                  <div
                    className={`max-w-[76%] px-5 py-4 text-[15px] leading-[1.65] rounded-[4px_16px_16px_16px] whitespace-pre-wrap ${bubbleClass}`}
                  >
                    {m.blocked && (
                      <div className="text-[10px] uppercase tracking-wider text-[#f0a040] mb-1 font-hud-mono">
                        ⊘ Blocked by Guardrail
                      </div>
                    )}
                    {emTheme && (
                      <div className="mb-2">
                        <span
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-hud-mono font-bold tracking-wide"
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
                      <div className="mt-3 flex flex-wrap gap-2">
                        {m.productImages.map((img) => (
                          <button
                            key={img.product_id}
                            type="button"
                            onClick={() => setZoomedImage(img.url)}
                            className="rounded-[10px] border border-[rgba(0,204,170,0.18)] transition-shadow hover:border-[#00ccaa]/60 hover:shadow-[0_0_14px_rgba(0,204,170,0.2)] cursor-zoom-in"
                          >
                            <img
                              src={img.url}
                              alt={img.product_id}
                              loading="lazy"
                              onError={(e) => {
                                const btn = e.currentTarget.parentElement;
                                if (btn) btn.style.display = "none";
                              }}
                              className="h-28 w-auto rounded-[10px] object-contain"
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
                        Retry
                      </button>
                    )}
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>

          {/* Typing indicator — silk mini + bouncing dots */}
          {chatLoading && messages.some((m) => m.role === "user") && (
            <motion.div
              initial={{ opacity: 0, x: -12 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex gap-3 items-center"
            >
              <div className="w-10 h-10 flex-shrink-0 rounded-[11px] overflow-hidden bg-[#0e1614] border border-[#1c2a26]">
                <SilkAvatar state="think" message="" bare size={40} />
              </div>
              <div className="px-[18px] py-3.5 rounded-[4px_16px_16px_16px] bg-[rgba(0,204,170,0.06)] border border-[rgba(0,204,170,0.18)] flex items-center gap-2.5">
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
                <span className="font-hud-mono text-[12px]" style={{ color: "rgba(0,204,170,0.6)" }}>
                  {loadingPhase === "search" ? "Searching knowledge base…" : "Generating reply…"}
                </span>
              </div>
            </motion.div>
          )}
        </div>

        {/* Input — pill field + circular send / clear */}
        <div className="flex-shrink-0 flex items-end gap-3 border-t border-[#1c2320] bg-white/[0.015] px-[22px] py-[18px]">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask about a product…"
            rows={1}
            disabled={!loaded}
            className="flex-1 resize-none bg-white/[0.03] border border-[#24302c] rounded-[26px] px-5 py-[14px] text-[14px] text-[#e6ede9] placeholder-[#5c6a66] focus:outline-none focus:border-[#00ccaa]/50 disabled:opacity-50 transition-colors"
            style={{ minHeight: 52, maxHeight: 140 }}
          />
          <button
            onClick={handleSend}
            disabled={chatLoading || !input.trim() || !loaded}
            aria-label="Send"
            className="flex-shrink-0 w-[52px] h-[52px] rounded-full bg-[#e07830] text-white flex items-center justify-center hover:bg-[#f08840] disabled:opacity-40 transition-colors shadow-[0_8px_20px_rgba(224,120,48,0.3)]"
          >
            <Send size={17} />
          </button>
          <button
            onClick={handleClear}
            disabled={chatLoading}
            title="Clear chat history and reset memory"
            aria-label="Clear chat history and reset memory"
            className="flex-shrink-0 w-[52px] h-[52px] rounded-full bg-transparent border border-[#24302c] text-[#6a7a76] flex items-center justify-center hover:border-[#00ccaa]/35 hover:text-[#a0b0ac] disabled:opacity-40 transition-colors"
          >
            <X size={16} />
          </button>
        </div>
      </section>

      {/* ░ right rail ░ */}
      <aside className="w-[266px] flex-shrink-0 flex flex-col gap-4 overflow-y-auto">
        {/* Avatar card */}
        <div className="flex flex-col items-center gap-3.5 rounded-[18px] border border-[#1c2320] bg-white/[0.02] p-[22px]">
          <div className="relative overflow-hidden rounded-[16px] border border-[#1c2a26] bg-[#0e1614] p-4">
            <div
              aria-hidden="true"
              className="absolute left-0 right-0 h-px bg-[#3fd0bd] opacity-[0.06]"
              style={{ animation: "scanY 4s linear infinite" }}
            />
            <SilkAvatar state={avatarState} message="" bare size={170} />
          </div>
          <div
            className="font-hud-mono text-[12px] tracking-[0.22em]"
            style={{ color: silkStatusColor(avatarState) }}
          >
            {SILK_STATUS_TEXT[avatarState]}
          </div>
          {avatarMessage && (
            <div className="text-[13px] text-[#8a9a96] text-center leading-relaxed">
              {avatarMessage}
            </div>
          )}
        </div>

        {/* Pipeline card — summary always visible, detail on demand */}
        <div className="rounded-[18px] border border-[#1c2320] bg-white/[0.02] p-5 flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <span className="text-[11px] tracking-[0.2em] uppercase text-[#4a5a56]">Pipeline</span>
            {hasPipeline && (
              <button
                onClick={() => setShowRetrieval((v) => !v)}
                aria-expanded={showRetrieval}
                aria-controls="pipeline-detail"
                aria-label={showRetrieval ? "Hide pipeline detail" : "Show pipeline detail"}
                className="text-[#4a5a56] hover:text-[#00ccaa]/70 transition-colors"
              >
                {showRetrieval ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>
            )}
          </div>

          <div className="flex flex-col gap-[11px] text-[13.5px]">
            <div className="flex justify-between items-center">
              <span className="text-[#8a9a96]">Guards</span>
              <span className="text-[#00ccaa]">
                {guards.length
                  ? `${guardPass} ✓${guardBlock ? ` · ${guardBlock} ⊘` : ""}${
                      guardSkip ? ` · ${guardSkip} –` : ""
                    }`
                  : "—"}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-[#8a9a96]">Rerank</span>
              <span className="text-[#7fd8c4]">
                {rerank ? `${rerank.kept} / ${rerank.total} kept` : "—"}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-[#8a9a96]">Critic</span>
              <span className="text-[#00ccaa]">
                {critique
                  ? critique.verdict === "pass"
                    ? `✓${critique.grounded ? " grounded" : ""}`
                    : critique.verdict === "skip"
                    ? "–"
                    : "⊘"
                  : "—"}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-[#8a9a96]">Retrieval</span>
              <span className="text-[#7fd8c4]">
                {retrieval.length ? `${retrieval.length} chunks` : "—"}
              </span>
            </div>
          </div>

          <div className="h-px bg-[#1c2320]" />
          <div className="font-hud-mono text-[12px] text-[#5c6a66]">
            {threshold !== null ? `threshold ${threshold} · top_k ${topK}` : "awaiting first query"}
          </div>

          {/* Expandable detail — full guard / rerank / critic / retrieval trace */}
          {showRetrieval && hasPipeline && (
            <div
              id="pipeline-detail"
              className="mt-1 max-h-80 overflow-y-auto space-y-4 border-t border-[#1c2320] pt-4 font-hud-mono text-[11px]"
            >
              {guards.length > 0 && (
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-[#4a5a56] mb-1.5">
                    Guards
                  </div>
                  <div className="space-y-1">
                    {guards.map((g, i) => {
                      const symbol =
                        g.status === "pass" ? "✓" : g.status === "block" ? "⊘" : "–";
                      const c =
                        g.status === "block"
                          ? "text-[#f0a040]"
                          : g.status === "skip"
                          ? "text-[#5c6a66]"
                          : "text-[#00ccaa]/80";
                      return (
                        <div key={i} className="flex items-baseline gap-1.5">
                          <span className={`w-3 ${c}`}>{symbol}</span>
                          <span className="text-[#8a9a96] flex-1 truncate">{g.name}</span>
                          <span className={`uppercase ${c}`}>{g.status}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              {rerank && (
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-[#4a5a56] mb-1.5">
                    Rerank · {rerank.kept}/{rerank.total} kept
                  </div>
                  <div className="space-y-1">
                    {rerank.verdicts.map((v) => {
                      const c = v.keep ? "text-[#00ccaa]/80" : "text-[#f0a040]";
                      return (
                        <div key={v.i} className="flex items-baseline gap-1.5">
                          <span className={`w-3 ${c}`}>{v.keep ? "✓" : "⊘"}</span>
                          <span className="text-[#7fd8c4]/70 flex-1 truncate">{v.source}</span>
                          <span className="text-[#5c6a66]">{v.score.toFixed(2)}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              {critique && (
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-[#4a5a56] mb-1.5">
                    Critic{critique.grounded ? " · grounded" : ""}
                  </div>
                  {(() => {
                    const c =
                      critique.verdict === "pass"
                        ? "text-[#00ccaa]/80"
                        : critique.verdict === "skip"
                        ? "text-[#5c6a66]"
                        : "text-[#f0a040]";
                    const symbol =
                      critique.verdict === "pass"
                        ? "✓"
                        : critique.verdict === "skip"
                        ? "–"
                        : "⊘";
                    return (
                      <div className="flex items-baseline gap-1.5">
                        <span className={c}>
                          {symbol} {(critique.verdict || "—").toUpperCase()}
                        </span>
                        {critique.revised && (
                          <span className="text-[#a070d0] uppercase text-[10px]">revised</span>
                        )}
                      </div>
                    );
                  })()}
                  {critique.reason && (
                    <div className="text-[#5c6a66] mt-1 leading-relaxed">{critique.reason}</div>
                  )}
                </div>
              )}
              {retrieval.length > 0 && (
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-[#4a5a56] mb-1.5">
                    Retrieval
                  </div>
                  <div className="space-y-1.5">
                    {retrieval.map((r, i) => (
                      <div key={i}>
                        <div className="flex items-baseline gap-1.5">
                          <span className="text-[#4a5a56]">#{i + 1}</span>
                          <span className="text-[#7fd8c4]/70 flex-1 truncate">{r.source}</span>
                          <span className="text-[#8a9a96]">{r.score}</span>
                          <span className={r.passed ? "text-[#00ccaa]" : "text-[#4a5a56]"}>
                            {r.passed ? "Y" : "N"}
                          </span>
                        </div>
                        <div className="text-[#5c6a66] truncate">{r.preview.slice(0, 60)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Active profile — read-only for kiosk, switcher for operator */}
        {admin ? (
          <div className="rounded-[18px] border border-[#1c2320] bg-white/[0.02] p-5">
            <div className="text-[11px] tracking-[0.2em] uppercase text-[#4a5a56] mb-3">
              Active Profile
            </div>
            <div className="flex flex-col gap-1">
              {Object.keys(profiles).map((name) => (
                <div
                  key={name}
                  className={`flex items-center justify-between px-2.5 py-2 rounded-[10px] text-[13px] cursor-pointer transition-colors ${
                    name === activeProfile
                      ? "bg-[#0d2a25] border border-[#00ccaa]/40 text-[#00ccaa]"
                      : "border border-transparent text-[#6a7a76] hover:text-[#a0b0ac] hover:bg-white/[0.03]"
                  }`}
                  onClick={() => handleActivateProfile(name)}
                >
                  <span className="truncate">{name}</span>
                  {name !== "default" && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteProfile(name);
                      }}
                      className="ml-1 flex-shrink-0 text-[#33413d] hover:text-[#a0b0ac] transition-colors"
                      title={`Delete "${name}"`}
                    >
                      <Trash2 size={12} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between gap-3 rounded-[18px] border border-[#1c2320] bg-white/[0.02] p-5">
            <div className="flex flex-col gap-1">
              <span className="text-[11px] tracking-[0.2em] uppercase text-[#4a5a56]">
                Active Profile
              </span>
              <span className="text-[15px] font-semibold text-[#e6ede9]">{activeProfile}</span>
            </div>
            <span className="flex items-center gap-1.5 font-hud-mono text-[11px] text-[#00ccaa]">
              <span
                className="w-[7px] h-[7px] rounded-full bg-[#00ccaa]"
                style={{ boxShadow: "0 0 8px #00ccaa" }}
              />
              ACTIVE
            </span>
          </div>
        )}

        {/* Operator-only KB control */}
        {admin && (
          <button
            onClick={handleIngest}
            disabled={kbStatus === "loading"}
            className="flex items-center justify-center gap-2 rounded-[14px] bg-[#e07830] px-4 py-3 text-sm text-white hover:bg-[#f08840] disabled:opacity-50 transition-colors shadow-[0_8px_20px_rgba(224,120,48,0.25)]"
          >
            <Database size={14} />
            {loaded ? "Reload KB" : "Load KB"}
          </button>
        )}
      </aside>

      {/* Full-screen product-image viewer. Click the backdrop to dismiss; the
          image itself stops propagation so tapping it doesn't close. */}
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
