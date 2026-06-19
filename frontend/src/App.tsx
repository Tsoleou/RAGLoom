import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { HelpCircle, X } from "lucide-react";
import { FlowEditor } from "./components/FlowEditor";
import { ChatView } from "./components/ChatView";
import { Dashboard } from "./components/Dashboard";
import { Avatar } from "./components/avatar/Avatar";
import { ToastProvider } from "./components/ui/Toast";
import { ConfirmProvider } from "./components/ui/ConfirmDialog";

type View = "editor" | "chat" | "dashboard";

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
        : "Analyze user query behavior";

  return (
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

          {/* Main content */}
          <main className="flex-1 overflow-hidden">
            {view === "editor" ? <FlowEditor /> : view === "chat" ? <ChatView /> : <Dashboard />}
          </main>

          <HelpModal open={helpOpen} onClose={() => setHelpOpen(false)} />
        </div>
      </ConfirmProvider>
    </ToastProvider>
  );
}

const VIEW_GUIDE: { name: string; desc: string }[] = [
  { name: "Editor", desc: "拖曳節點建構 RAG pipeline,按 Run 執行" },
  { name: "Chat", desc: "用目前的 pipeline 與知識庫對話" },
  { name: "Dashboard", desc: "分析使用者查詢行為與知識缺口" },
];

const SHORTCUTS: { keys: string; desc: string }[] = [
  { keys: "Enter", desc: "Chat 送出訊息" },
  { keys: "Shift + Enter", desc: "Chat 換行" },
  { keys: "Delete / Backspace", desc: "刪除選取的節點或連線" },
  { keys: "Esc", desc: "取消輸入 / 關閉對話框" },
];

function HelpModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

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
              <h2 className="text-sm font-bold text-[#e0e0e0]">RAGLoom · 使用說明</h2>
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
                分頁
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
                鍵盤快捷鍵
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
