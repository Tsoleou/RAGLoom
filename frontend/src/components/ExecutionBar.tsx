import { useRef, useState } from "react";

interface Props {
  isRunning: boolean;
  nodeCount: number;
  edgeCount: number;
  onRun: () => void;
  onCancel: () => void;
  onClear: () => void;
  onSaveProfile: (name: string) => Promise<void>;
  profiles: Record<string, unknown>;
  onLoadProfile: (name: string) => void;
  activeProfile: string;
  onActivateProfile: (name: string) => Promise<void>;
  canRunBatch: boolean;
  onRunBatch: () => void;
}

export function ExecutionBar({
  isRunning,
  nodeCount,
  edgeCount,
  onRun,
  onCancel,
  onClear,
  onSaveProfile,
  profiles,
  onLoadProfile,
  activeProfile,
  onActivateProfile,
  canRunBatch,
  onRunBatch,
}: Props) {
  const [saving, setSaving] = useState(false);
  const [profileName, setProfileName] = useState("");
  const [showInput, setShowInput] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showLoadMenu, setShowLoadMenu] = useState(false);
  const [showActivateMenu, setShowActivateMenu] = useState(false);
  const [activating, setActivating] = useState(false);
  const loadMenuRef = useRef<HTMLDivElement>(null);

  async function handleActivate(name: string) {
    setActivating(true);
    await onActivateProfile(name);
    setActivating(false);
    setShowActivateMenu(false);
  }

  async function handleSave() {
    const name = profileName.trim();
    if (!name) return;
    setSaving(true);
    await onSaveProfile(name);
    setSaving(false);
    setShowInput(false);
    setProfileName("");
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  return (
    <div className="h-12 bg-[#202020] border-b border-[#2a2a2a] px-4 flex items-center justify-between">
      <div className="flex items-center gap-3">
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

        {/* Clear canvas */}
        <button
          onClick={onClear}
          disabled={isRunning}
          className="px-3 py-1.5 text-xs font-medium rounded-md border border-[#333] text-[#888] hover:bg-[#2a2a2a] hover:text-[#aaa] disabled:opacity-40 transition-colors"
        >
          Clear
        </button>

        {/* Run Batch Eval (visible when current graph carries an eval_case_loader) */}
        {canRunBatch && (
          <button
            onClick={onRunBatch}
            disabled={isRunning}
            title="Run this graph against multiple golden-set cases"
            className="px-3 py-1.5 text-xs font-medium rounded-md border border-[#a070d0]/40 text-[#a070d0] hover:bg-[#a070d0]/10 hover:border-[#a070d0] disabled:opacity-40 transition-colors"
          >
            Run Batch ▸
          </button>
        )}

        {/* Load Profile */}
        <div className="relative border-l border-[#2a2a2a] pl-3" ref={loadMenuRef}>
          <button
            onClick={() => setShowLoadMenu((v) => !v)}
            disabled={isRunning || Object.keys(profiles).length === 0}
            className="px-3 py-1.5 text-xs font-medium rounded-md border border-[#00ccaa]/30 text-[#00ccaa]/70 hover:bg-[#00ccaa]/10 hover:border-[#00ccaa] hover:text-[#00ccaa] disabled:opacity-40 transition-colors"
          >
            Load Profile ▾
          </button>
          {showLoadMenu && (
            <div className="absolute top-full mt-1 left-0 z-50 bg-[#1a1a1a] border border-[#2a2a2a] rounded-md shadow-lg min-w-[140px] py-1">
              {Object.keys(profiles).map((name) => (
                <button
                  key={name}
                  onClick={() => { onLoadProfile(name); setShowLoadMenu(false); }}
                  className="w-full text-left px-3 py-1.5 text-xs text-[#888] hover:bg-[#00ccaa]/10 hover:text-[#00ccaa] transition-colors"
                >
                  {name}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Set Live — activate which profile the customer-facing chat presents */}
        <div className="relative border-l border-[#2a2a2a] pl-3">
          <button
            onClick={() => setShowActivateMenu((v) => !v)}
            disabled={isRunning || activating || Object.keys(profiles).length === 0}
            title="Choose which profile the customer chat (/) presents"
            className="px-3 py-1.5 text-xs font-medium rounded-md border border-[#e07830]/40 text-[#e07830] hover:bg-[#e07830]/10 hover:border-[#e07830] disabled:opacity-40 transition-colors"
          >
            {activating ? "..." : `Live: ${activeProfile} ▾`}
          </button>
          {showActivateMenu && (
            <div className="absolute top-full mt-1 left-0 z-50 bg-[#1a1a1a] border border-[#2a2a2a] rounded-md shadow-lg min-w-[160px] py-1">
              {Object.keys(profiles).map((name) => (
                <button
                  key={name}
                  onClick={() => handleActivate(name)}
                  className={`w-full text-left px-3 py-1.5 text-xs flex items-center justify-between transition-colors ${
                    name === activeProfile
                      ? "text-[#00ccaa]"
                      : "text-[#888] hover:bg-[#e07830]/10 hover:text-[#e07830]"
                  }`}
                >
                  <span className="truncate">{name}</span>
                  {name === activeProfile && <span className="ml-2 flex-shrink-0">✓</span>}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Save as Profile */}
        <div className="flex items-center gap-2 border-l border-[#2a2a2a] pl-3">
          {showInput ? (
            <>
              <input
                autoFocus
                value={profileName}
                onChange={(e) => setProfileName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleSave();
                  if (e.key === "Escape") { setShowInput(false); setProfileName(""); }
                }}
                placeholder="Profile name..."
                className="bg-[#1a1a1a] border border-[#00ccaa]/40 text-[#e0e0e0] text-xs px-2 py-1 rounded w-32 focus:outline-none focus:border-[#00ccaa]"
              />
              <button
                onClick={handleSave}
                disabled={saving || !profileName.trim()}
                className="px-2 py-1 text-xs rounded bg-[#00ccaa]/10 border border-[#00ccaa]/40 text-[#00ccaa] hover:bg-[#00ccaa]/20 disabled:opacity-40 transition-colors"
              >
                {saving ? "..." : "Save"}
              </button>
              <button
                onClick={() => { setShowInput(false); setProfileName(""); }}
                className="text-[#555] hover:text-[#888] text-xs transition-colors"
              >
                ✕
              </button>
            </>
          ) : (
            <button
              onClick={() => setShowInput(true)}
              disabled={isRunning}
              className="px-3 py-1.5 text-xs font-medium rounded-md border border-[#00ccaa]/30 text-[#00ccaa]/70 hover:bg-[#00ccaa]/10 hover:border-[#00ccaa] hover:text-[#00ccaa] disabled:opacity-40 transition-colors"
            >
              {saved ? "✓ Saved" : "Save as Profile"}
            </button>
          )}
        </div>

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
