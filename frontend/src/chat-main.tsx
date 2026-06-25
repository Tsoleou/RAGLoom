import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MotionConfig } from "framer-motion";
import "./index.css";
import { ChatView } from "./components/ChatView";
import { ToastProvider } from "./components/ui/Toast";
import { ConfirmProvider } from "./components/ui/ConfirmDialog";

// Chat-only kiosk entry (served at "/"). No header, view switcher, or help
// modal — just the visitor-facing chat. The providers below are the same ones
// App.tsx wraps ChatView in; ChatView still uses Toast/Confirm internally, so
// they must be present even though the admin controls that trigger them are
// hidden via admin={false}.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <ConfirmProvider>
          <div className="h-screen bg-[#1a1a1a]">
            <ChatView admin={false} />
          </div>
        </ConfirmProvider>
      </ToastProvider>
    </MotionConfig>
  </StrictMode>,
);
