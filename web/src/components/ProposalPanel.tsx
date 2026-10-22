import { useState } from "react";
import { api } from "../api";
import { colourLabel, FIELD_LABELS, FLAG_LABELS } from "../format";
import type { Colour, Proposal, RunState } from "../types";
import { EMPTY_VITALS, toVitals } from "../vitals";
import { VitalsFields } from "./VitalsFields";

type Props = {
  run: RunState;
  complaint: string | null;
  colours: Colour[];
  isShadow: boolean;
  /** Shadow mode: the proposal (triage id) the nurse already gave her own colour for. */
  assessedFor: string | null;
  onAssessed: (triageId: string) => void;
  onCommitted: () => void;
};

/** The nurse's screen: the proposal with every source, then approve, override or re-measure. */
export function ProposalPanel({ run, complaint, colours, isShadow, assessedFor, onAssessed, onCommitted }: Props) {
  const proposal = run.proposal;

  if (run.committed) return <CommittedView run={run} colours={colours} />;
  if (!proposal) return null;
  // One blind assessment per proposal: a re-measure makes a new proposal, so she is asked again.
  if (isShadow && assessedFor !== proposal.triage_id) {
    return <ShadowFirst runId={run.runId} colours={colours} onDone={() => onAssessed(proposal.triage_id)} />;
  }
  return (
    <section className="panel proposal" aria-labelledby="proposal-title">
      <h2 id="proposal-title" className="panel-title">
        <span className="step-no">3</span> Proposal <span className="pending">pending, not saved</span>
      </h2>
      {complaint && (
        <p className="complaint" dir="auto">
          {complaint}
        </p>
      )}
      <ColourBand proposal={proposal} colours={colours} />
      <Breakdown proposal={proposal} />
      {run.commitError && (
        <p className="error" role="alert">
          Commit refused: {run.commitError}
        </p>
      )}
      <DecisionForm key={proposal.triage_id} run={run} proposal={proposal} colours={colours} onCommitted={onCommitted} />
    </section>
  );
}

function ColourBand({ proposal, colours }: { proposal: Proposal; colours: Colour[] }) {
  return (
    <div className={`colour-band c-${proposal.colour}`}>
      <div className="colour-name">
        <span className="swatch" aria-hidden="true" />
        {proposal.colour.toUpperCase()} <span className="colour-label">{colourLabel(colours, proposal.colour)}</span>
      </div>
      <p className="colour-why">
        <span className="src-tag">why</span> {proposal.source}
      </p>
    </div>
  );
}

