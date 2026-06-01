export interface JudgeVerdict {
  i: number;
  keep: boolean;
  reason: string;
  source: string;
  score: number;
}

export interface JudgeTraceOutput {
  kept: number;
  total: number;
  verdicts: JudgeVerdict[];
}

/** Parse the JSON payload emitted by the judge_trace_inspector executor.
 *  Returns null for the placeholder "(no judge trace …)" text or any
 *  non-matching preview, so the caller can fall back to the plain renderer. */
export function parseJudgeTrace(text: string): JudgeTraceOutput | null {
  if (!text) return null;
  const match = text.match(/\{[\s\S]*\}/);
  if (!match) return null;
  try {
    const obj = JSON.parse(match[0]);
    if (obj && obj.__judge_trace === true && Array.isArray(obj.verdicts)) {
      return {
        kept: Number(obj.kept ?? 0),
        total: Number(obj.total ?? obj.verdicts.length),
        verdicts: obj.verdicts.map((v: Record<string, unknown>) => ({
          i: Number(v.i ?? 0),
          keep: Boolean(v.keep),
          reason: String(v.reason || ""),
          source: String(v.source || ""),
          score: Number(v.score ?? 0),
        })),
      };
    }
  } catch {
    return null;
  }
  return null;
}
