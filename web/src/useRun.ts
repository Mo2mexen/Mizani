import { useEffect, useState } from "react";
import { openStream } from "./api";
import type { Committed, Proposal, RunState, TraceStep } from "./types";

function fresh(runId: string): RunState {
  return { runId, steps: [], proposal: null, commitError: null, committed: null, error: null };
}

/** Follows one agent run's event stream: the steps as they happen, the proposal, the end. */
export function useRun(runId: string | null): RunState | null {
  const [state, setState] = useState<RunState | null>(null);

  useEffect(() => {
    if (!runId) return;
    // Every update starts from this run's state, so events of an older run never mix in.
    const update = (change: (s: RunState) => RunState) =>
      setState((s) => change(s && s.runId === runId ? s : fresh(runId)));
    return openStream(runId, {
      step: (step: TraceStep) => update((s) => ({ ...s, steps: [...s.steps, step] })),
      proposal: (data: { proposal: Proposal; error: string | null }) =>
        update((s) => ({ ...s, proposal: data.proposal, commitError: data.error })),
      committed: (committed: Committed) => update((s) => ({ ...s, committed })),
      error: (data: { message: string }) => update((s) => ({ ...s, error: data.message })),
    });
  }, [runId]);

  if (!runId) return null;
  return state && state.runId === runId ? state : fresh(runId);
}
