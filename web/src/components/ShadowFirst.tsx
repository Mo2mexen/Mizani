import { useState } from "react";
import { api, ApiError } from "../api";
import type { Colour } from "../types";

/** Shadow mode: the nurse records her own colour before Mizani's is shown. */
export function ShadowFirst({ runId, colours, onDone }: { runId: string; colours: Colour[]; onDone: () => void }) {
  const [nurse, setNurse] = useState("");
  const [colour, setColour] = useState("");
  const [reason, setReason] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setIsBusy(true);
    setError(null);
    try {
      await api.assess(runId, nurse.trim(), colour, reason.trim() || undefined);
      onDone();
    } catch (err) {
      // Recorded already (the page was reloaded after the nurse's colour went in): show the proposal.
      if (err instanceof ApiError && err.status === 409 && err.message.includes("already")) {
        onDone();
        return;
      }
      setError(err instanceof Error ? err.message : "Not recorded");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <form className="panel proposal shadow" onSubmit={handleSubmit} aria-labelledby="shadow-title">
      <h2 id="shadow-title" className="panel-title">
        <span className="step-no">3</span> Shadow mode: your colour first
      </h2>
      <p className="hint">Mizani's proposal is ready. Record your own colour before you see it.</p>
      <label className="field" htmlFor="shadow-nurse">
        <span className="field-label">Your name</span>
        <input id="shadow-nurse" required value={nurse} onChange={(e) => setNurse(e.target.value)} autoComplete="off" />
      </label>
      <label className="field" htmlFor="shadow-colour">
        <span className="field-label">Your colour</span>
        <select id="shadow-colour" required value={colour} onChange={(e) => setColour(e.target.value)}>
          <option value="">choose a colour</option>
          {colours.map((c) => (
            <option key={c.colour} value={c.colour}>
              {c.colour.toUpperCase()} · {c.label}
            </option>
          ))}
        </select>
      </label>
      <label className="field" htmlFor="shadow-reason">
        <span className="field-label">Your reason (optional)</span>
        <input id="shadow-reason" dir="auto" value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button className="btn btn-primary" type="submit" disabled={isBusy || !nurse.trim() || !colour}>
        {isBusy ? "Recording…" : "Record and show Mizani's proposal"}
      </button>
    </form>
  );
}
