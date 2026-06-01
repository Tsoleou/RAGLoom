import { useRef, useEffect, useCallback } from "react";
import type { AvatarProps, AvatarState } from "./types";

// ── Palette (restrained, instrument-like — RGB tuples for gradient mixing) ──
type RGB = [number, number, number];
const ACCENT: Record<AvatarState, { main: RGB; dim: RGB }> = {
  idle:  { main: [63, 208, 189],  dim: [29, 125, 114] },
  think: { main: [70, 200, 224],  dim: [31, 118, 131] },
  talk:  { main: [90, 208, 168],  dim: [31, 125, 99] },
  happy: { main: [70, 224, 150],  dim: [28, 120, 80] },
  error: { main: [224, 85, 105],  dim: [125, 37, 48] },
};
const WHITE: RGB = [255, 255, 255];
const rgba = (c: RGB, a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

// Per-state motion: pulse depth/speed, ring rotation speed, and effect flags.
const CFG: Record<
  AvatarState,
  {
    pulseAmp: number;
    pulseSpd: number;
    rot: number;
    sweep?: boolean;
    ripple?: number; // ripple emit period in frames (0 = off)
    flicker?: boolean;
  }
> = {
  idle:  { pulseAmp: 0.07, pulseSpd: 0.035, rot: 0.004 },
  think: { pulseAmp: 0.11, pulseSpd: 0.090, rot: 0.020, sweep: true },
  talk:  { pulseAmp: 0.22, pulseSpd: 0.170, rot: 0.010, ripple: 38 },
  happy: { pulseAmp: 0.16, pulseSpd: 0.110, rot: 0.014, ripple: 60 },
  error: { pulseAmp: 0.00, pulseSpd: 0.000, rot: 0.000, flicker: true },
};

const STATUS_TEXT: Record<AvatarState, string> = {
  idle: "IDLE",
  think: "SEARCHING...",
  talk: "RESPONDING",
  happy: "COMPLETE",
  error: "ERROR",
};

export function HudAvatar({ state, message, size = 96 }: AvatarProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef(0);
  const stateRef = useRef<AvatarState>(state);
  const rafRef = useRef<number>(0);

  stateRef.current = state;

  // ── Draw the HUD core ─────────────────────────────────────
  const draw = useCallback((ctx: CanvasRenderingContext2D, W: number) => {
    const s = stateRef.current;
    const f = frameRef.current;
    const cfg = CFG[s];
    const m = ACCENT[s].main;
    const d = ACCENT[s].dim;
    const cx = W / 2;
    const cy = W / 2;
    const R = W / 2;

    ctx.clearRect(0, 0, W, W);

    // ── Outer faint ring ──
    ctx.strokeStyle = rgba(d, 0.35);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, R * 0.82, 0, Math.PI * 2);
    ctx.stroke();

    // ── Rotating tick ring ──
    const rot = f * cfg.rot;
    const ticks = 36;
    const tickR = R * 0.66;
    for (let i = 0; i < ticks; i++) {
      const a = rot + (i / ticks) * Math.PI * 2;
      const major = i % 3 === 0;
      const r2 = tickR + (major ? R * 0.06 : R * 0.03);
      ctx.strokeStyle = rgba(d, major ? 0.7 : 0.32);
      ctx.lineWidth = major ? 1.4 : 1;
      ctx.beginPath();
      ctx.moveTo(cx + Math.cos(a) * tickR, cy + Math.sin(a) * tickR);
      ctx.lineTo(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2);
      ctx.stroke();
    }

    // ── Inner HUD arcs (two opposing, counter-rotating) ──
    const arcR = R * 0.48;
    const arot = -f * cfg.rot * 1.6;
    ctx.lineWidth = 2;
    ctx.strokeStyle = rgba(m, 0.8);
    for (let k = 0; k < 2; k++) {
      const base = arot + k * Math.PI;
      ctx.beginPath();
      ctx.arc(cx, cy, arcR, base + 0.25, base + Math.PI * 0.55);
      ctx.stroke();
    }

    // ── Scanning sweep (think) — fading comet trail on the arc radius ──
    if (cfg.sweep) {
      const head = f * 0.06;
      const steps = 14;
      const span = Math.PI * 0.6;
      for (let i = 0; i < steps; i++) {
        const a0 = head - (i / steps) * span;
        ctx.globalAlpha = (1 - i / steps) * 0.55;
        ctx.lineWidth = 2;
        ctx.strokeStyle = rgba(m, 1);
        ctx.beginPath();
        ctx.arc(cx, cy, arcR, a0 - 0.06, a0);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // ── Expanding ripples (talk / happy) ──
    if (cfg.ripple) {
      const t = (f % cfg.ripple) / cfg.ripple; // 0..1
      const rr = R * 0.16 + t * R * 0.55;
      ctx.strokeStyle = rgba(m, (1 - t) * 0.5);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(cx, cy, rr, 0, Math.PI * 2);
      ctx.stroke();
    }

    // ── Core ──
    const beat = s === "talk" ? Math.abs(Math.sin(f * cfg.pulseSpd)) : Math.sin(f * cfg.pulseSpd);
    const coreR = R * 0.16 * (1 + cfg.pulseAmp * beat);
    const alpha = cfg.flicker ? (Math.floor(f / 4) % 2 === 0 ? 1 : 0.4) : 1;

    // bloom
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 3.4);
    g.addColorStop(0, rgba(m, 0.95 * alpha));
    g.addColorStop(0.35, rgba(m, 0.5 * alpha));
    g.addColorStop(1, rgba(m, 0));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, coreR * 3.4, 0, Math.PI * 2);
    ctx.fill();

    // solid core
    ctx.fillStyle = rgba(m, alpha);
    ctx.beginPath();
    ctx.arc(cx, cy, coreR, 0, Math.PI * 2);
    ctx.fill();

    // hot center
    ctx.fillStyle = rgba(WHITE, 0.85 * alpha);
    ctx.beginPath();
    ctx.arc(cx, cy, coreR * 0.4, 0, Math.PI * 2);
    ctx.fill();
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
    ctx.lineCap = "round";

    function loop() {
      frameRef.current++;
      draw(ctx!, size);
      rafRef.current = requestAnimationFrame(loop);
    }

    rafRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(rafRef.current);
  }, [draw, size]);

  return (
    <div className="flex flex-col items-center gap-1.5">
      {/* Scanline container */}
      <div className="relative rounded-lg overflow-hidden bg-[#0e0e1a] border border-[#1a1a35] p-3">
        <div
          className="absolute top-0 left-0 right-0 h-px bg-[#3fd0bd] opacity-[0.06]"
          style={{ animation: "scanY 4s linear infinite" }}
        />
        <canvas ref={canvasRef} style={{ width: size, height: size, display: "block" }} />
        <div
          className="text-[9px] text-center tracking-[2px] mt-1 font-mono"
          style={{ color: rgba(ACCENT[state].main, 1) }}
        >
          {STATUS_TEXT[state]}
        </div>
      </div>

      {/* Bubble */}
      {message && (
        <div className="bg-[#13132a] border border-[#252550] rounded-md px-2.5 py-1.5 text-[10px] text-[#c0c0e0] leading-relaxed max-w-[140px] text-center font-mono">
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
