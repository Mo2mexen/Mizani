import { useEffect, useState } from "react";
import { colourLabel, FIELD_LABELS, FLAG_LABELS } from "../format";
import type { Colour, Proposal, RunState } from "../types";
import { DecisionForm } from "./DecisionForm";
import { ShadowFirst } from "./ShadowFirst";

type Props = {
  run: RunState | null;
  complaint: string | null;
  colours: Colour[];
  isShadow: boolean;
  /** Shadow mode: the proposal (triage id) the nurse already gave her own colour for. */
  assessedFor: string | null;
  onAssessed: (triageId: string) => void;
  onCommitted: () => void;
  onNextPatient: () => void;
};

const SLOW_SECONDS = 20;
// What the agent is doing now, from the last step it finished (the full list is in the trace).
const NEXT_STEP: Record<string, string> = {
  extract: "Checking the vitals.",
  fallback: "Checking the vitals.",
  validate_vitals: "Scoring the vitals (TEWS).",
  compute_tews: "Matching the complaint to chart signs.",
  match_discriminators: "Building the proposal.",
  propose_priority: "Building the proposal.",
}; // past this, say why the first patient after a start is slow

/** The nurse's screen: the proposal with every source, then approve, override or re-measure. */
export function ProposalPanel({
  run,
  complaint,
  colours,
  isShadow,
  assessedFor,
  onAssessed,
  onCommitted,
  onNextPatient,
}: Props) {
  if (!run) {
    return (
      <Shell>
        <p className="empty">
          No patient yet. Fill in the intake: the proposed colour appears here first, with the rule behind every number,
          and nothing is saved until you approve or override it.
        </p>
      </Shell>
    );
  }
  if (run.error) {
    return (
      <Shell>
        <p className="error" role="alert">
          {run.error}
        </p>
        <p className="hint">Nothing was saved for this patient.</p>
      </Shell>
    );
  }
  if (run.committed) return <CommittedView run={run} colours={colours} onNextPatient={onNextPatient} />;
  const proposal = run.proposal;
  if (!proposal) return <AgentWorking key={run.runId} run={run} />;
  // One blind assessment per proposal: a re-measure makes a new proposal, so she is asked again.
  if (isShadow && assessedFor !== proposal.triage_id) {
    return <ShadowFirst runId={run.runId} colours={colours} onDone={() => onAssessed(proposal.triage_id)} />;
  }
  const toConfirm = proposal.flags.filter((f) => f.kind === "confirm_sign");
  // After a reload the intake text is gone from the page; the propose step still carries it.
  const proposed = run.steps.find((s) => s.name === "propose_priority")?.input as { complaint?: string } | undefined;
  const shownComplaint = complaint ?? proposed?.complaint ?? null;
  return (
    <Shell pending>
      <ColourBand proposal={proposal} colours={colours} />
      {toConfirm.length > 0 && (
        <div className="confirm" role="note" aria-label="Nurse to confirm">
          <p className="confirm-title">Nurse to confirm before approving</p>
          <ul>
            {toConfirm.map((f, i) => (
              <li key={i}>
                {f.message}
                <span className="src">{f.source}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {shownComplaint && (
        <blockquote className="complaint" dir="auto">
          {shownComplaint}
        </blockquote>
      )}
      {run.isReconnecting && <Reconnecting />}
      <Breakdown proposal={proposal} />
      {run.commitError && (
        <p className="error" role="alert">
          Not saved: {run.commitError}
        </p>
      )}
      <DecisionForm
        key={proposal.triage_id}
        run={run}
        proposal={proposal}
        colours={colours}
        needsConfirm={toConfirm.length > 0}
        onCommitted={onCommitted}
      />
    </Shell>
  );
}

function Shell({ pending, children }: { pending?: boolean; children: React.ReactNode }) {
  return (
    <section className="panel proposal" aria-labelledby="proposal-title">
      <h2 id="proposal-title" className="panel-title">
        <span className="step-no">2</span> Proposal {pending && <span className="pending">pending: not saved</span>}
      </h2>
      {children}
    </section>
  );
}

function Reconnecting() {
  return (
    <p className="warning" role="status">
      Lost the connection to the API; retrying. Saved patients are safe. If the API restarted before this proposal
      reached you, the page will say so; then start the intake again.
    </p>
  );
}

function AgentWorking({ run }: { run: RunState }) {
  const [started] = useState(() => Date.now());
  const [now, setNow] = useState(started);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const seconds = Math.floor((now - started) / 1000);
  const last = run.steps[run.steps.length - 1]?.name;
  const doing = last ? (NEXT_STEP[last] ?? "Working…") : "The model is reading the complaint.";
  return (
    <Shell>
      <div className="working" role="status">
        <span className="working-dot" aria-hidden="true" />
        <p>
          <strong>The agent is working</strong> · {seconds} s
          <br />
          {doing}
        </p>
      </div>
      {run.isReconnecting && <Reconnecting />}
      {seconds >= SLOW_SECONDS && (
        <p className="hint">
          The model may still be loading: the first patient after a start can take a minute or two. Later patients are
          faster.
        </p>
      )}
    </Shell>
  );
}

function ColourBand({ proposal, colours }: { proposal: Proposal; colours: Colour[] }) {
  return (
    <div className={`colour-band c-${proposal.colour}`}>
      <p className="colour-name">
        <span className="swatch" aria-hidden="true" />
        {proposal.colour.toUpperCase()} <span className="colour-label">{colourLabel(colours, proposal.colour)}</span>
      </p>
      <p className="colour-set">{setBy(proposal)}</p>
      <p className="colour-why src">
        <span className="src-tag">rule</span> {proposal.source}
      </p>
    </div>
  );
}

/** The plain-language reason, read from the proposal's own fields; the full rule is printed under it. */
function setBy(proposal: Proposal): string {
  const sign = proposal.discriminators.find((d) => d.colour === proposal.colour);
  if (sign) return `Set by the chart sign "${sign.name}"`;
  return `Set by the vital signs: TEWS ${proposal.tews.total}`;
}

function Breakdown({ proposal }: { proposal: Proposal }) {
  const flags = groupRemeasure(proposal.flags.filter((f) => f.kind !== "confirm_sign"));
  const hasVitals = proposal.tews.components.length > 0;
  return (
    <div className="breakdown">
      {!hasVitals && (
        <div>
          <h3 className="sub-title">
            TEWS <strong>{proposal.tews.total}</strong>
          </h3>
          <p className="empty">No vitals were measured, so the vitals score nothing yet. Measure them and re-score.</p>
        </div>
      )}
      {hasVitals && (
      <table className="tews">
        <caption>
          TEWS <strong>{proposal.tews.total}</strong>
        </caption>
        <thead>
          <tr>
            <th scope="col">Vital</th>
            <th scope="col" className="num">
              Value
            </th>
            <th scope="col" className="num">
              Points
            </th>
            <th scope="col">Source</th>
          </tr>
        </thead>
        <tbody>
          {proposal.tews.components.map((c) => (
            <tr key={c.field}>
              <th scope="row">{FIELD_LABELS[c.field] ?? c.field}</th>
              <td className="num">{String(c.value)}</td>
              <td className="num">{c.points}</td>
              <td className="src">{c.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
      )}
      <div>
        <h3 className="sub-title">Chart signs</h3>
        {proposal.discriminators.length === 0 && <p className="empty">None applies. The vitals set the colour.</p>}
        <ul className="signs">
          {proposal.discriminators.map((d) => (
            <li key={d.name}>
              <span className={`chip c-${d.colour}`}>{d.colour.toUpperCase()}</span> {d.name}
              {d.quote && (
                <q className="quote" dir="auto">
                  {d.quote}
                </q>
              )}
              <span className="src">{d.source}</span>
            </li>
          ))}
        </ul>
        {flags.length > 0 && (
          <>
            <h3 className="sub-title">Flags</h3>
            <ul className="flags">
              {flags.map((f, i) => (
                <li key={`${f.kind}-${i}`} className={`flag flag-${f.kind}`}>
                  <span className="flag-kind">{FLAG_LABELS[f.kind] ?? f.kind}</span> <span dir="auto">{f.message}</span>
                  <span className="src">{f.source}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

function CommittedView({ run, colours, onNextPatient }: { run: RunState; colours: Colour[]; onNextPatient: () => void }) {
  const c = run.committed!;
  const fhir = run.steps.find((s) => s.name === "fhir_create");
  const fhirError = (fhir?.output as { error?: string } | null)?.error;
  return (
    <section className="panel proposal committed" aria-labelledby="saved-title">
      <h2 id="saved-title" className="panel-title">
        <span className="step-no">2</span> Saved
      </h2>
      <div className={`colour-band c-${c.colour}`}>
        <p className="colour-name">
          <span className="swatch" aria-hidden="true" />
          {c.colour.toUpperCase()} <span className="colour-label">{colourLabel(colours, c.colour)}</span>
        </p>
        <p className="colour-set">
          {c.action === "override" ? "Overridden" : "Approved"} by <strong>{c.approver}</strong> at{" "}
          {new Date(c.committed_at).toLocaleTimeString()}
        </p>
      </div>
      {fhir && !fhirError && <p className="ok">FHIR record written to the local HAPI server.</p>}
      {fhirError && (
        <p className="warning" role="status">
          The triage is saved, but the FHIR record was not written: {fhirError}
        </p>
      )}
      <p className="hint">The patient is in the waiting room below. Draft the doctor handover from their card.</p>
      <button type="button" className="btn btn-primary" onClick={onNextPatient}>
        Next patient
      </button>
    </section>
  );
}

/** Many "re-measure" flags (one per missing vital) read as one line: what to measure, and the inputs behind it. */
function groupRemeasure(flags: Proposal["flags"]): Proposal["flags"] {
  const remeasure = flags.filter((f) => f.kind === "remeasure");
  if (remeasure.length < 2) return flags;
  const fields = remeasure.map((f) => f.source.split(";")[0].replace("input: ", "").trim());
  const names = fields.map((f) => FIELD_LABELS[f] ?? f);
  const grouped = {
    kind: "remeasure" as const,
    message: `${names.join(", ")}: missing or impossible. Measure ${remeasure.length > 1 ? "them" : "it"} to complete TEWS.`,
    source: `input: ${fields.join(", ")}`,
  };
  return [grouped, ...flags.filter((f) => f.kind !== "remeasure")];
}
