import { useEffect, useState } from "react";
import { api } from "../api";
import { clock, FIELD_LABELS, minutes } from "../format";
import type { Colour, HandoverDraft, PatientRecord, QueueEntry } from "../types";
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
  const [search, setSearch] = useState("");
  const isLoading = queue === null || colours.length === 0;
  // Search only hides cards that don't match the label; the order is still the API's.
  const needle = search.trim().toLowerCase();
  const visible = (queue ?? []).filter((e) => !needle || e.patient_label.toLowerCase().includes(needle));
  return (
    <section className="panel queue" aria-labelledby="queue-title">
      <h2 id="queue-title" className="panel-title">
        <span className="step-no">3</span> Waiting room
        {queue && <span className="count">{queue.length} waiting</span>}
        {queue && queue.length > 0 && (
          <input
            type="search"
            className="queue-search"
            aria-label="Find a patient by label"
            placeholder="Find a patient"
            dir="auto"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        )}
      </h2>
      {needle && (
        <p className="hint" role="status">
          {visible.length} of {queue?.length ?? 0} match "{search.trim()}".{" "}
          <button type="button" className="btn btn-small" onClick={() => setSearch("")}>
            Clear
          </button>
        </p>
      )}
      {isLoading && <p className="empty">Loading the waiting room…</p>}
      {!isLoading && queue.length === 0 && (
        <p className="empty">Nobody is waiting. Saved patients appear here, in the colour a nurse approved.</p>
      )}
      {!isLoading && (
        <div className="queue-columns">
          {colours.map((c) => {
            const entries = visible.filter((e) => e.colour === c.colour);
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
                <div className="queue-cards">
                  {entries.map((e) => (
                    <Card key={e.triage_id} entry={e} onRetriage={onRetriage} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function Card({ entry, onRetriage }: { entry: QueueEntry; onRetriage: (runId: string) => void }) {
  const [panel, setPanel] = useState<"none" | "details" | "recheck" | "handover">("none");
  const isOver = entry.minutes_since_triage > entry.target_minutes;
  const toggle = (next: "details" | "recheck" | "handover") => setPanel(panel === next ? "none" : next);
  return (
    <article className={`card${entry.recheck_due ? " card-due" : ""}`} aria-label={entry.patient_label}>
      <header className="card-head">
        <button
          type="button"
          className="card-name"
          dir="auto"
          aria-expanded={panel === "details"}
          title="Show what was recorded for this patient"
          onClick={() => toggle("details")}
        >
          {entry.patient_label}
        </button>
        {entry.recheck_due && (
          <span className="badge-due">RE-CHECK DUE{entry.recheck_raised ? " · flagged" : ""}</span>
        )}
      </header>
      <p className={`card-time${isOver ? " over" : ""}`}>
        waited {minutes(entry.waited_minutes)} · last triage {minutes(entry.minutes_since_triage)} ago · target{" "}
        {entry.target_minutes === 0 ? "immediately" : minutes(entry.target_minutes)}
      </p>
      <div className="card-actions">
        <button type="button" className="btn btn-small" aria-expanded={panel === "details"} onClick={() => toggle("details")}>
          Details
        </button>
        <button type="button" className="btn btn-small" aria-expanded={panel === "recheck"} onClick={() => toggle("recheck")}>
          Re-check
        </button>
        <button type="button" className="btn btn-small" aria-expanded={panel === "handover"} onClick={() => toggle("handover")}>
          Handover
        </button>
      </div>
      {panel === "details" && <Details entry={entry} />}
      {panel === "recheck" && <Recheck entry={entry} onRetriage={onRetriage} />}
      {panel === "handover" && <Handover entry={entry} />}
    </article>
  );
}

const SHOWN: Record<string, string> = {
  alert: "alert",
  confused: "confused",
  voice: "responds to voice",
  pain: "responds to pain",
  unresponsive: "unresponsive",
  walking: "walking",
  with_help: "walks with help",
  stretcher: "stretcher / immobile",
};
const UNITS: Record<string, string> = {
  respiratory_rate: "/min",
  heart_rate: "/min",
  systolic_bp: " mmHg",
  temperature: " °C",
  spo2: " %",
  glucose: " mmol/L",
};

function shown(field: string, value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "yes" : "no";
  return typeof value === "string" ? (SHOWN[value] ?? value) : `${value}${UNITS[field] ?? ""}`;
}

/** What was recorded for a saved patient: read-only, straight from the stored record. */
function Details({ entry }: { entry: QueueEntry }) {
  const [record, setRecord] = useState<PatientRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.record(entry.triage_id).then(
      (r) => setRecord(r.triage),
      (err: unknown) => setError(err instanceof Error ? err.message : "Not loaded"),
    );
  }, [entry.triage_id]);

  if (error) {
    return (
      <div className="card-panel">
        <p className="error" role="alert">
          {error}
        </p>
      </div>
    );
  }
  if (!record) {
    return (
      <div className="card-panel">
        <p className="empty">Loading the record…</p>
      </div>
    );
  }
  const measured = Object.entries(record.vitals).filter(([, v]) => v !== null && v !== undefined) as [
    string,
    string | number | boolean,
  ][];
  const missing = Object.keys(record.vitals).filter((k) => record.vitals[k] === null || record.vitals[k] === undefined);
  const decision = record.decision;
  return (
    <div className="card-panel details">
      <h4 className="details-title">Complaint</h4>
      <blockquote className="complaint" dir="auto">
        {record.complaint}
      </blockquote>
      <h4 className="details-title">Vitals · TEWS {record.proposal.tews.total}</h4>
      {measured.length === 0 && <p className="empty">No vitals were measured.</p>}
      {measured.length > 0 && (
        <dl className="details-vitals">
          {measured.map(([k, v]) => (
            <div key={k}>
              <dt>{FIELD_LABELS[k] ?? k}</dt>
              <dd>{shown(k, v)}</dd>
            </div>
          ))}
        </dl>
      )}
      {missing.length > 0 && (
        <p className="hint">Not measured: {missing.map((k) => FIELD_LABELS[k] ?? k).join(", ")}.</p>
      )}
      <h4 className="details-title">Chart signs</h4>
      {record.proposal.discriminators.length === 0 && <p className="empty">None; the vitals set the colour.</p>}
      <ul className="signs">
        {record.proposal.discriminators.map((d) => (
          <li key={d.name}>
            <span className={`chip c-${d.colour}`}>{d.colour.toUpperCase()}</span> {d.name}
            {d.quote && (
              <q className="quote" dir="auto">
                {d.quote}
              </q>
            )}
          </li>
        ))}
      </ul>
      <h4 className="details-title">Decision</h4>
      <p className="details-decision">
        {decision?.action === "override" ? (
          <>
            Mizani proposed {record.proposal.colour.toUpperCase()}; <strong>{record.approver}</strong> overrode it to{" "}
            {record.colour.toUpperCase()}
            {decision.reason ? ` ("${decision.reason}")` : ""}.
          </>
        ) : (
          <>
            {record.colour.toUpperCase()}, approved by <strong>{record.approver}</strong>.
          </>
        )}{" "}
        Arrived {clock(record.arrived_at)}
        {record.committed_at ? `, saved ${clock(record.committed_at)}` : ""}
        {record.retriage_of ? "; this is a re-check of an earlier triage" : ""}.
      </p>
    </div>
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
