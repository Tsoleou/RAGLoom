export type ChatbotEmotion =
  | "idle"
  | "thinking"
  | "happy"
  | "confused"
  | "explaining";

export interface ChatbotOutput {
  reply: string;
  emotion: ChatbotEmotion;
}

export function parseChatbotOutput(text: string): ChatbotOutput | null {
  if (!text) return null;
  const match = text.match(/\{[\s\S]*\}/);
  if (!match) return null;
  try {
    const obj = JSON.parse(match[0]);
    if (typeof obj.reply === "string" && typeof obj.emotion === "string") {
      return { reply: obj.reply, emotion: obj.emotion as ChatbotEmotion };
    }
  } catch {
    return null;
  }
  return null;
}

export interface EmotionTheme {
  label: string;
  bg: string;
  border: string;
  text: string;
  dot: string;
}

export const EMOTION_THEMES: Record<string, EmotionTheme> = {
  happy:      { label: "HAPPY",      bg: "#1d3a2a", border: "#50a070", text: "#7fd8a0", dot: "#50d090" },
  thinking:   { label: "THINKING",   bg: "#1d2a3a", border: "#5070a0", text: "#90b0e0", dot: "#6090c0" },
  confused:   { label: "CONFUSED",   bg: "#3a2e1d", border: "#c08040", text: "#e0b070", dot: "#e0a050" },
  explaining: { label: "EXPLAINING", bg: "#1d3338", border: "#4090a0", text: "#70c0d0", dot: "#50b0c0" },
  idle:       { label: "IDLE",       bg: "#2a2a2a", border: "#555",    text: "#999",    dot: "#777"    },
};

export function getEmotionTheme(emotion: string): EmotionTheme {
  return EMOTION_THEMES[emotion] || EMOTION_THEMES.idle;
}

export type AvatarBaseState = "idle" | "think" | "talk" | "happy" | "error";

export function emotionToAvatarState(emotion: string): AvatarBaseState {
  switch (emotion) {
    case "happy":      return "happy";
    case "thinking":   return "think";
    case "confused":   return "think";
    case "explaining": return "talk";
    case "idle":       return "idle";
    default:           return "happy";
  }
}
