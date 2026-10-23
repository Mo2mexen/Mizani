import { useState } from "react";
import { api } from "../api";
import { EMPTY_VITALS, toVitals } from "../vitals";
import { VitalsFields } from "./VitalsFields";

/** isReady: the API has answered (colour chart loaded), so an intake can be sent. */
/** isFirst: the page's first patient gets a sample label; after "Next patient" the form starts empty. */
type Props = { isFirst: boolean; isReady: boolean; onStarted: (runId: string, complaint: string) => void };

export function IntakeForm({ isFirst, isReady, onStarted }: Props) {
  const [label, setLabel] = useState(isFirst ? "Test Patient 01" : "");
  const [complaint, setComplaint] = useState("");
  const [vitals, setVitals] = useState(EMPTY_VITALS);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setIsSending(true);
    setError(null);
    try {
      const { triage_id: runId } = await api.intake(label.trim(), complaint.trim(), toVitals(vitals));
      onStarted(runId, complaint.trim()); // the entry stays on screen until "Next patient"
    } catch (err) {
      setError(err instanceof Error ? err.message : "The intake was not sent");
    } finally {
      setIsSending(false);
    }
  }

  return (
    <form className="panel intake" onSubmit={handleSubmit} aria-labelledby="intake-title">
      <h2 id="intake-title" className="panel-title">
        <span className="step-no">1</span> Intake
      </h2>
      <label className="field" htmlFor="patient-label">
        <span className="field-label">Patient label</span>
        <input id="patient-label" dir="auto" required maxLength={80} value={label} onChange={(e) => setLabel(e.target.value)} />
        <span className="field-note">synthetic labels only, never a real name</span>
      </label>
      <label className="field" htmlFor="complaint">
        <span className="field-label">Complaint, as told</span>
        <textarea
          id="complaint"
          dir="auto"
          required
          rows={3}
          maxLength={2000}
          value={complaint}
          onChange={(e) => setComplaint(e.target.value)}
          placeholder="e.g. chest pain since this morning, sweaty (English or Arabic)"
        />
      </label>
      <p className="hint">Leave a vital empty if it wasn't measured: Mizani asks for a re-measure, it never guesses.</p>
      <VitalsFields value={vitals} onChange={setVitals} idPrefix="intake" />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button className="btn btn-primary" type="submit" disabled={isSending || !isReady}>
        {isSending ? "Sending…" : isReady ? "Start triage" : "Waiting for the API…"}
      </button>
    </form>
  );
}
