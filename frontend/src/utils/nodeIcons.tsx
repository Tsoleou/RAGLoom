import { createElement } from "react";
import {
  FileText,
  Scissors,
  Network,
  Database,
  BookOpen,
  MessageSquare,
  ShieldAlert,
  DollarSign,
  Gauge,
  Scale,
  SlidersHorizontal,
  PackageSearch,
  Search,
  FileCode,
  Settings,
  Sparkles,
  ShieldCheck,
  MessageCircle,
  ClipboardList,
  Target,
  BarChart3,
  Shuffle,
  CircleCheck,
  FileBarChart,
  ListChecks,
  Box,
  type LucideIcon,
} from "lucide-react";

/**
 * typeId → lucide icon. Single place to assign a glyph per node type so the
 * canvas node header and the palette stay in sync. Falls back to `Box` for any
 * future node type that lands before it gets a mapping here.
 */
const NODE_ICONS: Record<string, LucideIcon> = {
  // ingest
  loader: FileText,
  chunker: Scissors,
  vectorstore: Database,
  // shared
  embedder: Network,
  // query
  reference_loader: BookOpen,
  query_input: MessageSquare,
  guardrail: ShieldAlert,
  price_guard: DollarSign,
  scope_gate: Gauge,
  retrieval_judge: Scale,
  constraint_filter: SlidersHorizontal,
  product_selector: PackageSearch,
  retriever: Search,
  prompt_builder: FileCode,
  system_prompt: Settings,
  generator: Sparkles,
  output_critic: ShieldCheck,
  result_display: MessageCircle,
  // eval / debug
  judge_trace_inspector: ListChecks,
  eval_case_loader: ClipboardList,
  coverage_metric: Target,
  score_distribution_metric: BarChart3,
  diversity_metric: Shuffle,
  facts_coverage_metric: CircleCheck,
  eval_report: FileBarChart,
};

function getNodeIcon(typeId: string): LucideIcon {
  return NODE_ICONS[typeId] || Box;
}

interface NodeIconProps {
  typeId: string;
  className?: string;
  strokeWidth?: number;
}

/** Renders the icon for a node type. Goes through `createElement` rather than
 *  `const Icon = getNodeIcon(...)` + `<Icon/>` so the dynamic lookup doesn't
 *  read as "a component defined during render" to the lint rules. */
export function NodeIcon({ typeId, className, strokeWidth = 2 }: NodeIconProps) {
  return createElement(getNodeIcon(typeId), { className, strokeWidth });
}
