import { useEffect, useState } from "react";
import { api } from "../api";
import { clock, colourLabel, minutes } from "../format";
import type { Colour, InboxEntry, QueueEntry } from "../types";
import { Details } from "./QueueBoard";

const REFRESH_MS = 5000;

/** The doctor's screen: who is waiting, most urgent first, and the handovers nurses released.
 *  A doctor never changes a colour or approves a triage; "Seeing this patient" only records that
 *  the patient went in, which takes them off the waiting list. */
export function DoctorView({ colours }: { colours: Colour[] }) {
  const [queue, setQueue] = useState<QueueEntry[] | null>(null);
  const [inbox, setInbox] = useState<InboxEntry[] | null>(null);
  const [offline, setOffline] = useState<string | null>(null);
  const hasInbox = inbox !== null && inbox.length > 0;

  function refresh() {
    return Promise.all([api.queue(), api.handovers()]).then(
      ([rows, released]) => {
        setQueue(rows);
        setInbox(released);
        setOffline(null);
      },
      (err: unknown) => setOffline(err instanceof Error ? err.message : "API not reachable"),
    );
  }

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  return (
    <>
      {offline && (
        <p className="banner" role="alert">
          The waiting room could not be loaded ({offline}). This page tries again every few seconds.
        </p>
      )}
      <main className={hasInbox ? "doctor" : "doctor is-solo"}>
        <section className="panel" aria-labelledby="dr-queue-title">
          <h2 id="dr-queue-title" className="panel-title">
            Waiting room
            {queue && <span className="count">{queue.length} waiting</span>}
          </h2>
          <p className="hint">
            Most urgent first, then the longest wait. The colours are the ones nurses approved.
            {inbox?.length === 0 && " No handovers from nurses yet: they appear beside this list when a nurse releases one."}
          </p>
          {queue === null && <p className="empty">Loading the waiting room…</p>}
          {queue?.length === 0 && <p className="empty">Nobody is waiting. Patients appear here once a nurse saves their colour.</p>}
          {queue && queue.length > 0 && (
            <ol className="dr-list">
              {queue.map((entry) => (
                <WaitingPatient key={entry.triage_id} entry={entry} colours={colours} onSeen={() => void refresh()} />
              ))}
            </ol>
          )}
        </section>
        {hasInbox && (
          <section className="panel" aria-labelledby="inbox-title">
            <h2 id="inbox-title" className="panel-title">
              Handovers from nurses
              <span className="count">{inbox.length}</span>
            </h2>
            <ul className="inbox">
              {inbox.map((h) => (
                <li key={h.triage_id} className={`inbox-item c-${h.colour}`}>
                  <p className="inbox-head">
                    <strong dir="auto">{h.patient_label}</strong>
                    <span className="dr-colour">
                      {h.colour.toUpperCase()} · {colourLabel(colours, h.colour)}
                    </span>
                  </p>
                  <p className="handover-text" dir="auto">
                    {h.text}
                  </p>
                  <p className="card-time">
                    Released by {h.released_by} at {clock(h.released_at)}. Waiting {minutes(h.waited_minutes)}.
                  </p>
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>
    </>
  );
}

function WaitingPatient({ entry, colours, onSeen }: { entry: QueueEntry; colours: Colour[]; onSeen: () => void }) {
  const [panel, setPanel] = useState<"details" | "seeing" | "none">("none");
  const toggle = (next: "details" | "seeing") => setPanel(panel === next ? "none" : next);
  const isLate = entry.waited_minutes > entry.target_minutes;
  const target = entry.target_minutes === 0 ? "immediately" : `within ${minutes(entry.target_minutes)}`;
  return (
    <li className={`dr-row c-${entry.colour}`}>
      <div className="dr-row-head">
        <strong dir="auto">{entry.patient_label}</strong>
        <span className="dr-colour">
          {entry.colour.toUpperCase()} · {colourLabel(colours, entry.colour)}
        </span>
        {entry.recheck_due && <span className="badge-due">RE-CHECK DUE</span>}
      </div>
      <p className={`card-time${isLate ? " over" : ""}`}>
        Waited {minutes(entry.waited_minutes)}; to be seen {target}
        {isLate ? ": past the target" : ""}
      </p>
      <div className="card-actions">
        <button type="button" className="btn btn-small" aria-expanded={panel === "details"} onClick={() => toggle("details")}>
          Details
        </button>
        <button type="button" className="btn btn-small" aria-expanded={panel === "seeing"} onClick={() => toggle("seeing")}>
          Seeing this patient
        </button>
      </div>
      {panel === "details" && <Details entry={entry} />}
      {panel === "seeing" && <Seeing entry={entry} onDone={onSeen} onCancel={() => setPanel("none")} />}
    </li>
  );
}

/** Confirms before taking the patient off the waiting list, under the doctor's own name. */
function Seeing({ entry, onDone, onCancel }: { entry: QueueEntry; onDone: () => void; onCancel: () => void }) {
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setIsBusy(true);
    setError(null);
    try {
      await api.calledIn(entry.triage_id);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not saved");
      setIsBusy(false);
    }
  }

  return (
    <div className="card-panel" aria-live="polite">
      <p className="hint">
        {entry.patient_label} leaves the waiting room, recorded under your name. The record and the audit log stay.
      </p>
      <div className="decision-row">
        <button type="button" className="btn btn-primary btn-small" disabled={isBusy} onClick={() => void confirm()}>
          {isBusy ? "Saving…" : "Record that I am seeing them"}
        </button>
        <button type="button" className="btn btn-small" disabled={isBusy} onClick={onCancel}>
          Cancel
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
