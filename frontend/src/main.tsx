import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App, { AvatarPreview } from "./App.tsx";

const isAvatarPreview =
  new URLSearchParams(window.location.search).get("preview") === "avatar";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {isAvatarPreview ? <AvatarPreview /> : <App />}
  </StrictMode>,
);
