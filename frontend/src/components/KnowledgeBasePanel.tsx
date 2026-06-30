import { useCallback, useEffect, useRef, useState } from "react";
import { FileText, Upload, Trash2, Plus, Loader2, ShieldCheck, RefreshCw } from "lucide-react";
import { useToast } from "./ui/Toast";
import { useConfirm } from "./ui/ConfirmDialog";

interface KBDocument {
  filename: string;
  type: string;
  bytes: number;
  encrypted: boolean;
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Operator-facing knowledge-base manager: list, inject (upload or paste), and
 * remove source documents. Each write goes through the server's encryption +
 * single-file re-ingest path, so an added document is immediately answerable in
 * chat. Lives behind the UnlockGate, so the KB is guaranteed unlocked here.
 */
export function KnowledgeBasePanel() {
  const toast = useToast();
  const confirm = useConfirm();
  const [docs, setDocs] = useState<KBDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null); // filename being mutated
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteName, setPasteName] = useState("");
  const [pasteBody, setPasteBody] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/kb/documents");
      const data = await res.json();
      setDocs(data.documents || []);
    } catch {
      toast("無法載入文件清單", "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleUploadFile(file: File) {
    setBusy(file.name);
    try {
      const res = await fetch(`/api/kb/documents/${encodeURIComponent(file.name)}`, {
        method: "PUT",
        body: file,
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        toast(`已注入 ${file.name}（共 ${data.chunks} chunks）`, "success");
        await reload();
      } else {
        toast(data.detail || "上傳失敗", "error");
      }
    } catch {
      toast("上傳失敗", "error");
    } finally {
      setBusy(null);
    }
  }

  async function handlePasteSubmit() {
    const name = pasteName.trim();
    if (!name || !pasteBody.trim()) return;
    setBusy(name);
    try {
      const res = await fetch("/api/kb/documents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: name, content: pasteBody }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        toast(`已注入 ${name}（共 ${data.chunks} chunks）`, "success");
        setPasteOpen(false);
        setPasteName("");
        setPasteBody("");
        await reload();
      } else {
        toast(data.detail || "注入失敗", "error");
      }
    } catch {
      toast("注入失敗", "error");
    } finally {
      setBusy(null);
    }
  }

  async function handleDelete(filename: string) {
    const ok = await confirm({
      title: `刪除文件「${filename}」？`,
      message: "這會一併移除它在向量庫中的所有 chunk，無法復原。",
      confirmLabel: "刪除",
    });
    if (!ok) return;
    setBusy(filename);
    try {
      const res = await fetch(`/api/kb/documents/${encodeURIComponent(filename)}`, {
        method: "DELETE",
      });
      if (res.ok) {
        toast(`已刪除 ${filename}`, "success");
        await reload();
      } else {
        const data = await res.json().catch(() => ({}));
        toast(data.detail || "刪除失敗", "error");
      }
    } catch {
      toast("刪除失敗", "error");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col gap-4 overflow-y-auto p-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-bold text-[#e0e0e0]">知識庫文件</h2>
          <p className="text-xs text-[#777]">注入的文件會加密儲存並即時索引進對話</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={reload}
            title="重新整理"
            className="flex h-8 w-8 items-center justify-center rounded-md border border-[#333] bg-[#252525] text-[#888] hover:text-[#00ccaa]"
          >
            <RefreshCw size={14} />
          </button>
          <button
            onClick={() => setPasteOpen((v) => !v)}
            className="flex items-center gap-1.5 rounded-md border border-[#333] bg-[#252525] px-3 py-1.5 text-xs text-[#ccc] hover:bg-[#2a2a2a]"
          >
            <Plus size={14} /> 貼上文字
          </button>
          <button
            onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-1.5 rounded-md bg-[#e07830] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#e08a48]"
          >
            <Upload size={14} /> 上傳檔案
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt,.md,.csv,.pdf"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleUploadFile(f);
              e.target.value = "";
            }}
          />
        </div>
      </div>

      {pasteOpen && (
        <div className="rounded-lg border border-[#333] bg-[#202020] p-4">
          <input
            value={pasteName}
            onChange={(e) => setPasteName(e.target.value)}
            placeholder="檔名（例如 product_new.txt）"
            className="mb-2 w-full rounded-md border border-[#333] bg-[#1a1a1a] px-3 py-2 text-sm text-[#e0e0e0] outline-none focus:border-[#00ccaa]/60"
          />
          <textarea
            value={pasteBody}
            onChange={(e) => setPasteBody(e.target.value)}
            placeholder="貼上文件內容…（.txt / .md / .csv）"
            rows={6}
            className="mb-2 w-full resize-y rounded-md border border-[#333] bg-[#1a1a1a] px-3 py-2 text-sm text-[#e0e0e0] outline-none focus:border-[#00ccaa]/60"
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setPasteOpen(false)}
              className="rounded-md border border-[#333] px-3 py-1.5 text-xs text-[#999] hover:bg-[#2a2a2a]"
            >
              取消
            </button>
            <button
              onClick={handlePasteSubmit}
              disabled={!pasteName.trim() || !pasteBody.trim() || busy !== null}
              className="flex items-center gap-1.5 rounded-md bg-[#e07830] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#e08a48] disabled:opacity-50"
            >
              {busy ? <Loader2 className="animate-spin" size={13} /> : <Plus size={13} />}
              注入
            </button>
          </div>
        </div>
      )}

      <div className="rounded-lg border border-[#2a2a2a] bg-[#1d1d1d]">
        {loading ? (
          <div className="flex items-center justify-center gap-2 p-8 text-sm text-[#777]">
            <Loader2 className="animate-spin" size={16} /> 載入中…
          </div>
        ) : docs.length === 0 ? (
          <div className="p-8 text-center text-sm text-[#666]">尚無文件，請上傳或貼上。</div>
        ) : (
          <ul className="divide-y divide-[#2a2a2a]">
            {docs.map((d) => (
              <li key={d.filename} className="flex items-center gap-3 px-4 py-2.5">
                <FileText size={15} className="flex-shrink-0 text-[#00ccaa]/70" />
                <span className="flex-1 truncate text-sm text-[#ddd]" title={d.filename}>
                  {d.filename}
                </span>
                {d.encrypted && (
                  <span
                    title="已加密"
                    className="flex items-center gap-1 rounded bg-[#00ccaa]/10 px-1.5 py-0.5 text-[10px] text-[#00ccaa]"
                  >
                    <ShieldCheck size={11} /> 加密
                  </span>
                )}
                <span className="w-16 text-right text-[11px] text-[#666]">{fmtBytes(d.bytes)}</span>
                <button
                  onClick={() => handleDelete(d.filename)}
                  disabled={busy === d.filename}
                  title="刪除"
                  className="flex h-7 w-7 items-center justify-center rounded text-[#666] hover:bg-[#2a2a2a] hover:text-[#e0664a] disabled:opacity-40"
                >
                  {busy === d.filename ? (
                    <Loader2 className="animate-spin" size={13} />
                  ) : (
                    <Trash2 size={13} />
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
