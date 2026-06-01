// ── Single switch point for the active avatar theme ──────────────────────────
// To swap themes: write a new component that accepts AvatarProps (see ./types),
// then change the one line below to re-export it as `Avatar`. Callers always
// import { Avatar } from "./avatar/Avatar" and never need to change.
//
//   export { OrbAvatar as Avatar } from "./OrbAvatar";
//
export { SilkAvatar as Avatar } from "./SilkAvatar";
export type { AvatarProps, AvatarState } from "./types";
