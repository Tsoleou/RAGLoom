export type CriticVerdict = "pass" | "fail" | "skip";

export interface CriticOutput {
  verdict: CriticVerdict;
  reason: string;
  revised: boolean;
  mode: string;
}

export function parseCriticOutput(text: string): CriticOutput | null {
  if (!text) return null;
  const match = text.match(/\{[\s\S]*\}/);
  if (!match) return null;
  try {
    const obj = JSON.parse(match[0]);
    if (obj && obj.__critic === true && typeof obj.verdict === "string") {
      return {
        verdict: obj.verdict as CriticVerdict,
        reason: String(obj.reason || ""),
        revised: Boolean(obj.revised),
        mode: String(obj.mode || "audit"),
      };
    }
  } catch {
    return null;
  }
  return null;
}

export interface CriticTheme {
  label: string;
  bg: string;
  border: string;
  text: string;
  dot: string;
}

const PASS_THEME: CriticTheme = {
  label: "PASS",
  bg: "#1d3a2a",
  border: "#50a070",
  text: "#7fd8a0",
  dot: "#50d090",
};

const FAIL_THEME: CriticTheme = {
  label: "FAIL",
  bg: "#3a1d1d",
  border: "#c05050",
  text: "#e08080",
  dot: "#e06060",
};

const REVISED_THEME: CriticTheme = {
  label: "REVISED",
  bg: "#1d2a3a",
  border: "#5080c0",
  text: "#80b0e0",
  dot: "#6090d0",
};

const SKIP_THEME: CriticTheme = {
  label: "SKIPPED",
  bg: "#2a2a2a",
  border: "#555",
  text: "#999",
  dot: "#777",
};

export function getCriticTheme(output: CriticOutput): CriticTheme {
  if (output.verdict === "skip") return SKIP_THEME;
  if (output.revised) return REVISED_THEME;
  if (output.verdict === "pass") return PASS_THEME;
  return FAIL_THEME;
}
