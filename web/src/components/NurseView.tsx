import { useEffect, useState } from "react";
import { api } from "../api";
import { colourLabel } from "../format";
import type { Colour, QueueEntry, RunState } from "../types";
import { useRun } from "../useRun";
import { IntakeForm } from "./IntakeForm";
import { ProposalPanel } from "./ProposalPanel";
import { QueueBoard } from "./QueueBoard";
import { TracePanel } from "./TracePanel";

const QUEUE_REFRESH_MS = 5000; // the agent's re-check watch runs every 30 s on the server

// The open run lives in the address (#run=<id>), so a reload, a second tab, or signing in again
// after the idle time-out picks it up: the API replays the run's events.
function runFromAddress(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get("run");
}

/** One sentence for screen readers when the run reaches a result. */
function announcement(run: RunState | null, colours: Colour[], isMasked: boolean): string {
  if (!run) return "";
  if (run.error) return `The agent stopped: ${run.error}`;
  if (run.committed) {
    const c = run.committed;
    return `Saved ${c.colour}, ${c.action === "override" ? "overridden" : "approved"} by ${c.approver}.`;
  }
  if (run.proposal && isMasked) return "Mizani's proposal is ready. Record your own colour first.";
  if (run.proposal) return `Proposal ready: ${run.proposal.colour}, ${colourLabel(colours, run.proposal.colour)}. Pending a nurse.`;
  return "";
}

/** The triage desk: 1 intake, 2 proposal and the nurse's decision, 3 the waiting room, and the agent's steps. */
export function NurseView({ colours, isShadow }: { colours: Colour[]; isShadow: boolean }) {
  const [queue, setQueue] = useState<QueueEntry[] | null>(null);
  const [runId, setRunId] = useState<string | null>(runFromAddress);
  const [complaint, setComplaint] = useState<string | null>(null);
  const [assessedFor, setAssessedFor] = useState<string | null>(null);
  const [offline, setOffline] = useState<string | null>(null);
  const [intakeKey, setIntakeKey] = useState(0); // a new key empties the intake for the next patient
  const run = useRun(runId);
  // Shadow mode: Mizani's answer stays hidden until the nurse has recorded their own colour.
  const isMasked = isShadow && !run?.committed && assessedFor !== run?.proposal?.triage_id;

  function openRun(id: string, text: string | null) {
    setRunId(id);
    setComplaint(text);
    window.history.replaceState(null, "", `#run=${id}`);
    // On a narrow screen the proposal sits under the intake: bring it into view (no-op on a desk).
    requestAnimationFrame(() => document.getElementById("proposal-title")?.scrollIntoView({ block: "nearest" }));
  }

  function nextPatient() {
    setRunId(null);
    setComplaint(null);
    window.history.replaceState(null, "", window.location.pathname);
    setIntakeKey((k) => k + 1);
    requestAnimationFrame(() => document.getElementById("patient-label")?.focus());
  }

  function refreshQueue() {
    return api.queue().then(
      (rows) => {
        setQueue(rows);
        setOffline(null);
      },
      (err: unknown) => setOffline(err instanceof Error ? err.message : "API not reachable"),
    );
  }

  useEffect(() => {
    void refreshQueue();
    const id = setInterval(() => void refreshQueue(), QUEUE_REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  return (
    <>
      {offline && (
        <p className="banner" role="alert">
          The API is not reachable ({offline}). It may still be starting; this page retries every few seconds. If it
          stays, start it with <code>docker compose up</code>.
        </p>
      )}
      <p className="visually-hidden" aria-live="polite" aria-atomic="true">
        {announcement(run, colours, isMasked)}
      </p>
      <main className="workspace">
        <IntakeForm key={intakeKey} isFirst={intakeKey === 0} isReady={colours.length > 0} onStarted={openRun} />
        <ProposalPanel
          run={run}
          complaint={complaint}
          colours={colours}
          isShadow={isShadow}
          assessedFor={assessedFor}
          onAssessed={setAssessedFor}
          onCommitted={() => void refreshQueue()}
          onNextPatient={nextPatient}
        />
      </main>
      <QueueBoard
        colours={colours}
        queue={queue}
        onRetriage={(id) => openRun(id, null)}
        onCalledIn={() => void refreshQueue()}
      />
      <TracePanel run={run} isMasked={isMasked} />
    </>
  );
}
