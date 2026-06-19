import { useCallback, useEffect, useState } from "react";
import type { NodeTypeDef } from "../types/pipeline";

/**
 * Single source of truth for node definitions: fetched from `/api/node-types`.
 * Module-level cache + in-flight promise so multiple components mount concurrently
 * trigger exactly one network call. No static mirror in the frontend — backend
 * changes (new params, renamed ports, new node types) show up automatically.
 */

let cache: NodeTypeDef[] | null = null;
let inFlight: Promise<NodeTypeDef[]> | null = null;
type Listener = (defs: NodeTypeDef[]) => void;
const listeners = new Set<Listener>();

function loadNodeTypes(): Promise<NodeTypeDef[]> {
  if (cache) return Promise.resolve(cache);
  if (inFlight) return inFlight;
  inFlight = fetch("/api/node-types")
    .then((r) => {
      if (!r.ok) throw new Error(`node-types fetch failed: ${r.status}`);
      return r.json();
    })
    .then((data: NodeTypeDef[]) => {
      cache = data;
      inFlight = null;
      listeners.forEach((fn) => fn(data));
      return data;
    })
    .catch((e) => {
      inFlight = null;
      throw e;
    });
  return inFlight;
}

export interface NodeTypesState {
  nodeTypes: NodeTypeDef[];
  byTypeId: Record<string, NodeTypeDef>;
  loading: boolean;
  error: string | null;
  /** Drop the cache and re-fetch — wired to the palette's retry button. */
  reload: () => void;
}

export function useNodeTypes(): NodeTypesState {
  const [defs, setDefs] = useState<NodeTypeDef[] | null>(cache);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (cache) {
      // cache may have filled between this component's render (useState(cache)
      // captured null) and the effect firing — sync it so defs isn't stuck null.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDefs(cache);
      return;
    }
    const listener: Listener = (data) => setDefs(data);
    listeners.add(listener);
    loadNodeTypes().catch((e) => setError(String(e)));
    return () => {
      listeners.delete(listener);
    };
  }, []);

  const reload = useCallback(() => {
    cache = null;
    inFlight = null;
    setError(null);
    setDefs(null);
    loadNodeTypes()
      .then((data) => setDefs(data))
      .catch((e) => setError(String(e)));
  }, []);

  const byTypeId: Record<string, NodeTypeDef> = {};
  (defs ?? []).forEach((d) => {
    byTypeId[d.typeId] = d;
  });

  return {
    nodeTypes: defs ?? [],
    byTypeId,
    loading: defs === null && error === null,
    error,
    reload,
  };
}
