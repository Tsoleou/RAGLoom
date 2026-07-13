import { useRef, useEffect, useCallback } from "react";
import type { AvatarProps, AvatarState } from "./types";
import { SILK_ACCENT as ACCENT, SILK_STATUS_TEXT as STATUS_TEXT, rgba, type RGB } from "./silkTheme";

const mix = (a: RGB, b: RGB, t: number): RGB => [
  Math.round(a[0] + (b[0] - a[0]) * t),
  Math.round(a[1] + (b[1] - a[1]) * t),
  Math.round(a[2] + (b[2] - a[2]) * t),
];

// Per-state flow: drift speed, amplitude (fraction of height), brightness,
// strand count, plus organic modifiers. No sharp peaks anywhere — talk swells
// the whole field instead of spiking, so it never reads as an ECG trace.
const CFG: Record<
  AvatarState,
  {
    flow: number;
    amp: number;
    alpha: number;
    strands: number;
    turb?: boolean;    // extra turbulence in the wave shape (think)
    swell?: boolean;   // whole field breathes in/out (talk)
    jitter?: boolean;  // small frozen jitter (error)
    flicker?: boolean; // brightness flicker (error)
  }
> = {
  idle:  { flow: 0.010, amp: 0.10, alpha: 0.45, strands: 7 },
  think: { flow: 0.028, amp: 0.12, alpha: 0.55, strands: 7, turb: true },
  talk:  { flow: 0.018, amp: 0.11, alpha: 0.55, strands: 7, swell: true },
  happy: { flow: 0.024, amp: 0.14, alpha: 0.70, strands: 9 },
  error: { flow: 0.000, amp: 0.07, alpha: 0.50, strands: 6, jitter: true, flicker: true },
};

// `bare` renders only the animated canvas (no box, scanline, status or bubble)
// so callers can wrap it in their own frame — used for the right-rail avatar card
// and the 40px mini glyphs beside assistant messages.
// `animate` (default true) drives the continuous rAF loop; pass false for the
// per-message mini glyphs so a long chat doesn't accumulate N live rAF loops —
// they draw one static frame instead.
export function SilkAvatar({
  state,
  message,
  size = 96,
  bare = false,
  animate = true,
}: AvatarProps & { bare?: boolean; animate?: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef(0);
  const stateRef = useRef<AvatarState>(state);
  const rafRef = useRef<number>(0);

  // Keep the latest state readable inside the persistent rAF loop without
  // restarting it. Synced in an effect (never written during render).
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  // ── Draw flowing silk threads ─────────────────────────────
  const draw = useCallback((ctx: CanvasRenderingContext2D, W: number) => {
    const s = stateRef.current;
    const f = frameRef.current;
    const cfg = CFG[s];
    const m = ACCENT[s].main;
    const d = ACCENT[s].dim;
    const H = W;

    ctx.clearRect(0, 0, W, H);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";

    const t = f * cfg.flow;
    const swell = cfg.swell ? 1 + 0.45 * Math.abs(Math.sin(f * 0.05)) : 1;
    // Gentle brightness breathing (not a hard on/off flicker).
    const flick = cfg.flicker ? 0.78 + 0.22 * Math.sin(f * 0.1) : 1;
    const amp = H * cfg.amp * swell;
    const f1 = ((Math.PI * 2) / W) * 1.7; // low frequency → smooth, no spikes
    const f2 = ((Math.PI * 2) / W) * 0.9;

    for (let i = 0; i < cfg.strands; i++) {
      const u = (i + 0.5) / cfg.strands; // 0..1 vertical position
      const baseY = H * 0.12 + u * H * 0.76; // keep top/bottom margin
      const ph = i * 0.9;
      const drift = Math.sin(t * 0.6 + i * 1.3) * H * 0.04;
      const turb = cfg.turb ? Math.sin(t * 1.6 + i) * 0.4 : 0;
      const jit = cfg.jitter ? Math.sin(f * 0.25 + i * 5) * H * 0.006 : 0;
      const depth = Math.abs(u - 0.5) * 2; // 0 centre .. 1 edge
      const col = mix(m, d, depth * 0.6);
      const a = cfg.alpha * (0.5 + 0.5 * (1 - depth)) * flick;

      // Ends fade out — threads flow in and out rather than spanning edge-to-edge.
      const grad = ctx.createLinearGradient(0, 0, W, 0);
      grad.addColorStop(0, rgba(col, 0));
      grad.addColorStop(0.5, rgba(col, a));
      grad.addColorStop(1, rgba(col, 0));

      const path = new Path2D();
      for (let x = 0; x <= W; x += 2) {
        const y =
          baseY +
          drift +
          jit +
          amp * Math.sin(x * f1 + t + ph) +
          amp * 0.5 * Math.sin(x * f2 - t * 0.7 + ph * 1.6 + turb);
        if (x === 0) path.moveTo(x, y);
        else path.lineTo(x, y);
      }

      ctx.strokeStyle = grad;
      // soft glow
      ctx.globalAlpha = 0.35;
      ctx.lineWidth = 3.2;
      ctx.stroke(path);
      // crisp core
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1.3;
      ctx.stroke(path);
    }
    ctx.globalAlpha = 1;
  }, []);

  // ── Animation loop ───────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);

    // Static mode: one frame, no perpetual loop (used for per-message glyphs).
    if (!animate) {
      draw(ctx, size);
      return;
    }

    function loop() {
      frameRef.current++;
      draw(ctx!, size);
      rafRef.current = requestAnimationFrame(loop);
    }

    rafRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(rafRef.current);
  }, [draw, size, animate]);

  // Canvas-only: the composing layout supplies its own frame/status/message.
  if (bare) {
    return (
      <canvas
        aria-hidden="true"
        ref={canvasRef}
        style={{ width: size, height: size, display: "block" }}
      />
    );
  }

  return (
    <div className="flex flex-col items-center gap-1.5">
      {/* Scanline container */}
      <div
        className="relative rounded-lg overflow-hidden bg-[#0e1614] border border-[#1c2a26] p-3"
        role="img"
        aria-label={`Assistant avatar — ${STATUS_TEXT[state]}`}
      >
        <div
          aria-hidden="true"
          className="absolute top-0 left-0 right-0 h-px bg-[#3fd0bd] opacity-[0.06]"
          style={{ animation: "scanY 4s linear infinite" }}
        />
        <canvas
          aria-hidden="true"
          ref={canvasRef}
          style={{ width: size, height: size, display: "block" }}
        />
        <div
          aria-hidden="true"
          className="text-[9px] text-center tracking-[2px] mt-1 font-mono"
          style={{ color: rgba(ACCENT[state].main, 1) }}
        >
          {STATUS_TEXT[state]}
        </div>
      </div>

      {/* Bubble */}
      {message && (
        <div
          role="status"
          className="bg-[#0e1614] border border-[#1c2a26] rounded-md px-2.5 py-1.5 text-[10px] text-[#8fbfb2] leading-relaxed max-w-[140px] text-center font-mono"
        >
          {message}
        </div>
      )}

      {/* Scanline keyframe (injected once) */}
      <style>{`
        @keyframes scanY {
          0%   { top: 0; }
          100% { top: 100%; }
        }
      `}</style>
    </div>
  );
}
