interface Props {
  isRunning: boolean;
  nodeCount: number;
  edgeCount: number;
  onRun: () => void;
  onCancel: () => void;
  onClear: () => void;
  onLoadDefault: () => void;
}

export function ExecutionBar({
  isRunning,
  nodeCount,
  edgeCount,
  onRun,
  onCancel,
  onClear,
  onLoadDefault,
}: Props) {
  return (
    <div className="h-12 bg-[#202020] border-b border-[#2a2a2a] px-4 flex items-center justify-between">
      <div className="flex items-center gap-4">
        {/* Run / Cancel */}
        {isRunning ? (
          <button
            onClick={onCancel}
            className="px-4 py-1.5 text-xs font-medium rounded-md bg-red-700 text-red-100 hover:bg-red-600 transition-colors"
          >
            Cancel
          </button>
        ) : (
          <button
            onClick={onRun}
            disabled={nodeCount === 0}
            className="px-4 py-1.5 text-xs font-medium rounded-md bg-[#e07830] text-white hover:bg-[#c06828] disabled:bg-[#333] disabled:text-[#555] disabled:cursor-not-allowed transition-colors"
          >
            Run Pipeline
          </button>
        )}

        {/* Load default pipeline */}
        <button
          onClick={onLoadDefault}
          disabled={isRunning}
          className="px-3 py-1.5 text-xs font-medium rounded-md border border-[#333] text-[#888] hover:bg-[#2a2a2a] hover:text-[#aaa] disabled:opacity-40 transition-colors"
        >
          Load Default
        </button>

        {/* Clear canvas */}
        <button
          onClick={onClear}
          disabled={isRunning}
          className="px-3 py-1.5 text-xs font-medium rounded-md border border-[#333] text-[#888] hover:bg-[#2a2a2a] hover:text-[#aaa] disabled:opacity-40 transition-colors"
        >
          Clear
        </button>

        {/* Status indicator */}
        {isRunning && (
          <div className="flex items-center gap-2 text-xs text-[#e07830]">
            <div className="w-2 h-2 rounded-full bg-[#e07830] animate-pulse" />
            Running...
          </div>
        )}
      </div>

      {/* Stats */}
      <div className="text-[10px] text-[#555]">
        {nodeCount} nodes / {edgeCount} edges
      </div>
    </div>
  );
}
