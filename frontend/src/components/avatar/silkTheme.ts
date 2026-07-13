// Shared silk-avatar palette + status vocabulary. Kept out of SilkAvatar.tsx so
// that file only exports a component (React Fast Refresh requires it), letting a
// composing layout — e.g. the Soft HUD right-rail avatar card — echo the avatar's
// accent colour and status label without importing the component itself.
import type { AvatarState } from "./types";

export type RGB = [number, number, number];

// Per-state accent (main + dim), restrained so the animation reads as ambient.
export const SILK_ACCENT: Record<AvatarState, { main: RGB; dim: RGB }> = {
  idle:  { main: [63, 208, 189],  dim: [29, 125, 114] },
  think: { main: [70, 200, 224],  dim: [31, 118, 131] },
  talk:  { main: [242, 246, 250], dim: [120, 132, 146] },
  happy: { main: [70, 224, 150],  dim: [28, 120, 80] },
  error: { main: [224, 85, 105],  dim: [125, 37, 48] },
};

export const rgba = (c: RGB, a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

export const SILK_STATUS_TEXT: Record<AvatarState, string> = {
  idle: "IDLE",
  think: "SEARCHING...",
  talk: "RESPONDING",
  happy: "COMPLETE",
  error: "ERROR",
};

export const silkStatusColor = (state: AvatarState) => rgba(SILK_ACCENT[state].main, 1);
