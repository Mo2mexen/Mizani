import { useEffect, useState } from "react";
import { api } from "./api";
import { AuditDrawer } from "./components/AuditDrawer";
import { IntakeForm } from "./components/IntakeForm";
import { ProposalPanel } from "./components/ProposalPanel";
import { QueueBoard } from "./components/QueueBoard";
import { TracePanel } from "./components/TracePanel";
import type { Colour, QueueEntry } from "./types";
import { useRun } from "./useRun";

const QUEUE_REFRESH_MS = 5000; // the agent's re-check watch runs every 30 s on the server

export function App() {
  const [colours, setColours] = useState<Colour[]>([]);
  const [queue, setQueue] = useState<QueueEntry[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [complaint, setComplaint] = useState<string | null>(null);
  const [isShadow, setIsShadow] = useState(false);
  const [assessedFor, setAssessedFor] = useState<string | null>(null);
  const [isAuditOpen, setIsAuditOpen] = useState(false);
  const [offline, setOffline] = useState<string | null>(null);
  const run = useRun(runId);

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
          <span className="brand-mark" aria-hidden="true">
            ⚖
          </span>
          <span className="brand-name">Mizani</span>
          <span className="brand-sub">triage co-pilot · SATS adult · the nurse decides</span>
        </div>
        <label className="toggle">
          <input type="checkbox" checked={isShadow} onChange={(e) => setIsShadow(e.target.checked)} />
          Shadow mode
        </label>
        <button type="button" className="btn btn-small" onClick={() => setIsAuditOpen(true)}>
          Audit log
        </button>
      </header>
      {offline && (
        <p className="banner" role="alert">
          API not reachable ({offline}). Start it with <code>docker compose up</code> or <code>uv run mizani-api</code>.
        </p>
      )}
      <main className="workspace">
        <IntakeForm
          onStarted={(id, text) => {
            setRunId(id);
            setComplaint(text);
          }}
        />
        <div className="middle">
          <TracePanel run={run} isHidden={isShadow && !run?.committed && assessedFor !== run?.proposal?.triage_id} />
          {run && (
            <ProposalPanel
              run={run}
              complaint={complaint}
              colours={colours}
              isShadow={isShadow}
              assessedFor={assessedFor}
              onAssessed={setAssessedFor}
              onCommitted={() => void refreshQueue()}
            />
          )}
        </div>
      </main>
      <QueueBoard
        colours={colours}
        queue={queue}
        onRetriage={(id) => {
          setRunId(id);
          setComplaint(null);
        }}
      />
      <AuditDrawer isOpen={isAuditOpen} onClose={() => setIsAuditOpen(false)} />
      <footer className="foot">
        Synthetic patients only. Mizani suggests; a named nurse approves every priority. Every number shows the rule it came from.
      </footer>
    </div>
  );
}
