import { useRef, useEffect, useCallback } from "react";

type AvatarState = "idle" | "think" | "talk" | "happy" | "error";

interface Props {
  state: AvatarState;
  message: string;
  size?: number;
}

// ── Color Palette ────────────────────────────────────────────
const C = {
  metalLight:  "#a0b0c0",
  metal:       "#8090a0",
  metalDark:   "#607080",
  metalDarker: "#405060",
  bodyLight:   "#d8e4f0",
  body:        "#c0d0e0",
  bodyDark:    "#90a0b8",
  cyan:        "#00ffcc",
  cyanDark:    "#00ccaa",
  white:       "#ffffff",
  red:         "#ff5577",
  yellow:      "#ffdd44",
  pink:        "#ff99bb",
};

const STATUS_TEXT: Record<AvatarState, string> = {
  idle: "IDLE",
  think: "SEARCHING...",
  talk: "RESPONDING",
  happy: "COMPLETE",
  error: "ERROR",
};

const STATUS_COLOR: Record<AvatarState, string> = {
  idle: "#00ffcc",
  think: "#00ffcc",
  talk: "#00ffcc",
  happy: "#ffdd44",
  error: "#ff4466",
};

export function RobotAvatar({ state, message, size = 96 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef(0);
  const blinkRef = useRef(0);
  const stateRef = useRef<AvatarState>(state);
  const rafRef = useRef<number>(0);

  stateRef.current = state;

  // ── Pixel helper ─────────────────────────────────────────
  const px = useCallback(
    (ctx: CanvasRenderingContext2D, x: number, y: number, color: string) => {
      ctx.fillStyle = color;
      ctx.fillRect(x, y, 1, 1);
    },
    []
  );

  // ── Draw robot ───────────────────────────────────────────
  const drawRobot = useCallback(
    (ctx: CanvasRenderingContext2D) => {
      const s = stateRef.current;
      const f = frameRef.current;
      const blink = blinkRef.current;
      ctx.clearRect(0, 0, 32, 32);

      const bob =
        s === "idle"  ? Math.sin(f * 0.05) * 0.5 :
        s === "happy" ? Math.sin(f * 0.1) * 0.8 : 0;
      const dy = Math.round(bob * 100) / 100;

      // ── Antenna ──
      px(ctx, 15, 1 + dy, C.metalDark);
      px(ctx, 16, 1 + dy, C.metalDark);
      px(ctx, 15, 2 + dy, C.metal);
      px(ctx, 16, 2 + dy, C.metal);

      let bulb = C.cyan;
      if (s === "think") bulb = Math.sin(f * 0.15) > 0 ? C.cyan : "#005544";
      else if (s === "error") bulb = Math.floor(f / 6) % 2 === 0 ? C.red : "#881122";
      for (let x = 14; x <= 17; x++) px(ctx, x, 0 + dy, bulb);

      // ── Head ──
      const y0 = 3;
      for (let x = 8; x <= 23; x++) px(ctx, x, y0 + dy, C.metalDark);
      for (let y = y0 + 1; y <= y0 + 9; y++) {
        px(ctx, 7, y + dy, C.metalDark);
        for (let x = 8; x <= 23; x++) px(ctx, x, y + dy, C.body);
        px(ctx, 24, y + dy, C.bodyDark);
      }
      for (let x = 8; x <= 23; x++) px(ctx, x, y0 + 10 + dy, C.bodyDark);
      px(ctx, 8, y0 + 1 + dy, C.bodyLight);
      px(ctx, 9, y0 + 1 + dy, C.bodyLight);
      px(ctx, 10, y0 + 1 + dy, C.bodyLight);
      px(ctx, 8, y0 + 2 + dy, C.bodyLight);
      for (let x = 8; x <= 10; x++) px(ctx, x, y0 + 4 + dy, C.metalDark);
      for (let x = 21; x <= 23; x++) px(ctx, x, y0 + 4 + dy, C.metalDark);

      // ── Eyes ──
      const ey = 7;
      const isBlink = blink > 0 && blink < 4;

      if (s === "happy") {
        px(ctx, 10, ey + 1 + dy, C.cyan); px(ctx, 11, ey + dy, C.cyan); px(ctx, 12, ey + 1 + dy, C.cyan);
        px(ctx, 19, ey + 1 + dy, C.cyan); px(ctx, 20, ey + dy, C.cyan); px(ctx, 21, ey + 1 + dy, C.cyan);
      } else if (isBlink) {
        for (let x = 10; x <= 12; x++) px(ctx, x, ey + 1 + dy, C.cyanDark);
        for (let x = 19; x <= 21; x++) px(ctx, x, ey + 1 + dy, C.cyanDark);
      } else if (s === "error") {
        const ec = Math.floor(f / 6) % 2 === 0 ? C.red : "#cc2244";
        px(ctx, 10, ey + dy, ec); px(ctx, 12, ey + dy, ec);
        px(ctx, 11, ey + 1 + dy, ec);
        px(ctx, 10, ey + 2 + dy, ec); px(ctx, 12, ey + 2 + dy, ec);
        px(ctx, 19, ey + dy, ec); px(ctx, 21, ey + dy, ec);
        px(ctx, 20, ey + 1 + dy, ec);
        px(ctx, 19, ey + 2 + dy, ec); px(ctx, 21, ey + 2 + dy, ec);
      } else {
        let eyeColor = C.cyan;
        if (s === "think") {
          const pulse = Math.sin(f * 0.12) * 0.5 + 0.5;
          const g = Math.round(200 + 55 * pulse);
          const b = Math.round(160 + 95 * pulse);
          eyeColor = `rgb(0,${g},${b})`;
        }
        for (let x = 10; x <= 12; x++)
          for (let y = ey; y <= ey + 2; y++)
            px(ctx, x, y + dy, eyeColor);
        for (let x = 19; x <= 21; x++)
          for (let y = ey; y <= ey + 2; y++)
            px(ctx, x, y + dy, eyeColor);
        px(ctx, 12, ey + dy, C.white);
        px(ctx, 21, ey + dy, C.white);
      }

      // ── Mouth ──
      const my = 11;
      if (s === "talk") {
        const open = Math.floor(f / 5) % 2 === 0;
        if (open) {
          for (let x = 13; x <= 18; x++) px(ctx, x, my + dy, C.cyanDark);
          for (let x = 14; x <= 17; x++) px(ctx, x, my + 1 + dy, C.cyan);
          for (let x = 13; x <= 18; x++) px(ctx, x, my + 2 + dy, C.cyanDark);
        } else {
          for (let x = 13; x <= 18; x++) px(ctx, x, my + 1 + dy, C.cyan);
        }
      } else if (s === "happy") {
        px(ctx, 12, my + dy, C.cyan);
        for (let x = 13; x <= 18; x++) px(ctx, x, my + 1 + dy, C.cyan);
        px(ctx, 19, my + dy, C.cyan);
      } else if (s === "error") {
        for (let x = 13; x <= 18; x++) px(ctx, x, my + 1 + dy, C.red);
      } else {
        for (let x = 13; x <= 18; x++) px(ctx, x, my + dy, C.cyan);
      }

      // ── Cheeks ──
      if (s === "happy" || s === "talk") {
        px(ctx, 8, 10 + dy, C.pink); px(ctx, 9, 10 + dy, C.pink);
        px(ctx, 22, 10 + dy, C.pink); px(ctx, 23, 10 + dy, C.pink);
      }

      // ── Body ──
      const by = 14;
      for (let x = 9; x <= 22; x++) px(ctx, x, by + dy, C.metalDark);
      for (let y = by + 1; y <= by + 7; y++) {
        px(ctx, 9, y + dy, C.metalDark);
        for (let x = 10; x <= 21; x++) px(ctx, x, y + dy, C.metal);
        px(ctx, 22, y + dy, C.metalDarker);
      }
      for (let x = 9; x <= 22; x++) px(ctx, x, by + 8 + dy, C.metalDarker);
      px(ctx, 10, by + 1 + dy, C.metalLight); px(ctx, 11, by + 1 + dy, C.metalLight);
      px(ctx, 10, by + 2 + dy, C.metalLight);

      let core = C.cyan;
      if (s === "think") core = Math.sin(f * 0.1) > 0 ? C.cyan : "#005544";
      px(ctx, 15, by + 2 + dy, core); px(ctx, 16, by + 2 + dy, core);
      px(ctx, 14, by + 3 + dy, C.cyanDark); px(ctx, 15, by + 3 + dy, core);
      px(ctx, 16, by + 3 + dy, core); px(ctx, 17, by + 3 + dy, C.cyanDark);
      px(ctx, 15, by + 4 + dy, C.cyanDark); px(ctx, 16, by + 4 + dy, C.cyanDark);
      for (let x = 13; x <= 18; x++) px(ctx, x, by + 6 + dy, C.metalDark);

      // ── Arms ──
      const ay = 15;
      const swing =
        s === "happy" ? Math.sin(f * 0.12) * 1.2 :
        s === "talk"  ? Math.sin(f * 0.08) * 0.3 : 0;

      px(ctx, 7, ay + dy + swing, C.metalDark); px(ctx, 8, ay + dy + swing, C.metal);
      px(ctx, 6, ay + 1 + dy + swing, C.metalDark); px(ctx, 7, ay + 1 + dy + swing, C.metal);
      px(ctx, 5, ay + 2 + dy + swing, C.metal); px(ctx, 6, ay + 2 + dy + swing, C.metalLight);
      px(ctx, 5, ay + 3 + dy + swing, C.metalLight); px(ctx, 6, ay + 3 + dy + swing, C.body);

      px(ctx, 23, ay + dy - swing, C.metalDark); px(ctx, 24, ay + dy - swing, C.metal);
      px(ctx, 24, ay + 1 + dy - swing, C.metal); px(ctx, 25, ay + 1 + dy - swing, C.metalDark);
      px(ctx, 25, ay + 2 + dy - swing, C.metalLight); px(ctx, 26, ay + 2 + dy - swing, C.metal);
      px(ctx, 25, ay + 3 + dy - swing, C.body); px(ctx, 26, ay + 3 + dy - swing, C.metalLight);

      // ── Legs ──
      const ly = 23;
      px(ctx, 11, ly + dy, C.metalDark); px(ctx, 12, ly + dy, C.metal); px(ctx, 13, ly + dy, C.metal);
      px(ctx, 11, ly + 1 + dy, C.metalDark); px(ctx, 12, ly + 1 + dy, C.metal); px(ctx, 13, ly + 1 + dy, C.metal);
      px(ctx, 18, ly + dy, C.metal); px(ctx, 19, ly + dy, C.metal); px(ctx, 20, ly + dy, C.metalDark);
      px(ctx, 18, ly + 1 + dy, C.metal); px(ctx, 19, ly + 1 + dy, C.metal); px(ctx, 20, ly + 1 + dy, C.metalDark);
      px(ctx, 10, ly + 2 + dy, C.metalDark); px(ctx, 11, ly + 2 + dy, C.metal);
      px(ctx, 12, ly + 2 + dy, C.cyan); px(ctx, 13, ly + 2 + dy, C.metal); px(ctx, 14, ly + 2 + dy, C.metalDark);
      px(ctx, 17, ly + 2 + dy, C.metalDark); px(ctx, 18, ly + 2 + dy, C.metal);
      px(ctx, 19, ly + 2 + dy, C.cyan); px(ctx, 20, ly + 2 + dy, C.metal); px(ctx, 21, ly + 2 + dy, C.metalDark);

      // ── Sparkles (happy only) ──
      if (s === "happy") {
        const sp = Math.sin(f * 0.08);
        if (sp > 0.2) {
          ctx.globalAlpha = (sp - 0.2) / 0.8;
          px(ctx, 4, 4 + dy, C.yellow); px(ctx, 5, 3 + dy, C.yellow); px(ctx, 4, 2 + dy, C.yellow);
          px(ctx, 27, 5 + dy, C.yellow); px(ctx, 28, 4 + dy, C.yellow); px(ctx, 27, 3 + dy, C.yellow);
          px(ctx, 3, 18 + dy, C.cyan); px(ctx, 28, 20 + dy, C.cyan);
          ctx.globalAlpha = 1;
        }
      }
    },
    [px]
  );

  // ── Animation loop ───────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.imageSmoothingEnabled = false;

    function loop() {
      frameRef.current++;
      blinkRef.current--;
      if (blinkRef.current < -80 - Math.random() * 100) blinkRef.current = 5;
      drawRobot(ctx!);
      rafRef.current = requestAnimationFrame(loop);
    }

    rafRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(rafRef.current);
  }, [drawRobot]);

  return (
    <div className="flex flex-col items-center gap-1.5">
      {/* Scanline container */}
      <div className="relative rounded-lg overflow-hidden bg-[#0e0e1a] border border-[#1a1a35] p-3">
        <div
          className="absolute top-0 left-0 right-0 h-px bg-[#00ffcc] opacity-[0.06]"
          style={{ animation: "scanY 4s linear infinite" }}
        />
        <canvas
          ref={canvasRef}
          width={32}
          height={32}
          style={{ width: size, height: size, imageRendering: "pixelated" as const }}
        />
        <div
          className="text-[9px] text-center tracking-[2px] mt-1 font-mono"
          style={{ color: STATUS_COLOR[state] }}
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