function Breakdown({ proposal }: { proposal: Proposal }) {
  return (
    <div className="breakdown">
      <table className="tews">
        <caption>
          TEWS <strong>{proposal.tews.total}</strong>
        </caption>
        <thead>
          <tr>
            <th>Vital</th>
            <th className="num">Value</th>
            <th className="num">Points</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {proposal.tews.components.map((c) => (
            <tr key={c.field}>
              <td>{FIELD_LABELS[c.field] ?? c.field}</td>
              <td className="num">{String(c.value)}</td>
              <td className="num">{c.points}</td>
              <td className="src">{c.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3 className="sub-title">Chart signs</h3>
      {proposal.discriminators.length === 0 && <p className="empty">None named or proven.</p>}
      <ul className="signs">
        {proposal.discriminators.map((d) => (
          <li key={d.name}>
            <span className={`chip c-${d.colour}`}>{d.colour.toUpperCase()}</span> {d.name}
            <span className="src">{d.source}</span>
          </li>
        ))}
      </ul>
      {proposal.flags.length > 0 && (
        <>
          <h3 className="sub-title">Flags</h3>
          <ul className="flags">
            {proposal.flags.map((f, i) => (
              <li key={`${f.kind}-${i}`} className={`flag flag-${f.kind}`}>
                <span className="flag-kind">{FLAG_LABELS[f.kind] ?? f.kind}</span> {f.message}
                <span className="src">{f.source}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

type DecisionProps = { run: RunState; proposal: Proposal; colours: Colour[]; onCommitted: () => void };

function DecisionForm({ run, proposal, colours, onCommitted }: DecisionProps) {
  const [nurse, setNurse] = useState("");
  const [overrideColour, setOverrideColour] = useState("");
  const [reason, setReason] = useState("");
  const [isRemeasuring, setIsRemeasuring] = useState(false);
  const [vitals, setVitals] = useState(EMPTY_VITALS);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const flagged = new Set(proposal.flags.filter((f) => f.kind === "remeasure").map((f) => f.source.split(";")[0].replace("input: ", "")));
  const named = nurse.trim().length > 0;

  async function act(work: () => Promise<unknown>) {
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

  const approve = () =>
    act(async () => {
      await api.decide(run.runId, { nurse_name: nurse.trim(), action: "approve", triage_id: proposal.triage_id });
      onCommitted();
    });
  const override = () =>
    act(async () => {
      await api.decide(run.runId, {
        nurse_name: nurse.trim(),
        action: "override",
        colour: overrideColour,
        reason: reason.trim(),
        triage_id: proposal.triage_id,
      });
      onCommitted();
    });
  const remeasure = () =>
    act(async () => {
      await api.remeasure(run.runId, toVitals(vitals));
      setIsRemeasuring(false);
      setVitals(EMPTY_VITALS);
    });

  return (
    <div className="decision">
      <h3 className="sub-title">Nurse decision</h3>
      <label className="field" htmlFor="nurse-name">
        <span className="field-label">Your name (required to save)</span>
        <input id="nurse-name" value={nurse} onChange={(e) => setNurse(e.target.value)} autoComplete="off" />
      </label>
      <div className="decision-row">
        <button type="button" className="btn btn-primary" disabled={!named || isBusy} onClick={approve}>
          Approve {proposal.colour.toUpperCase()}
        </button>
        <button type="button" className="btn" disabled={isBusy} onClick={() => setIsRemeasuring(!isRemeasuring)}>
          {isRemeasuring ? "Cancel re-measure" : "Enter re-measured vitals"}
        </button>
      </div>
      <fieldset className="override">
        <legend>Override (colour and reason required)</legend>
        <select aria-label="Override colour" value={overrideColour} onChange={(e) => setOverrideColour(e.target.value)}>
          <option value="">choose a colour</option>
          {colours.map((c) => (
            <option key={c.colour} value={c.colour}>
              {c.colour.toUpperCase()} · {c.label}
            </option>
          ))}
        </select>
        <input aria-label="Override reason" placeholder="reason" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button
          type="button"
          className="btn btn-warn"
          disabled={!named || !overrideColour || !reason.trim() || isBusy}
          onClick={override}
        >
          Override
        </button>
      </fieldset>
      {isRemeasuring && (
        <div className="remeasure">
          <p className="hint">Highlighted fields were missing or impossible. The agent re-scores without the model.</p>
          <VitalsFields value={vitals} onChange={setVitals} highlight={flagged} idPrefix="remeasure" />
          <button type="button" className="btn btn-primary" disabled={isBusy} onClick={remeasure}>
            Re-score with these vitals
          </button>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

function ShadowFirst({ runId, colours, onDone }: { runId: string; colours: Colour[]; onDone: () => void }) {
  const [nurse, setNurse] = useState("");
  const [colour, setColour] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.assess(runId, nurse.trim(), colour, reason.trim() || undefined);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not recorded");
    }
  }

  return (
    <form className="panel proposal shadow" onSubmit={handleSubmit}>
      <h2 className="panel-title">
        <span className="step-no">3</span> Shadow mode: your colour first
      </h2>
      <p className="hint">Mizani's proposal is ready. Record your own colour before you see it.</p>
      <label className="field" htmlFor="shadow-nurse">
        <span className="field-label">Your name</span>
        <input id="shadow-nurse" required value={nurse} onChange={(e) => setNurse(e.target.value)} />
      </label>
      <select aria-label="Your colour" required value={colour} onChange={(e) => setColour(e.target.value)}>
        <option value="">your colour</option>
        {colours.map((c) => (
          <option key={c.colour} value={c.colour}>
            {c.colour.toUpperCase()} · {c.label}
          </option>
        ))}
      </select>
      <input aria-label="Your reason" placeholder="reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
      {error && <p className="error" role="alert">{error}</p>}
      <button className="btn btn-primary" type="submit">
        Record and show Mizani's proposal
      </button>
    </form>
  );
}

function CommittedView({ run, colours }: { run: RunState; colours: Colour[] }) {
  const c = run.committed!;
  const fhir = run.steps.find((s) => s.name === "fhir_create");
  return (
    <section className="panel proposal committed" aria-live="polite">
      <h2 className="panel-title">
        <span className="step-no">3</span> Saved
      </h2>
      <div className={`colour-band c-${c.colour}`}>
        <div className="colour-name">
          <span className="swatch" aria-hidden="true" />
          {c.colour.toUpperCase()} <span className="colour-label">{colourLabel(colours, c.colour)}</span>
        </div>
        <p className="colour-why">
          {c.action === "override" ? "Overridden" : "Approved"} by {c.approver} at {new Date(c.committed_at).toLocaleTimeString()}
          {fhir ? " · FHIR record written" : ""}
        </p>
      </div>
    </section>
  );
}
