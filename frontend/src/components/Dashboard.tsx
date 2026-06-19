import { useState, useEffect, useCallback } from "react";
import { RefreshCw, AlertTriangle, ShieldOff, Clock, MessageSquare } from "lucide-react";

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
};

const RANGES = [
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
  { label: "All", days: 0 },
];

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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, q] = await Promise.all([
        fetch(`/api/dashboard/stats?days=${days}`).then((r) => r.json()),
        fetch(`/api/dashboard/queries?limit=100`).then((r) => r.json()),
      ]);
      setStats(s);
      setRows(q.queries || []);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { load(); }, [load]);

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
          <button onClick={load} disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded border border-[#333] text-[#888] hover:text-[#e0e0e0] hover:bg-[#2a2a2a] transition-colors disabled:opacity-50">
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} /> Refresh
          </button>
        </div>
      </div>

      {error && <div className="text-xs text-[#ff5566] mb-4">Failed to load: {error}</div>}

      {stats && stats.total === 0 ? (
        <div className="text-sm text-[#888] mt-10 text-center">
          尚無查詢紀錄。到 Chat 分頁問幾個問題後回來看。
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
            <BarList title="主要詢問的產品 · most-asked (primary subject)" data={stats.top_products} colorFor={() => "#00ccaa"} />
            <BarList title="最常被提及的產品 · most-mentioned (incl. comparisons)" data={stats.most_mentioned} colorFor={() => "#7aa2f7"} />
          </div>

          <div className="grid grid-cols-1 gap-3">
            <Card>
              <div className="text-[10px] uppercase tracking-widest text-[#666] mb-3">熱門問題 · top questions</div>
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
              Knowledge gaps · 答得出但檢索分數低的高頻問題
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

          {/* Recent queries table */}
          <Card>
            <div className="text-[10px] uppercase tracking-widest text-[#666] mb-3">Recent queries</div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-[#555] text-left border-b border-[#2a2a2a]">
                    <th className="py-1.5 pr-3 font-normal">Time</th>
                    <th className="py-1.5 pr-3 font-normal">Query</th>
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
              {rows.length === 0 && <div className="text-xs text-[#444] py-3">no queries yet</div>}
            </div>
          </Card>
        </div>
      ) : (
        <div className="text-sm text-[#888]">Loading…</div>
      )}
    </div>
  );
}
