import { useState, useEffect, useCallback } from "react";
import { RefreshCw, AlertTriangle, ShieldOff, Clock, MessageSquare, Download, Database, Search, Terminal, Play } from "lucide-react";

// ── Types (mirror api/routers/dashboard.py responses) ──────────────
type Bucket = { key: string; count: number };
type ProductBucket = { key: string; product_id: string; count: number };
type Question = { query: string; count: number };
type Gap = { query: string; count: number; avg_top_score: number };
type Stats = {
  days: number;
  total: number;
  blocked: number;
  errors: number;
  blocked_rate: number;
  avg_latency_ms: number | null;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  avg_top_score: number | null;
  by_intent: Bucket[];
  by_gate: Bucket[];
  by_status: Bucket[];
  top_sources: Bucket[];
  top_products: ProductBucket[];
  most_mentioned: ProductBucket[];
  top_questions: Question[];
  volume: { day: string; count: number }[];
  knowledge_gaps: Gap[];
};
type QueryRow = {
  id: number; ts: string; query: string; profile: string; model: string;
  latency_ms: number | null; status: string; blocked: number;
  blocked_reason: string | null; gate: string | null; intent: string;
  product: string | null; top_score: number | null; n_retrieved: number; n_passed: number;
  answer?: string; critic_verdict?: string | null; critic_reason?: string | null;
};

const RANGES = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "All", days: 0 },
];

// Read-only SQL console (self-diagnosis). Runs against a decrypted in-memory
// snapshot server-side — table `queries`, these columns.
type SqlResult = { columns: string[]; rows: unknown[][]; row_count: number; truncated: boolean };
const SQL_COLUMNS = "id, ts, query, answer, profile, model, intent, product, status, blocked, blocked_reason, gate, top_score, n_retrieved, n_passed, top_source, rerank_kept, rerank_total, critic_verdict, critic_reason, latency_ms";
const SQL_EXAMPLES: { label: string; sql: string }[] = [
  { label: "Errors", sql: "SELECT ts, query, blocked_reason\nFROM queries WHERE status='error'\nORDER BY id DESC" },
  { label: "Knowledge gaps", sql: "SELECT query, top_score, answer\nFROM queries\nWHERE blocked=0 AND top_score < 0.45\nORDER BY top_score" },
  { label: "Blocked", sql: "SELECT ts, query, gate, blocked_reason\nFROM queries WHERE blocked=1\nORDER BY id DESC" },
  { label: "Slowest", sql: "SELECT query, latency_ms, model\nFROM queries WHERE latency_ms IS NOT NULL\nORDER BY latency_ms DESC LIMIT 20" },
  { label: "By intent", sql: "SELECT intent, COUNT(*) n, ROUND(AVG(top_score),3) avg_top\nFROM queries GROUP BY intent ORDER BY n DESC" },
  { label: "Critic revised", sql: "SELECT query, critic_verdict, critic_reason\nFROM queries WHERE critic_verdict IS NOT NULL\nORDER BY id DESC" },
];
const SQL_DEFAULT = "SELECT ts, query, status, top_score, latency_ms\nFROM queries\nORDER BY id DESC\nLIMIT 50";

// Stable colors per intent so the eye can track them across renders.
const INTENT_COLOR: Record<string, string> = {
  spec: "#00ccaa", price: "#f0c070", comparison: "#7aa2f7",
  availability: "#9ece6a", greeting: "#888", off_topic: "#ff5566",
  blocked_other: "#ff8855", other: "#666",
};

function Card({ children }: { children: React.ReactNode }) {
  return <div className="bg-[#141414] border border-[#2a2a2a] rounded-lg p-4">{children}</div>;
}

