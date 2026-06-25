import { createContext, useContext, useState, useCallback, useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { AlertTriangle } from "lucide-react";
import { useFocusTrap } from "../../hooks/useFocusTrap";

interface ConfirmOptions {
  title: string;
  message?: string;
  /** Label for the confirming action. Defaults to "確認". */
  confirmLabel?: string;
  cancelLabel?: string;
  /** Red confirm button for destructive actions (default). */
  danger?: boolean;
}

interface ConfirmContextValue {
  /** Opens a modal and resolves true (confirm) / false (cancel/dismiss). */
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
}

const ConfirmContext = createContext<ConfirmContextValue | null>(null);

interface PendingConfirm extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const pendingRef = useRef<PendingConfirm | null>(null);
  useEffect(() => {
    pendingRef.current = pending;
  }, [pending]);

  const close = useCallback((ok: boolean) => {
    pendingRef.current?.resolve(ok);
    setPending(null);
  }, []);

  const confirm = useCallback(
    (opts: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        // A still-open confirm being replaced by a new one would otherwise leave
        // its awaiter hanging forever — resolve it as cancelled first.
        pendingRef.current?.resolve(false);
        setPending({ ...opts, resolve });
      }),
    [],
  );

  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(!!pending, dialogRef, () => close(false));

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      <AnimatePresence>
        {pending && (
          <motion.div
            className="fixed inset-0 z-[1100] flex items-center justify-center bg-black/60 p-4"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={() => close(false)}
          >
            <motion.div
              ref={dialogRef}
              role="alertdialog"
              aria-modal="true"
              className="w-full max-w-sm rounded-lg border border-[#f0a040]/40 bg-[#1a1a1a] p-5 shadow-2xl"
              initial={{ opacity: 0, scale: 0.96, y: 8 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 8 }}
              transition={{ duration: 0.15 }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="flex items-start gap-3">
                <span
                  className="mt-0.5 flex-shrink-0"
                  style={{ color: pending.danger === false ? "#00ccaa" : "#f0a040" }}
                >
                  <AlertTriangle size={18} />
                </span>
                <div className="flex-1">
                  <h2 className="text-sm font-bold text-[#e0e0e0]">{pending.title}</h2>
                  {pending.message && (
                    <p className="mt-1.5 text-xs leading-relaxed text-[#999]">
                      {pending.message}
                    </p>
                  )}
                </div>
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <button
                  onClick={() => close(false)}
                  className="rounded border border-[#333] bg-[#252525] px-3 py-1.5 text-xs text-[#aaa] transition-colors hover:bg-[#2a2a2a]"
                >
                  {pending.cancelLabel ?? "取消"}
                </button>
                <button
                  data-autofocus
                  onClick={() => close(true)}
                  className={`rounded px-3 py-1.5 text-xs font-medium text-white transition-colors ${
                    pending.danger === false
                      ? "bg-[#0a8f78] hover:bg-[#0aa88e]"
                      : "bg-[#c0443c] hover:bg-[#d65049]"
                  }`}
                >
                  {pending.confirmLabel ?? "確認"}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </ConfirmContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useConfirm(): ConfirmContextValue["confirm"] {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used within a ConfirmProvider");
  return ctx.confirm;
}
