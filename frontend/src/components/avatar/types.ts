// Shared contract for every avatar theme. A theme is just a React component
// that accepts AvatarProps and handles all five AvatarState values — swap one
// for another by changing the single re-export in ./Avatar.tsx.

export type AvatarState = "idle" | "think" | "talk" | "happy" | "error";

export interface AvatarProps {
  state: AvatarState;
  message: string;
  size?: number;
}