function Stat({ icon, label, value, accent }: { icon?: React.ReactNode; label: string; value: string; accent?: string }) {
  return (
    <Card>
      <div className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-[#666] mb-1">
        {icon}{label}
      </div>
      <div className="text-2xl font-bold" style={{ color: accent || "#e0e0e0" }}>{value}</div>
    </Card>
  );
}

// Horizontal bar list — hand-rolled, no chart lib.
function BarList({ title, data, colorFor }: { title: string; data: Bucket[]; colorFor?: (k: string) => string }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  return (
    <Card>
      <div className="text-[10px] uppercase tracking-widest text-[#666] mb-3">{title}</div>
      {data.length === 0 ? (
        <div className="text-xs text-[#444]">no data</div>
      ) : (
        <div className="flex flex-col gap-2">
          {data.map((d) => (
            <div key={d.key} className="flex items-center gap-2 text-xs">
              <div className="w-28 truncate text-[#aaa]" title={d.key}>{d.key}</div>
              <div className="flex-1 h-4 bg-[#1a1a1a] rounded overflow-hidden">
                <div
                  className="h-full rounded transition-all"
                  style={{ width: `${(d.count / max) * 100}%`, background: colorFor?.(d.key) || "#00ccaa" }}
                />
              </div>
              <div className="w-8 text-right text-[#888] tabular-nums">{d.count}</div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// Daily volume mini bar chart.
function VolumeChart({ data }: { data: { day: string; count: number }[] }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  return (
    <Card>
      <div className="text-[10px] uppercase tracking-widest text-[#666] mb-3">Query volume / day</div>
      {data.length === 0 ? (
        <div className="text-xs text-[#444]">no data</div>
      ) : (
        <div className="flex items-end gap-1 h-32">
          {data.map((d) => (
            <div key={d.day} className="flex-1 flex flex-col items-center justify-end gap-1 group" title={`${d.day}: ${d.count}`}>
              <div className="text-[9px] text-[#666] opacity-0 group-hover:opacity-100">{d.count}</div>
              <div className="w-full rounded-t bg-[#00ccaa]/60 group-hover:bg-[#00ccaa] transition-colors"
                   style={{ height: `${(d.count / max) * 100}%`, minHeight: "2px" }} />
              <div className="text-[8px] text-[#555] truncate w-full text-center">{d.day.slice(5)}</div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

const statusColor = (s: string) => s === "blocked" ? "#ff8855" : s === "error" ? "#ff5566" : "#00ccaa";

export function Dashboard() {
  const [days, setDays] = useState(7);
  const [stats, setStats] = useState<Stats | null>(null);
  const [rows, setRows] = useState<QueryRow[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState<null | "csv" | "db">(null);
  const [error, setError] = useState<string | null>(null);

  // Read-only SQL console state.
  const [sql, setSql] = useState(SQL_DEFAULT);
  const [sqlResult, setSqlResult] = useState<SqlResult | null>(null);
  const [sqlError, setSqlError] = useState<string | null>(null);
  const [sqlRunning, setSqlRunning] = useState(false);

  const runSql = useCallback(async () => {
    setSqlRunning(true);
    setSqlError(null);
    try {
      const res = await fetch("/api/dashboard/sql", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql }),
      });
      const data = await res.json().catch(() => null);
      // Defensive: any non-ok response (404 if the backend wasn't restarted, 422,
      // 500, auth challenge) or an unexpected shape must surface as an error, not
      // get fed to the result table where columns/rows would be undefined.
      if (!res.ok || !data) {
        setSqlError((data && (data.error || data.detail)) || `HTTP ${res.status}`);
        setSqlResult(null);
      } else if (data.error) {
        setSqlError(data.error);
        setSqlResult(null);
      } else if (Array.isArray(data.columns) && Array.isArray(data.rows)) {
        setSqlResult(data);
      } else {
        setSqlError("Unexpected response from server");
        setSqlResult(null);
      }
    } catch (e) {
      setSqlError(e instanceof Error ? e.message : String(e));
      setSqlResult(null);
    } finally {
      setSqlRunning(false);
    }
  }, [sql]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await fetch(`/api/dashboard/stats?days=${days}`).then((r) => r.json());
      setStats(s);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { load(); }, [load]);

  // Recent-queries table lives on its own fetch: it's always the latest rows
  // (not date-filtered like the stats), and it refetches as the operator types
  // in the search box. Question + answer text are decrypted server-side.
  const reloadQueries = useCallback(() => {
    fetch(`/api/dashboard/queries?limit=100&search=${encodeURIComponent(search)}`)
      .then((r) => r.json())
      .then((q) => setRows(q.queries || []))
      .catch((e) => setError(String(e)));
  }, [search]);

  // Debounce so each keystroke doesn't fire a request.
  useEffect(() => {
    const t = setTimeout(reloadQueries, 300);
    return () => clearTimeout(t);
  }, [reloadQueries]);

  // Download the full query history for the range: CSV (spreadsheet) or a clean
  // decrypted SQLite .db (SQL tools). The backend exports every row in the
  // window, not just the 100 shown in the table.
  const downloadExport = useCallback(async (kind: "csv" | "db") => {
    setExporting(kind);
    setError(null);
    try {
      const res = await fetch(`/api/dashboard/export.${kind}?days=${days}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `query_history_${days === 0 ? "all" : `${days}d`}.${kind}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(`Export failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setExporting(null);
    }
  }, [days]);

  return (
    <div className="h-full overflow-y-auto bg-[#1a1a1a] text-[#e0e0e0] p-6">
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-base font-bold text-[#e0e0e0]">Query Analytics</h2>
        <div className="flex items-center gap-3">
          <div className="flex rounded-md border border-[#333] overflow-hidden text-xs">
            {RANGES.map((r) => (
              <button key={r.label} onClick={() => setDays(r.days)}
                className={`px-3 py-1.5 transition-colors ${days === r.days ? "bg-[#00ccaa] text-black" : "bg-[#252525] text-[#888] hover:bg-[#2a2a2a]"}`}>
                {r.label}
              </button>
            ))}
          </div>
          <button onClick={() => downloadExport("csv")} disabled={loading || exporting !== null || !stats || stats.total === 0}
            title="Export the full query history for this range as CSV"
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded border border-[#333] text-[#888] hover:text-[#00ccaa] hover:bg-[#2a2a2a] transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
            <Download size={13} className={exporting === "csv" ? "animate-pulse" : ""} /> Export CSV
          </button>
          <button onClick={() => downloadExport("db")} disabled={loading || exporting !== null || !stats || stats.total === 0}
            title="Export the full query history as a decrypted SQLite database (question + answer in plaintext) for SQL analysis"
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded border border-[#333] text-[#888] hover:text-[#00ccaa] hover:bg-[#2a2a2a] transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
            <Database size={13} className={exporting === "db" ? "animate-pulse" : ""} /> Export DB
          </button>
          <button onClick={() => { load(); reloadQueries(); }} disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded border border-[#333] text-[#888] hover:text-[#e0e0e0] hover:bg-[#2a2a2a] transition-colors disabled:opacity-50">
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} /> Refresh
          </button>
        </div>
      </div>

      {error && <div className="text-xs text-[#ff5566] mb-4">Failed to load: {error}</div>}

      {stats && stats.total === 0 ? (
        <div className="text-sm text-[#888] mt-10 text-center">
          No query history yet. Ask a few questions in the Chat tab, then come back.
        </div>
      ) : stats ? (
        <div className="flex flex-col gap-5">
          {/* Headline stats */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <Stat icon={<MessageSquare size={12} />} label="Total queries" value={String(stats.total)} />
            <Stat icon={<ShieldOff size={12} />} label="Blocked" value={`${stats.blocked} (${Math.round(stats.blocked_rate * 100)}%)`} accent="#ff8855" />
            <Stat icon={<AlertTriangle size={12} />} label="Errors" value={String(stats.errors)} accent={stats.errors ? "#ff5566" : undefined} />
            <Stat icon={<Clock size={12} />} label="Latency p50 / p95" value={`${stats.p50_latency_ms ?? "–"} / ${stats.p95_latency_ms ?? "–"} ms`} />
            <Stat label="Avg top score" value={stats.avg_top_score != null ? stats.avg_top_score.toFixed(2) : "–"} accent="#00ccaa" />
          </div>

          <VolumeChart data={stats.volume} />

          {/* Marketing focus: which products + what people ask */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <BarList title="Most-asked products · primary subject" data={stats.top_products} colorFor={() => "#00ccaa"} />
            <BarList title="Most-mentioned products · incl. comparisons" data={stats.most_mentioned} colorFor={() => "#7aa2f7"} />
          </div>

          <div className="grid grid-cols-1 gap-3">
            <Card>
              <div className="text-[10px] uppercase tracking-widest text-[#666] mb-3">Top questions</div>
              {stats.top_questions.length === 0 ? (
                <div className="text-xs text-[#444]">no data</div>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {stats.top_questions.map((q, i) => (
                    <div key={i} className="flex items-center gap-3 text-xs">
                      <span className="text-[#00ccaa] tabular-nums w-6">×{q.count}</span>
                      <span className="flex-1 truncate text-[#c0c0c0]" title={q.query}>{q.query}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <BarList title="Intent distribution" data={stats.by_intent} colorFor={(k) => INTENT_COLOR[k] || "#00ccaa"} />
            <BarList title="Guard triggers" data={stats.by_gate} colorFor={() => "#ff8855"} />
            <BarList title="Top retrieved sources" data={stats.top_sources} />
          </div>

          {/* Knowledge gaps */}
          <Card>
            <div className="text-[10px] uppercase tracking-widest text-[#666] mb-3">
              Knowledge gaps · frequent questions answered despite low retrieval scores
            </div>
            {stats.knowledge_gaps.length === 0 ? (
              <div className="text-xs text-[#444]">no weak-retrieval queries — knowledge base covers asked questions well</div>
            ) : (
              <div className="flex flex-col gap-1.5">
                {stats.knowledge_gaps.map((g, i) => (
                  <div key={i} className="flex items-center gap-3 text-xs">
                    <span className="text-[#f0c070] tabular-nums w-6">×{g.count}</span>
                    <span className="flex-1 truncate text-[#c0c0c0]" title={g.query}>{g.query}</span>
                    <span className="text-[#ff5566] tabular-nums">top {g.avg_top_score.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* Read-only SQL console — ad-hoc diagnosis over the decrypted log */}
          <Card>
            <div className="flex items-center justify-between gap-3 mb-2">
              <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-widest text-[#666]">
                <Terminal size={12} /> SQL console · read-only
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                {SQL_EXAMPLES.map((ex) => (
                  <button key={ex.label} onClick={() => setSql(ex.sql)}
                    className="px-2 py-0.5 text-[10px] rounded border border-[#2a2a2a] text-[#888] hover:text-[#00ccaa] hover:border-[#00ccaa]/40 transition-colors">
                    {ex.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="text-[10px] text-[#555] mb-2">
              Table <span className="text-[#888]">queries</span> · columns: <span className="text-[#777]">{SQL_COLUMNS}</span>
            </div>
            <textarea
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); runSql(); } }}
              spellCheck={false}
              rows={5}
              placeholder="SELECT ... FROM queries WHERE ..."
              className="w-full font-mono text-xs bg-[#0f0f0f] border border-[#2a2a2a] rounded p-2.5 text-[#d0d0d0] placeholder-[#555] focus:outline-none focus:border-[#00ccaa] resize-y"
            />
            <div className="flex items-center gap-3 mt-2">
              <button onClick={runSql} disabled={sqlRunning}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-[#00ccaa] text-black font-medium hover:bg-[#00e0bb] transition-colors disabled:opacity-50">
                <Play size={13} className={sqlRunning ? "animate-pulse" : ""} /> Run
              </button>
              <span className="text-[10px] text-[#555]">⌘/Ctrl + Enter · read-only, max 1000 rows</span>
              {sqlResult && !sqlError && (
                <span className="text-[10px] text-[#666] ml-auto">
                  {sqlResult.row_count} row{sqlResult.row_count === 1 ? "" : "s"}{sqlResult.truncated ? " (truncated at 1000)" : ""}
                </span>
              )}
            </div>

            {sqlError && <div className="mt-2 text-xs text-[#ff5566] font-mono">⊘ {sqlError}</div>}

            {sqlResult && !sqlError && Array.isArray(sqlResult.columns) && Array.isArray(sqlResult.rows) && (
              <div className="mt-3 overflow-x-auto max-h-96 overflow-y-auto border border-[#1f1f1f] rounded">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-[#141414]">
                    <tr className="text-[#555] text-left border-b border-[#2a2a2a]">
                      {sqlResult.columns.map((c) => (
                        <th key={c} className="py-1.5 px-2 font-normal whitespace-nowrap">{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sqlResult.rows.map((row, i) => (
                      <tr key={i} className="border-b border-[#1a1a1a] hover:bg-[#1a1a1a]">
                        {row.map((cell, j) => {
                          const s = cell === null ? "" : String(cell);
                          return (
                            <td key={j} className="py-1 px-2 max-w-[360px] truncate text-[#c0c0c0] align-top" title={s}>
                              {cell === null ? <span className="text-[#444]">null</span> : s}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                    {sqlResult.rows.length === 0 && (
                      <tr><td className="py-2 px-2 text-[#444]" colSpan={Math.max(1, sqlResult.columns.length)}>no rows</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {/* Recent queries table */}
          <Card>
            <div className="flex items-center justify-between gap-3 mb-3">
              <div className="text-[10px] uppercase tracking-widest text-[#666]">Recent queries</div>
              <div className="relative">
                <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-[#555]" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search question or answer…"
                  className="pl-7 pr-2 py-1 w-60 text-xs bg-[#1a1a1a] border border-[#333] rounded text-[#d0d0d0] placeholder-[#555] focus:outline-none focus:border-[#00ccaa]"
                />
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-[#555] text-left border-b border-[#2a2a2a]">
                    <th className="py-1.5 pr-3 font-normal">Time</th>
                    <th className="py-1.5 pr-3 font-normal">Query</th>
                    <th className="py-1.5 pr-3 font-normal">Answer</th>
                    <th className="py-1.5 pr-3 font-normal">Product</th>
                    <th className="py-1.5 pr-3 font-normal">Intent</th>
                    <th className="py-1.5 pr-3 font-normal">Status</th>
                    <th className="py-1.5 pr-3 font-normal text-right">Top</th>
                    <th className="py-1.5 pr-3 font-normal text-right">Hits</th>
                    <th className="py-1.5 pr-3 font-normal text-right">ms</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id} className="border-b border-[#1f1f1f] hover:bg-[#1a1a1a]">
                      <td className="py-1.5 pr-3 text-[#666] whitespace-nowrap">{r.ts.slice(5, 16).replace("T", " ")}</td>
                      <td className="py-1.5 pr-3 max-w-[260px] truncate text-[#d0d0d0]" title={r.blocked_reason ? `${r.query}\n⊘ ${r.blocked_reason}` : r.query}>{r.query}</td>
                      <td className="py-1.5 pr-3 max-w-[320px] truncate text-[#999]" title={r.answer || ""}>{r.answer || "–"}</td>
                      <td className="py-1.5 pr-3 whitespace-nowrap text-[#00ccaa]">{r.product || "–"}</td>
                      <td className="py-1.5 pr-3">
                        <span className="px-1.5 py-0.5 rounded text-[10px]" style={{ color: INTENT_COLOR[r.intent] || "#888", background: `${INTENT_COLOR[r.intent] || "#888"}1a` }}>{r.intent}</span>
                      </td>
                      <td className="py-1.5 pr-3" style={{ color: statusColor(r.status) }}>
                        {r.status}{r.gate ? ` · ${r.gate}` : ""}
                      </td>
                      <td className="py-1.5 pr-3 text-right tabular-nums text-[#aaa]">{r.top_score != null ? r.top_score.toFixed(2) : "–"}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums text-[#888]">{r.n_passed}/{r.n_retrieved}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums text-[#888]">{r.latency_ms ?? "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {rows.length === 0 && <div className="text-xs text-[#444] py-3">{search ? "no matching queries" : "no queries yet"}</div>}
            </div>
          </Card>
        </div>
      ) : (
        <div className="text-sm text-[#888]">Loading…</div>
      )}
    </div>
  );
}
