import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronUp, ChevronDown, Download } from "lucide-react";
import { useToast } from "./ui/Toast";
import { useFocusTrap } from "../hooks/useFocusTrap";

interface SerializedGraph {
  nodes: Array<{
    id: string;
    type: string;
    position: { x: number; y: number };
    params: Record<string, unknown>;
  }>;
  edges: Array<{
    source: string;
    target: string;
    sourceHandle: string;
    targetHandle: string;
  }>;
}

interface CaseInfo {
  id: string;
  category: string;
}

interface MetricEntry {
  mean: number | null;
  n: number;
}

interface BatchResponse {
  per_case: Array<{
    case_id: string;
    category: string;
    metrics: Record<string, { score: number | null; details: Record<string, unknown> } | null>;
  }>;
  aggregate: {
    macro: Record<string, MetricEntry>;
    per_category: Record<string, Record<string, MetricEntry>>;
    worst_k: Array<{
      case_id: string;
      category: string;
      composite_score: number | null;
      missing_metrics: string[];
    }>;
    total_cases: number;
  };
  skipped: Array<{ case_id: string; reason: string }>;
}

interface Props {
  open: boolean;
  graph: SerializedGraph;
  onClose: () => void;
}

const METRIC_LABELS: Record<string, string> = {
  coverage: "Coverage",
  score_distribution: "Score Dist.",
  diversity: "Diversity",
  facts_coverage: "Facts",
};
const METRIC_ORDER = ["coverage", "score_distribution", "diversity", "facts_coverage"];

function fmtScore(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  return s.toFixed(3);
}

function scoreColor(s: number | null | undefined): string {
  if (s === null || s === undefined) return "text-[#666]";
  if (s >= 0.8) return "text-[#60c080]";
  if (s >= 0.5) return "text-[#d0c060]";
  return "text-[#e07060]";
}

type SortKey = "case_id" | "category" | string;
type SortDir = "asc" | "desc";

