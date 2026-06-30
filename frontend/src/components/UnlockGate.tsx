import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Lock, Loader2 } from "lucide-react";

type Phase = "checking" | "locked" | "open" | "error";

/**
 * Gates the admin app behind KB encryption unlock.
 *
 * On mount it asks GET /api/kb/status. When encryption is disabled, or already
 * unlocked, children render immediately (no behavior change for plaintext
 * deployments). When enabled-but-locked, it shows a passphrase prompt; a
 * successful POST /api/kb/unlock derives the key server-side (and brings chat
 * online), after which the app is revealed.
 */
export function UnlockGate({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("checking");
  const [passphrase, setPassphrase] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/kb/status")
      .then((r) => r.json())
      .then((s: { enabled: boolean; unlocked: boolean }) => {
        if (cancelled) return;
        setPhase(!s.enabled || s.unlocked ? "open" : "locked");
      })
      .catch(() => !cancelled && setPhase("open")); // fail open: don't brick admin if status errors
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleUnlock(e: FormEvent) {
    e.preventDefault();
    if (!passphrase || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/api/kb/unlock", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ passphrase }),
      });
      if (res.ok) {
        setPassphrase("");
        setPhase("open");
      } else {
        const data = await res.json().catch(() => ({}));
        setError(res.status === 401 ? "密碼錯誤" : data.detail || "解鎖失敗");
      }
    } catch {
      setError("無法連線到伺服器");
    } finally {
      setSubmitting(false);
    }
  }

  if (phase === "open") return <>{children}</>;

  if (phase === "checking") {
    return (
      <div className="flex h-full items-center justify-center bg-[#1a1a1a] text-[#888]">
        <Loader2 className="animate-spin" size={20} />
      </div>
    );
  }

  return (
    <div className="flex h-full items-center justify-center bg-[#1a1a1a] p-4">
      <form
        onSubmit={handleUnlock}
        className="w-full max-w-sm rounded-lg border border-[#00ccaa]/30 bg-[#202020] p-6 shadow-2xl"
      >
        <div className="mb-4 flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-[#00ccaa]/10 text-[#00ccaa]">
            <Lock size={18} />
          </div>
          <div>
            <h2 className="text-sm font-bold text-[#e0e0e0]">知識庫已加密鎖定</h2>
            <p className="text-xs text-[#777]">輸入操作者密碼以解鎖並啟用對話</p>
          </div>
        </div>

        <input
          type="password"
          autoFocus
          value={passphrase}
          onChange={(e) => setPassphrase(e.target.value)}
          placeholder="操作者密碼"
          className="mb-3 w-full rounded-md border border-[#333] bg-[#1a1a1a] px-3 py-2 text-sm text-[#e0e0e0] outline-none focus:border-[#00ccaa]/60"
        />

        {error && <p className="mb-3 text-xs text-[#e0664a]">{error}</p>}

        <button
          type="submit"
          disabled={submitting || !passphrase}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-[#e07830] py-2 text-sm font-medium text-white transition-colors hover:bg-[#e08a48] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? <Loader2 className="animate-spin" size={15} /> : <Lock size={15} />}
          解鎖
        </button>

        <p className="mt-4 text-[10px] leading-relaxed text-[#555]">
          密碼用來在記憶體中派生加密金鑰，不會寫入磁碟。每次伺服器重啟都需重新解鎖。
        </p>
      </form>
    </div>
  );
}
