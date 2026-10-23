import { useState } from "react";
import { api } from "../api";
import type { Colour, Proposal, RunState } from "../types";
import { EMPTY_VITALS, toVitals } from "../vitals";
import { VitalsFields } from "./VitalsFields";

type Props = {
  run: RunState;
  proposal: Proposal;
  colours: Colour[];
  /** The proposal has an emergency sign from the complaint alone: approving needs a tick (the server checks it too). */
  needsConfirm: boolean;
  onCommitted: () => void;
};

type Busy = "approve" | "override" | "remeasure" | null;

/** Approve, override or re-measure. Nothing is saved without the nurse's name. */
export function DecisionForm({ run, proposal, colours, needsConfirm, onCommitted }: Props) {
  const [nurse, setNurse] = useState("");
  const [isConfirmed, setIsConfirmed] = useState(false);
  const [overrideColour, setOverrideColour] = useState("");
  const [reason, setReason] = useState("");
  const [isRemeasuring, setIsRemeasuring] = useState(false);
  const [vitals, setVitals] = useState(EMPTY_VITALS);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const flagged = new Set(
    proposal.flags.filter((f) => f.kind === "remeasure").map((f) => f.source.split(";")[0].replace("input: ", "")),
  );
  const named = nurse.trim().length > 0;
  const canApprove = named && (!needsConfirm || isConfirmed) && busy === null;

  async function act(kind: Busy, work: () => Promise<unknown>) {
    setBusy(kind);
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not sent");
    } finally {
      setBusy(null);
    }
  }

  function approve(e: React.FormEvent) {
    e.preventDefault();
    if (!canApprove) return;
    void act("approve", async () => {
      await api.decide(run.runId, {
        nurse_name: nurse.trim(),
        action: "approve",
        confirmed: needsConfirm ? isConfirmed : undefined,
        triage_id: proposal.triage_id,
      });
      onCommitted();
    });
  }

  function override(e: React.FormEvent) {
    e.preventDefault();
    if (!named || !overrideColour || !reason.trim() || busy !== null) return;
    void act("override", async () => {
      await api.decide(run.runId, {
        nurse_name: nurse.trim(),
        action: "override",
        colour: overrideColour,
        reason: reason.trim(),
        triage_id: proposal.triage_id,
      });
      onCommitted();
    });
  }

  function remeasure(e: React.FormEvent) {
    e.preventDefault();
    void act("remeasure", async () => {
      await api.remeasure(run.runId, toVitals(vitals));
      setIsRemeasuring(false);
      setVitals(EMPTY_VITALS);
    });
  }

  return (
    <div className="decision">
      <h3 className="sub-title">Your decision</h3>
      <form onSubmit={approve}>
        <label className="field" htmlFor="nurse-name">
          <span className="field-label">Your name (required to save)</span>
          <input id="nurse-name" dir="auto" value={nurse} onChange={(e) => setNurse(e.target.value)} autoComplete="off" />
        </label>
        {needsConfirm && (
          <label className="check">
            <input type="checkbox" checked={isConfirmed} onChange={(e) => setIsConfirmed(e.target.checked)} />
            I checked the sign marked "nurse to confirm" with the patient
          </label>
        )}
        <div className="decision-row">
          <button type="submit" className="btn btn-primary" disabled={!canApprove}>
            {busy === "approve" ? "Saving…" : `Approve ${proposal.colour.toUpperCase()}`}
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy !== null}
            aria-expanded={isRemeasuring}
            onClick={() => setIsRemeasuring(!isRemeasuring)}
          >
            {isRemeasuring ? "Cancel re-measure" : "Enter re-measured vitals"}
          </button>
        </div>
        {!named && <p className="hint">Type your name to approve or override.</p>}
      </form>
      <form className="override" onSubmit={override}>
        <fieldset>
          <legend>Override: a colour and a reason are required</legend>
          <label className="field" htmlFor="override-colour">
            <span className="field-label">Colour</span>
            <select id="override-colour" value={overrideColour} onChange={(e) => setOverrideColour(e.target.value)}>
              <option value="">choose a colour</option>
              {colours.map((c) => (
                <option key={c.colour} value={c.colour}>
                  {c.colour.toUpperCase()} · {c.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field" htmlFor="override-reason">
            <span className="field-label">Reason</span>
            <input id="override-reason" dir="auto" value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <button
            type="submit"
            className="btn btn-warn"
            disabled={!named || !overrideColour || !reason.trim() || busy !== null}
          >
            {busy === "override" ? "Saving…" : "Override"}
          </button>
        </fieldset>
      </form>
      {isRemeasuring && (
        <form className="remeasure" onSubmit={remeasure} aria-label="Re-measured vitals">
          <p className="hint">Highlighted fields were missing or impossible. The agent re-scores without the model.</p>
          <VitalsFields value={vitals} onChange={setVitals} highlight={flagged} idPrefix="remeasure" />
          <button type="submit" className="btn btn-primary" disabled={busy !== null}>
            {busy === "remeasure" ? "Sending…" : "Re-score with these vitals"}
          </button>
        </form>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