export function BatchEvalModal({ open, graph, onClose }: Props) {
  const toast = useToast();
  const [cases, setCases] = useState<CaseInfo[]>([]);
  const [mode, setMode] = useState<"all" | "category" | "ids">("all");
  const [category, setCategory] = useState<string>("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [worstK, setWorstK] = useState(3);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<BatchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [sortKey, setSortKey] = useState<SortKey>("case_id");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const startedRef = useRef(0);
  const dialogRef = useRef<HTMLDivElement>(null);

  // Esc + focus trap + focus restore for the dialog.
  useFocusTrap(open, dialogRef, () => handleClose());

  useEffect(() => {
    if (!open) return;
    fetch("/api/eval/cases")
      .then((r) => r.json())
      .then((data: CaseInfo[]) => {
        setCases(data);
        if (data.length > 0 && !category) setCategory(data[0].category);
      })
      .catch((e) => {
        const msg = `Failed to load eval cases: ${e}`;
        toast(msg, "error");
        setError(msg);
      });
  }, [open, category, toast]);

  // Tick an elapsed-seconds counter while a batch is running. No real progress
  // signal exists (single blocking request), so we report honest elapsed time.
  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => {
      setElapsed(Math.round((performance.now() - startedRef.current) / 1000));
    }, 1000);
    return () => window.clearInterval(id);
  }, [running]);

  const categories = useMemo(() => {
    const set = new Set(cases.map((c) => c.category));
    return Array.from(set);
  }, [cases]);

  const selectedCount = useMemo(() => {
    if (mode === "all") return cases.length;
    if (mode === "category") return cases.filter((c) => c.category === category).length;
    return selectedIds.size;
  }, [mode, cases, category, selectedIds]);

  const sortedCases = useMemo(() => {
    if (!result) return [];
    const rows = [...result.per_case];
    const dir = sortDir === "asc" ? 1 : -1;
    rows.sort((a, b) => {
      if (sortKey === "case_id") return a.case_id.localeCompare(b.case_id) * dir;
      if (sortKey === "category") return a.category.localeCompare(b.category) * dir;
      // Metric column: numeric, with null/undefined scores always sorted last.
      const av = a.metrics[sortKey]?.score;
      const bv = b.metrics[sortKey]?.score;
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      return (av - bv) * dir;
    });
    return rows;
  }, [result, sortKey, sortDir]);

  const ariaSort = (key: SortKey): "ascending" | "descending" | "none" =>
    sortKey === key ? (sortDir === "asc" ? "ascending" : "descending") : "none";

  async function handleRun() {
    setRunning(true);
    setResult(null);
    setError(null);
    setElapsed(0);
    startedRef.current = performance.now();
    try {
      const scope =
        mode === "all"
          ? { mode: "all" }
          : mode === "category"
          ? { mode: "category", category }
          : { mode: "ids", case_ids: Array.from(selectedIds) };

      const res = await fetch("/api/eval/batch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph, scope, worst_k: worstK }),
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(`HTTP ${res.status}: ${detail}`);
      }
      const data = (await res.json()) as BatchResponse;
      setResult(data);
    } catch (e) {
      // Batch runs are long and the user may have looked away — surface the
      // failure both as an immediate toast and as a persistent block in the
      // results area, so it's still there when they return.
      const msg = `Batch eval failed: ${e}`;
      toast(msg, "error");
      setError(msg);
    } finally {
      setRunning(false);
    }
  }

  function handleClose() {
    setResult(null);
    setError(null);
    onClose();
  }

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  function exportCsv() {
    if (!result) return;
    const header = ["case_id", "category", ...METRIC_ORDER];
    const rows = result.per_case.map((r) => [
      r.case_id,
      r.category,
      ...METRIC_ORDER.map((m) => {
        const s = r.metrics[m]?.score;
        return s === null || s === undefined ? "" : String(s);
      }),
    ]);
    const escape = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
    const csv = [header, ...rows].map((row) => row.map(escape).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "batch_eval.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[100] bg-black/60 flex items-center justify-center p-6"
      onClick={handleClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Batch Retrieval Eval"
        className="bg-[#1a1a1a] border border-[#a070d0]/40 rounded-lg shadow-2xl max-w-5xl w-full max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-[#2a2a2a]">
          <h2 className="text-sm font-bold text-[#d0d0d0]">
            Batch Retrieval Eval
            <span className="text-[10px] text-[#888] font-normal ml-2">
              ({graph.nodes.length} nodes / {graph.edges.length} edges)
            </span>
          </h2>
          <button
            onClick={handleClose}
            aria-label="Close"
            className="text-[#888] hover:text-[#ccc] text-lg leading-none"
          >
            ×
          </button>
        </div>

        {/* Scope picker */}
        <div className="px-5 py-3 border-b border-[#2a2a2a] flex flex-wrap items-center gap-4 text-xs">
          <label className="flex items-center gap-2 text-[#bbb]">
            <input
              type="radio"
              checked={mode === "all"}
              onChange={() => setMode("all")}
              className="accent-[#a070d0]"
            />
            All ({cases.length})
          </label>
          <label className="flex items-center gap-2 text-[#bbb]">
            <input
              type="radio"
              checked={mode === "category"}
              onChange={() => setMode("category")}
              className="accent-[#a070d0]"
            />
            By category
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              disabled={mode !== "category"}
              className="bg-[#252525] border border-[#333] text-[#d0d0d0] text-xs px-2 py-1 rounded disabled:opacity-40"
            >
              {categories.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-[#bbb]">
            <input
              type="radio"
              checked={mode === "ids"}
              onChange={() => setMode("ids")}
              className="accent-[#a070d0]"
            />
            By IDs ({selectedIds.size})
          </label>
          <div className="ml-auto flex items-center gap-2 text-[#888]">
            <label>worst-K:</label>
            <input
              type="number"
              min={1}
              max={20}
              value={worstK}
              onChange={(e) => setWorstK(Math.max(1, Number(e.target.value) || 3))}
              className="bg-[#252525] border border-[#333] text-[#d0d0d0] text-xs px-2 py-1 rounded w-14"
            />
            <button
              onClick={handleRun}
              disabled={running || selectedCount === 0}
              className="px-3 py-1.5 rounded-md bg-[#a070d0] text-white font-medium hover:bg-[#8050b0] disabled:bg-[#333] disabled:text-[#555] disabled:cursor-not-allowed transition-colors"
            >
              {running ? "Running..." : `Run on ${selectedCount} case${selectedCount === 1 ? "" : "s"}`}
            </button>
          </div>
        </div>

        {/* ID picker (only when mode=ids) */}
        {mode === "ids" && (
          <div className="px-5 py-2 border-b border-[#2a2a2a] max-h-32 overflow-y-auto text-xs">
            <div className="grid grid-cols-2 gap-x-4">
              {cases.map((c) => (
                <label key={c.id} className="flex items-center gap-2 text-[#bbb] py-0.5">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(c.id)}
                    onChange={(e) => {
                      const next = new Set(selectedIds);
                      if (e.target.checked) next.add(c.id);
                      else next.delete(c.id);
                      setSelectedIds(next);
                    }}
                    className="accent-[#a070d0]"
                  />
                  <span className="text-[#d0d0d0]">{c.id}</span>
                  <span className="text-[#888] text-[10px]">{c.category}</span>
                </label>
              ))}
            </div>
          </div>
        )}

        {/* Results */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5 text-xs">
          {!result && !running && !error && (
            <div className="text-[#888] text-center py-10">
              Pick a scope and click Run to evaluate the current graph against the golden set.
            </div>
          )}

          {error && !running && (
            <div
              role="alert"
              className="rounded border border-[#ff5566]/40 bg-[#2a1618] px-4 py-3 text-[#ffb3bd]"
            >
              <div className="font-semibold text-[#ff8d9b]">Run failed</div>
              <div className="mt-1 whitespace-pre-wrap break-words leading-relaxed">{error}</div>
              <div className="mt-1 text-[10px] text-[#c98a92]">Adjust the range and run again.</div>
            </div>
          )}

          {running && (
            <div className="py-10 flex flex-col items-center gap-3">
              <div className="text-[#a070d0]">
                Running graph for {selectedCount} cases… {elapsed}s elapsed
              </div>
              {/* Indeterminate bar — honest "working" signal, no fake percentage. */}
              <div className="w-64 h-1 rounded-full bg-[#2a2a2a] overflow-hidden">
                <div className="h-full w-1/3 rounded-full bg-[#a070d0] batch-indeterminate" />
              </div>
            </div>
          )}

          {result && (
            <>
              <div className="flex justify-end">
                <button
                  onClick={exportCsv}
                  className="flex items-center gap-1.5 rounded border border-[#a070d0]/40 px-2.5 py-1 text-[11px] text-[#c0a0e0] transition-colors hover:bg-[#a070d0]/10"
                >
                  <Download size={12} />
                  Export CSV
                </button>
              </div>

              {/* Macro averages */}
              <section>
                <h3 className="text-[#aaa] font-semibold mb-2">
                  Macro Averages
                  <span className="text-[10px] text-[#888] ml-2 font-normal">
                    across {result.aggregate.total_cases} cases
                    {result.skipped.length > 0 && ` (${result.skipped.length} skipped)`}
                  </span>
                </h3>
                <div className="grid grid-cols-4 gap-3">
                  {METRIC_ORDER.map((m) => {
                    const v = result.aggregate.macro[m];
                    return (
                      <div key={m} className="bg-[#252525] border border-[#333] rounded p-3">
                        <div className="text-[#888] text-[10px] uppercase tracking-wide">
                          {METRIC_LABELS[m]}
                        </div>
                        <div className={`text-xl font-mono mt-1 ${scoreColor(v?.mean)}`}>
                          {fmtScore(v?.mean)}
                        </div>
                        <div className="text-[10px] text-[#666] mt-0.5">n = {v?.n ?? 0}</div>
                      </div>
                    );
                  })}
                </div>
              </section>

              {/* Per-category */}
              <section>
                <h3 className="text-[#aaa] font-semibold mb-2">Per Category</h3>
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-[11px]">
                    <caption className="sr-only">Average metric scores per category</caption>
                    <thead>
                      <tr className="border-b border-[#333] text-[#888]">
                        <th scope="col" className="text-left py-1.5 pr-3 font-medium">Category</th>
                        {METRIC_ORDER.map((m) => (
                          <th scope="col" key={m} className="text-right py-1.5 px-2 font-medium">
                            {METRIC_LABELS[m]}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(result.aggregate.per_category).map(([cat, metrics]) => (
                        <tr key={cat} className="border-b border-[#2a2a2a]">
                          <td className="py-1.5 pr-3 text-[#d0d0d0]">{cat}</td>
                          {METRIC_ORDER.map((m) => {
                            const v = metrics[m];
                            return (
                              <td
                                key={m}
                                className={`text-right py-1.5 px-2 font-mono ${scoreColor(v?.mean)}`}
                              >
                                {fmtScore(v?.mean)}
                                <span className="text-[9px] text-[#555] ml-1">
                                  ({v?.n ?? 0})
                                </span>
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              {/* Worst-K */}
              {result.aggregate.worst_k.length > 0 && (
                <section>
                  <h3 className="text-[#aaa] font-semibold mb-2">
                    Worst {result.aggregate.worst_k.length} cases
                  </h3>
                  <div className="space-y-1">
                    {result.aggregate.worst_k.map((w) => (
                      <div
                        key={w.case_id}
                        className="flex items-center gap-3 bg-[#252525] border border-[#3a2020] rounded px-3 py-2"
                      >
                        <span
                          className={`font-mono font-medium ${scoreColor(w.composite_score)} w-16`}
                        >
                          {fmtScore(w.composite_score)}
                        </span>
                        <span className="text-[#d0d0d0]">{w.case_id}</span>
                        <span className="text-[10px] text-[#999]">{w.category}</span>
                        {w.missing_metrics.length > 0 && (
                          <span className="text-[10px] text-[#999] ml-auto">
                            missing: {w.missing_metrics.join(", ")}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Per-case table */}
              <section>
                <h3 className="text-[#aaa] font-semibold mb-2">
                  Per Case ({result.per_case.length})
                  <span className="text-[10px] text-[#888] ml-2 font-normal">Click a column header to sort</span>
                </h3>
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-[11px]">
                    <caption className="sr-only">Metric scores per eval case; click a column header to sort</caption>
                    <thead>
                      <tr className="border-b border-[#333] text-[#888] sticky top-0 bg-[#1a1a1a]">
                        <SortableTh label="Case ID" sortKey="case_id" active={sortKey} dir={sortDir} ariaSort={ariaSort("case_id")} onSort={toggleSort} align="left" />
                        <SortableTh label="Category" sortKey="category" active={sortKey} dir={sortDir} ariaSort={ariaSort("category")} onSort={toggleSort} align="left" />
                        {METRIC_ORDER.map((m) => (
                          <SortableTh key={m} label={METRIC_LABELS[m]} sortKey={m} active={sortKey} dir={sortDir} ariaSort={ariaSort(m)} onSort={toggleSort} align="right" />
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedCases.map((row) => (
                        <tr key={row.case_id} className="border-b border-[#2a2a2a]">
                          <td className="py-1.5 pr-3 text-[#d0d0d0]">{row.case_id}</td>
                          <td className="py-1.5 pr-3 text-[#888]">{row.category}</td>
                          {METRIC_ORDER.map((m) => {
                            const v = row.metrics[m];
                            const s = v?.score;
                            return (
                              <td
                                key={m}
                                className={`text-right py-1.5 px-2 font-mono ${scoreColor(s)}`}
                              >
                                {fmtScore(s)}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              {result.skipped.length > 0 && (
                <section>
                  <h3 className="text-[#aaa] font-semibold mb-2">Skipped</h3>
                  <div className="space-y-1">
                    {result.skipped.map((s) => (
                      <div key={s.case_id} className="text-[#888]">
                        <span className="text-[#d0a060]">{s.case_id}</span>: {s.reason}
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function SortableTh({
  label,
  sortKey,
  active,
  dir,
  ariaSort,
  onSort,
  align,
}: {
  label: string;
  sortKey: SortKey;
  active: SortKey;
  dir: SortDir;
  ariaSort: "ascending" | "descending" | "none";
  onSort: (key: SortKey) => void;
  align: "left" | "right";
}) {
  const isActive = active === sortKey;
  return (
    <th
      scope="col"
      aria-sort={ariaSort}
      className={`py-1.5 px-2 font-medium ${align === "left" ? "text-left pr-3" : "text-right"}`}
    >
      <button
        onClick={() => onSort(sortKey)}
        className={`inline-flex items-center gap-1 hover:text-[#c0a0e0] transition-colors ${
          align === "right" ? "flex-row-reverse" : ""
        } ${isActive ? "text-[#c0a0e0]" : ""}`}
      >
        {label}
        {isActive &&
          (dir === "asc" ? <ChevronUp size={11} /> : <ChevronDown size={11} />)}
      </button>
    </th>
  );
}
