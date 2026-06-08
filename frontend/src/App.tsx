import { useState } from "react";
import { FlowEditor } from "./components/FlowEditor";
import { ChatView } from "./components/ChatView";
import { Dashboard } from "./components/Dashboard";
import { Avatar } from "./components/avatar/Avatar";

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

  const hint =
    view === "editor"
      ? "Drag nodes to build pipeline / click Run to execute"
      : view === "chat"
        ? "Chat with the RAG pipeline"
        : "Analyze user query behavior";

  return (
    <div className="h-screen flex flex-col bg-[#1a1a1a]">
      {/* Title bar */}
      <header className="px-6 py-3 bg-[#202020] border-b border-[#2a2a2a] flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold text-[#e0e0e0]">
            <span style={{ textShadow: "0 0 18px rgba(0,204,170,0.5)" }}>RAGLoom</span>
          </h1>
          <span className="text-xs text-[#666]">{hint}</span>
        </div>

        {/* View switcher */}
        <div className="flex rounded-md border border-[#333] overflow-hidden text-xs">
          <button
            onClick={() => setView("editor")}
            className={`px-3 py-1.5 transition-colors ${view === "editor" ? "bg-[#e07830] text-white" : "bg-[#252525] text-[#888] hover:bg-[#2a2a2a]"}`}
          >
            Editor
          </button>
          <button
            onClick={() => setView("chat")}
            className={`px-3 py-1.5 transition-colors ${view === "chat" ? "bg-[#e07830] text-white" : "bg-[#252525] text-[#888] hover:bg-[#2a2a2a]"}`}
          >
            Chat
          </button>
          <button
            onClick={() => setView("dashboard")}
            className={`px-3 py-1.5 transition-colors ${view === "dashboard" ? "bg-[#e07830] text-white" : "bg-[#252525] text-[#888] hover:bg-[#2a2a2a]"}`}
          >
            Dashboard
          </button>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">
        {view === "editor" ? <FlowEditor /> : view === "chat" ? <ChatView /> : <Dashboard />}
      </main>
    </div>
  );
}

export default App;
