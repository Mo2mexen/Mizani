import { useState } from "react";
import { api } from "../api";
import { minutes } from "../format";
import type { Colour, HandoverDraft, QueueEntry } from "../types";
import { EMPTY_VITALS, toVitals } from "../vitals";
import { VitalsFields } from "./VitalsFields";

type Props = {
  colours: Colour[];
  /** null until the API has answered once */
  queue: QueueEntry[] | null;
  onRetriage: (runId: string) => void;
};

/** One column per colour, cards in the order the API sends. The UI never reorders anything. */
export function QueueBoard({ colours, queue, onRetriage }: Props) {
  const isLoading = queue === null || colours.length === 0;
  return (
    <section className="panel queue" aria-labelledby="queue-title">
      <h2 id="queue-title" className="panel-title">
        <span className="step-no">3</span> Waiting room
        {queue && <span className="count">{queue.length} waiting</span>}
      </h2>
      {isLoading && <p className="empty">Loading the waiting room…</p>}
      {!isLoading && queue.length === 0 && (
        <p className="empty">Nobody is waiting. Saved patients appear here, in the colour a nurse approved.</p>
      )}
      {!isLoading && (
        <div className="queue-columns">
          {colours.map((c) => {
            const entries = queue.filter((e) => e.colour === c.colour);
            return (
              <div key={c.colour} className={`queue-col c-${c.colour}`}>
                <h3 className="queue-head">
                  <span className="swatch" aria-hidden="true" />
                  {c.colour.toUpperCase()} · {c.label}
                  <span className="count">{entries.length}</span>
                </h3>
                <p className="target">
                  {c.target_minutes === 0 ? "seen immediately" : `seen within ${minutes(c.target_minutes)}`}
                  <span className="src">{c.source}</span>
                </p>
                {entries.map((e) => (
                  <Card key={e.triage_id} entry={e} onRetriage={onRetriage} />
                ))}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function Card({ entry, onRetriage }: { entry: QueueEntry; onRetriage: (runId: string) => void }) {
  const [panel, setPanel] = useState<"none" | "recheck" | "handover">("none");
  const isOver = entry.minutes_since_triage > entry.target_minutes;
  const toggle = (next: "recheck" | "handover") => setPanel(panel === next ? "none" : next);
  return (
    <article className={`card${entry.recheck_due ? " card-due" : ""}`} aria-label={entry.patient_label}>
      <header className="card-head">
        <strong dir="auto">{entry.patient_label}</strong>
        {entry.recheck_due && (
          <span className="badge-due">RE-CHECK DUE{entry.recheck_raised ? " · flagged" : ""}</span>
        )}
      </header>
      <p className={`card-time${isOver ? " over" : ""}`}>
        waited {minutes(entry.waited_minutes)} · last triage {minutes(entry.minutes_since_triage)} ago · target{" "}
        {entry.target_minutes === 0 ? "immediately" : minutes(entry.target_minutes)}
      </p>
      <div className="card-actions">
        <button type="button" className="btn btn-small" aria-expanded={panel === "recheck"} onClick={() => toggle("recheck")}>
          Re-check
        </button>
        <button type="button" className="btn btn-small" aria-expanded={panel === "handover"} onClick={() => toggle("handover")}>
          Handover
        </button>
      </div>
      {panel === "recheck" && <Recheck entry={entry} onRetriage={onRetriage} />}
      {panel === "handover" && <Handover entry={entry} />}
    </article>
  );
}

function Recheck({ entry, onRetriage }: { entry: QueueEntry; onRetriage: (runId: string) => void }) {
  const [vitals, setVitals] = useState(EMPTY_VITALS);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (isBusy) return; // a double click starts one run
    setIsBusy(true);
    setError(null);
    try {
      const { triage_id: runId } = await api.retriage(entry.triage_id, toVitals(vitals));
      onRetriage(runId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not sent");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <form className="card-panel" onSubmit={handleSubmit} aria-label={`Re-check ${entry.patient_label}`}>
      <p className="hint">Fresh vitals only. The chart signs stay; a nurse confirms any new colour.</p>
      <VitalsFields value={vitals} onChange={setVitals} idPrefix={`recheck-${entry.triage_id}`} />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="btn btn-primary btn-small" disabled={isBusy}>
        {isBusy ? "Re-scoring…" : "Re-score"}
      </button>
    </form>
  );
}

function Handover({ entry }: { entry: QueueEntry }) {
  const [draft, setDraft] = useState<HandoverDraft | null>(null);
  const [nurse, setNurse] = useState("");
  const [released, setReleased] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameId = `handover-name-${entry.triage_id}`;

  async function run(work: () => Promise<void>) {
    setIsBusy(true);
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not sent");
    } finally {
      setIsBusy(false);
    }
  }

  function release(e: React.FormEvent) {
    e.preventDefault();
    if (!draft || !nurse.trim() || isBusy) return;
    void run(async () => {
      const r = await api.releaseHandover(entry.triage_id, draft.draft_id, nurse.trim());
      setReleased(r.approver);
    });
  }

  return (
    <div className="card-panel" aria-live="polite">
      {!draft && (
        <button
          type="button"
          className="btn btn-small"
          disabled={isBusy}
          onClick={() => run(async () => setDraft(await api.draftHandover(entry.triage_id)))}
        >
          {isBusy ? "The model is drafting…" : "Draft handover (model)"}
        </button>
      )}
      {draft && (
        <>
          <p className="hint">Draft for the doctor. Read it; it reaches the doctor only when you release it.</p>
          <p className="handover-text" dir="auto">
            {draft.text}
          </p>
          {released ? (
            <p className="ok">Released to the doctor by {released}</p>
          ) : (
            <form className="decision-row" onSubmit={release}>
              <label className="visually-hidden" htmlFor={nameId}>
                Your name
              </label>
              <input id={nameId} dir="auto" placeholder="your name" value={nurse} onChange={(e) => setNurse(e.target.value)} />
              <button type="submit" className="btn btn-primary" disabled={!nurse.trim() || isBusy}>
                {isBusy ? "Releasing…" : "Release to doctor"}
              </button>
            </form>
          )}
        </>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
