import { useState, useRef } from "react";
import { motion, AnimatePresence, MotionConfig } from "framer-motion";
import { HelpCircle, X } from "lucide-react";
import { FlowEditor } from "./components/FlowEditor";
import { ChatView } from "./components/ChatView";
import { Dashboard } from "./components/Dashboard";
import { KnowledgeBasePanel } from "./components/KnowledgeBasePanel";
import { UnlockGate } from "./components/UnlockGate";
import { Avatar } from "./components/avatar/Avatar";
import { ToastProvider } from "./components/ui/Toast";
import { ConfirmProvider } from "./components/ui/ConfirmDialog";
import { useFocusTrap } from "./hooks/useFocusTrap";

type View = "editor" | "chat" | "knowledge" | "dashboard";

// TEMP preview harness — reach via ?preview=avatar, no backend needed. Remove when done.
export function AvatarPreview() {
  const states = [
    { state: "idle" as const, msg: "Ready when you are!" },
    { state: "think" as const, msg: "Retriever..." },
    { state: "talk" as const, msg: "Generator..." },
    { state: "happy" as const, msg: "Feeling: cheerful" },
    { state: "error" as const, msg: "Retriever failed" },
  ];
  return (
    <div className="h-screen bg-[#1a1a1a] flex items-center justify-center gap-10 flex-wrap">
      {states.map((s) => (
        <div key={s.state} className="flex flex-col items-center gap-3">
          <div className="text-xs text-[#888] font-mono uppercase tracking-widest">{s.state}</div>
          <Avatar state={s.state} message={s.msg} size={140} />
        </div>
      ))}
    </div>
  );
}

function App() {
  const [view, setView] = useState<View>("editor");
  const [helpOpen, setHelpOpen] = useState(false);

  const hint =
    view === "editor"
      ? "Drag nodes to build pipeline / click Run to execute"
      : view === "chat"
        ? "Chat with the RAG pipeline"
        : view === "knowledge"
          ? "Encrypt-at-rest knowledge base — inject or remove documents"
          : "Analyze user query behavior";

  return (
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <ConfirmProvider>
          <div className="h-screen flex flex-col bg-[#1a1a1a]">
            {/* Title bar */}
            <header className="px-6 py-3 bg-[#202020] border-b border-[#2a2a2a] flex items-center justify-between">
              <div className="flex items-center gap-3">
                <h1 className="text-lg font-bold text-[#e0e0e0]">
                  <span style={{ textShadow: "0 0 18px rgba(0,204,170,0.5)" }}>RAGLoom</span>
                </h1>
                <span className="text-xs text-[#666]">{hint}</span>
              </div>

              <div className="flex items-center gap-2">
                {/* View switcher */}
                <div className="flex rounded-md border border-[#333] overflow-hidden text-xs">
                  <button
                    onClick={() => setView("editor")}
                    aria-current={view === "editor"}
                    className={`px-3 py-1.5 transition-colors ${view === "editor" ? "bg-[#e07830] text-white" : "bg-[#252525] text-[#888] hover:bg-[#2a2a2a]"}`}
                  >
                    Editor
                  </button>
                  <button
                    onClick={() => setView("chat")}
                    aria-current={view === "chat"}
                    className={`px-3 py-1.5 transition-colors ${view === "chat" ? "bg-[#e07830] text-white" : "bg-[#252525] text-[#888] hover:bg-[#2a2a2a]"}`}
                  >
                    Chat
                  </button>
                  <button
                    onClick={() => setView("knowledge")}
                    aria-current={view === "knowledge"}
                    className={`px-3 py-1.5 transition-colors ${view === "knowledge" ? "bg-[#e07830] text-white" : "bg-[#252525] text-[#888] hover:bg-[#2a2a2a]"}`}
                  >
                    Knowledge
                  </button>
                  <button
                    onClick={() => setView("dashboard")}
                    aria-current={view === "dashboard"}
                    className={`px-3 py-1.5 transition-colors ${view === "dashboard" ? "bg-[#e07830] text-white" : "bg-[#252525] text-[#888] hover:bg-[#2a2a2a]"}`}
                  >
                    Dashboard
                  </button>
                </div>

                <button
                  onClick={() => setHelpOpen(true)}
                  aria-label="Help & keyboard shortcuts"
                  title="Help & keyboard shortcuts"
                  className="flex items-center justify-center w-7 h-7 rounded-md border border-[#333] bg-[#252525] text-[#888] hover:text-[#00ccaa] hover:bg-[#2a2a2a] transition-colors"
                >
                  <HelpCircle size={15} />
                </button>
              </div>
            </header>

            {/* Main content — gated behind KB unlock when encryption is on */}
            <main className="flex-1 overflow-hidden">
              <UnlockGate>
                {view === "editor" ? (
                  <FlowEditor />
                ) : view === "chat" ? (
                  <ChatView />
                ) : view === "knowledge" ? (
                  <KnowledgeBasePanel />
                ) : (
                  <Dashboard />
                )}
              </UnlockGate>
            </main>

            <HelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
          </div>
        </ConfirmProvider>
      </ToastProvider>
    </MotionConfig>
  );
}

const VIEW_GUIDE: { name: string; desc: string }[] = [
  { name: "Editor", desc: "Drag nodes to build the RAG pipeline, then click Run" },
  { name: "Chat", desc: "Chat with the current pipeline and knowledge base" },
  { name: "Knowledge", desc: "Manage the encrypted knowledge base: ingest or remove documents" },
  { name: "Dashboard", desc: "Analyze user query behavior and knowledge gaps" },
];

const SHORTCUTS: { keys: string; desc: string }[] = [
  { keys: "Enter", desc: "Send message in Chat" },
  { keys: "Shift + Enter", desc: "New line in Chat" },
  { keys: "Delete / Backspace", desc: "Delete the selected node or connection" },
  { keys: "Esc", desc: "Cancel input / close dialog" },
];

function HelpModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(open, dialogRef, onClose);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[1100] flex items-center justify-center bg-black/60 p-4"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          onClick={onClose}
        >
          <motion.div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-label="Help"
            className="w-full max-w-md rounded-lg border border-[#00ccaa]/30 bg-[#1a1a1a] p-5 shadow-2xl"
            initial={{ opacity: 0, scale: 0.96, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 8 }}
            transition={{ duration: 0.15 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-bold text-[#e0e0e0]">RAGLoom · Help</h2>
              <button
                onClick={onClose}
                aria-label="Close"
                className="text-[#555] transition-colors hover:text-[#aaa]"
              >
                <X size={16} />
              </button>
            </div>

            <div className="mb-4">
              <div className="mb-2 text-[10px] uppercase tracking-widest text-[#00ccaa]/60">
                Tabs
              </div>
              <div className="space-y-1.5">
                {VIEW_GUIDE.map((v) => (
                  <div key={v.name} className="flex gap-3 text-xs">
                    <span className="w-20 flex-shrink-0 font-medium text-[#e07830]">{v.name}</span>
                    <span className="text-[#999]">{v.desc}</span>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <div className="mb-2 text-[10px] uppercase tracking-widest text-[#00ccaa]/60">
                Keyboard shortcuts
              </div>
              <div className="space-y-1.5">
                {SHORTCUTS.map((s) => (
                  <div key={s.keys} className="flex items-center gap-3 text-xs">
                    <kbd className="w-32 flex-shrink-0 rounded border border-[#333] bg-[#252525] px-2 py-0.5 text-center font-mono text-[10px] text-[#c0c0c0]">
                      {s.keys}
                    </kbd>
                    <span className="text-[#999]">{s.desc}</span>
                  </div>
                ))}
              </div>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export default App;
