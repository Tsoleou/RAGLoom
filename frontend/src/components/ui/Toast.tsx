import { createContext, useContext, useState, useCallback, useRef } from "react";
import type { ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { CheckCircle2, AlertTriangle, XCircle, Info, X } from "lucide-react";

export type ToastVariant = "success" | "error" | "warning" | "info";

interface ToastItem {
  id: number;
  message: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  /** Push a transient toast. `durationMs <= 0` keeps it until dismissed. */
  toast: (message: string, variant?: ToastVariant, durationMs?: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

// Sticks to the existing palette: cyan for success/info, red for error,
// amber for warning — same hexes used across the editor and chat.
const VARIANT_STYLE: Record<
  ToastVariant,
  { accent: string; text: string; icon: ReactNode }
> = {
  success: { accent: "#00ccaa", text: "#9be8da", icon: <CheckCircle2 size={15} /> },
  error: { accent: "#ff5566", text: "#ffb3bd", icon: <XCircle size={15} /> },
  warning: { accent: "#f0a040", text: "#f5cd96", icon: <AlertTriangle size={15} /> },
  info: { accent: "#00ccaa", text: "#9be8da", icon: <Info size={15} /> },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const idRef = useRef(0);

  const remove = useCallback((id: number) => {
    setToasts((ts) => ts.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (message: string, variant: ToastVariant = "info", durationMs = 4000) => {
      const id = ++idRef.current;
      setToasts((ts) => [...ts, { id, message, variant }]);
      if (durationMs > 0) {
        setTimeout(() => remove(id), durationMs);
      }
    },
    [remove],
  );

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="fixed bottom-4 right-4 z-[1000] flex flex-col items-end gap-2 pointer-events-none">
        <AnimatePresence initial={false}>
          {toasts.map((t) => {
            const s = VARIANT_STYLE[t.variant];
            return (
              <motion.div
                key={t.id}
                layout
                role="status"
                initial={{ opacity: 0, x: 40, scale: 0.96 }}
                animate={{ opacity: 1, x: 0, scale: 1 }}
                exit={{ opacity: 0, x: 40, scale: 0.96 }}
                transition={{ duration: 0.2, ease: "easeOut" }}
                className="pointer-events-auto flex max-w-sm items-start gap-2.5 rounded-lg border bg-[#161616] px-3.5 py-2.5 font-mono text-xs shadow-2xl"
                style={{
                  borderColor: `${s.accent}66`,
                  color: s.text,
                  boxShadow: `0 0 18px ${s.accent}22`,
                }}
              >
                <span className="mt-0.5 flex-shrink-0" style={{ color: s.accent }}>
                  {s.icon}
                </span>
                <span className="flex-1 whitespace-pre-wrap break-words leading-snug">
                  {t.message}
                </span>
                <button
                  onClick={() => remove(t.id)}
                  aria-label="Dismiss"
                  className="mt-0.5 flex-shrink-0 text-[#555] transition-colors hover:text-[#aaa]"
                >
                  <X size={13} />
                </button>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast(): ToastContextValue["toast"] {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within a ToastProvider");
  return ctx.toast;
}
