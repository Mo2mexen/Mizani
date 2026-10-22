import { useState } from "react";
import { clock, short } from "../format";
import type { RunState, TraceStep } from "../types";

const KIND_LABEL: Record<TraceStep["kind"], string> = {
  model: "MODEL",
  tool_call: "TOOL",
  waiting_for_nurse: "NURSE",
};

function Step({ step }: { step: TraceStep }) {
  const [isOpen, setIsOpen] = useState(false);
  const output = step.output as { error?: string; tokens?: number; model?: string } | null;
  const isError = Boolean(output && typeof output === "object" && "error" in output && output.error);
  return (
    <li className={`trace-step kind-${step.kind}${isError ? " is-error" : ""}`}>
      <button type="button" className="trace-row" onClick={() => setIsOpen(!isOpen)} aria-expanded={isOpen}>
        <span className="trace-time">{clock(step.time)}</span>
        <span className="trace-kind">{KIND_LABEL[step.kind]}</span>
        <span className="trace-name">{step.name}</span>
        <span className="trace-meta">
          {step.kind === "model" && output?.tokens ? `${output.tokens} tok · ` : ""}
          {step.latency_ms ? `${Math.round(step.latency_ms)} ms` : ""}
        </span>
      </button>
      <div className="trace-io">
        <code>in {short(step.input, isOpen ? 4000 : 120)}</code>
        <code>out {short(step.output, isOpen ? 4000 : 120)}</code>
      </div>
    </li>
  );
}

type Props = { run: RunState | null };

export function TracePanel({ run }: Props) {
  return (
    <section className="panel trace" aria-labelledby="trace-title">
      <h2 id="trace-title" className="panel-title">
        <span className="step-no">2</span> Agent trace
        {run && <span className="run-id">run {run.runId}</span>}
      </h2>
      {!run && <p className="empty">Every model call and tool call of the next triage appears here, live.</p>}
      {run && (
        <ol className="trace-list" aria-live="polite">
          {run.steps.map((s, i) => (
            <Step key={`${s.time}-${s.name}-${i}`} step={s} />
          ))}
          {!run.proposal && !run.error && <li className="trace-wait">the agent is working…</li>}
        </ol>
      )}
      {run?.error && <p className="error" role="alert">{run.error}</p>}
    </section>
  );
}
