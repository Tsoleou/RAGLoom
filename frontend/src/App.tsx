import { useState } from "react";
import { FlowEditor } from "./components/FlowEditor";
import { ChatView } from "./components/ChatView";

type View = "editor" | "chat";

function App() {
  const [view, setView] = useState<View>("editor");

  const hint =
    view === "editor"
      ? "Drag nodes to build pipeline / click Run to execute"
      : "Chat with the RAG pipeline";

  return (
    <div className="h-screen flex flex-col bg-[#1a1a1a]">
      {/* Title bar */}
      <header className="px-6 py-3 bg-[#202020] border-b border-[#2a2a2a] flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold text-[#e0e0e0]">
            RAGLoom
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
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">
        {view === "editor" ? <FlowEditor /> : <ChatView />}
      </main>
    </div>
  );
}

export default App;
