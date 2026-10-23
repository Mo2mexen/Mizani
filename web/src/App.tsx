import { useEffect, useState } from "react";
import { api } from "./api";
import { AuditDrawer } from "./components/AuditDrawer";
import { IntakeForm } from "./components/IntakeForm";
import { ProposalPanel } from "./components/ProposalPanel";
import { QueueBoard } from "./components/QueueBoard";
import { TracePanel } from "./components/TracePanel";
import { colourLabel } from "./format";
import type { Colour, QueueEntry, RunState } from "./types";
import { useRun } from "./useRun";

const QUEUE_REFRESH_MS = 5000; // the agent's re-check watch runs every 30 s on the server

// The open run lives in the address (#run=<id>), so a reload or a second tab picks it up again:
// the API replays the run's events, rebuilt from its checkpoint if the API restarted.
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

export function App() {
  const [colours, setColours] = useState<Colour[]>([]);
  const [queue, setQueue] = useState<QueueEntry[] | null>(null);
  const [runId, setRunId] = useState<string | null>(runFromAddress);
  const [complaint, setComplaint] = useState<string | null>(null);
  const [isShadow, setIsShadow] = useState(false);
  const [assessedFor, setAssessedFor] = useState<string | null>(null);
  const [isAuditOpen, setIsAuditOpen] = useState(false);
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

  async function refreshQueue() {
    try {
      setQueue(await api.queue());
      setOffline(null);
    } catch (err) {
      setOffline(err instanceof Error ? err.message : "API not reachable");
    }
  }

  // Colours come once, but keep asking until the API answers (it may still be starting).
  useEffect(() => {
    if (colours.length > 0) return;
    const load = () => api.colours().then(setColours, (err: unknown) => setOffline(String(err)));
    void load();
    const id = setInterval(load, QUEUE_REFRESH_MS);
    return () => clearInterval(id);
  }, [colours.length]);

  useEffect(() => {
    void refreshQueue();
    const id = setInterval(() => void refreshQueue(), QUEUE_REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <img className="brand-logo" src="/favicon.svg" alt="" width="30" height="30" />
          <h1 className="brand-name">Mizani</h1>
          <span className="brand-sub">Triage co-pilot for the adult SATS chart. The nurse decides.</span>
        </div>
        <label className="toggle" title="For training and audit: you choose a colour before Mizani's is shown, and the audit log counts how often you agree.">
          <input type="checkbox" checked={isShadow} onChange={(e) => setIsShadow(e.target.checked)} />
          Shadow mode <span className="toggle-note">(choose your colour first)</span>
        </label>
        <button type="button" className="btn btn-small" onClick={() => setIsAuditOpen(true)} aria-expanded={isAuditOpen}>
          Audit log
        </button>
      </header>
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
      />
      <TracePanel run={run} isMasked={isMasked} />
      <AuditDrawer isOpen={isAuditOpen} onClose={() => setIsAuditOpen(false)} />
      <footer className="foot">
        Synthetic patients only. Mizani suggests; a named nurse approves every priority. Every number shows the rule it
        came from. Mizani never diagnoses or prescribes.
      </footer>
    </div>
  );
}
