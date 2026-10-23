import { useState } from "react";

const OPEN_KEY = "mizani.trace.open"; // the nurse's choice, kept in this browser only

function savedOpen(): boolean {
  try {
    return window.localStorage.getItem(OPEN_KEY) !== "false";
  } catch {
    return true;
  }
}
import { clock, short } from "../format";
import { traceLine } from "../trace";
import type { RunState, TraceStep } from "../types";

const KIND_LABEL: Record<TraceStep["kind"], string> = {
  model: "MODEL",
  tool_call: "TOOL",
  waiting_for_nurse: "NURSE",
};
const MASKED = "hidden until you record your own colour (shadow mode)";

function Step({ step, isMasked }: { step: TraceStep; isMasked: boolean }) {
  const [isOpen, setIsOpen] = useState(false);
  const output = step.output as { error?: string; tokens?: number } | null;
  const isError = Boolean(output && typeof output === "object" && "error" in output && output.error);
  const line = traceLine(step);
  return (
    <li className={`trace-step kind-${step.kind}${isError ? " is-error" : ""}`}>
      <button type="button" className="trace-row" onClick={() => setIsOpen(!isOpen)} aria-expanded={isOpen}>
        <span className="trace-time">{clock(step.time)}</span>
        <span className="trace-kind">{KIND_LABEL[step.kind]}</span>
        <span className="trace-name">{step.kind === "waiting_for_nurse" ? "waiting for the nurse" : step.name}</span>
        <span className="trace-meta">
          {step.kind === "model" && output?.tokens ? `${output.tokens} tok · ` : ""}
          {step.latency_ms ? `${(step.latency_ms / 1000).toFixed(1)} s` : ""}
        </span>
      </button>
      <dl className="trace-io">
        <dt>in</dt>
        <dd dir="auto">{isMasked ? MASKED : isOpen ? short(step.input, 4000) : line.input}</dd>
        <dt>out</dt>
        <dd dir="auto">{isMasked ? MASKED : isOpen ? short(step.output, 4000) : line.output}</dd>
      </dl>
    </li>
  );
}

/** isMasked: shadow mode before the nurse's own colour is in, so inputs and outputs (signs, scores, colour) stay hidden. */
type Props = { run: RunState | null; isMasked: boolean };

export function TracePanel({ run, isMasked }: Props) {
  const [isOpen, setIsOpen] = useState(savedOpen);
  const isWorking = run !== null && !run.proposal && !run.committed && !run.error;
  const tools = run?.steps.filter((s) => s.kind === "tool_call").length ?? 0;
  const models = run?.steps.filter((s) => s.kind === "model").length ?? 0;
  const seconds = (run?.steps ?? []).reduce((t, s) => t + (s.latency_ms ?? 0), 0) / 1000;

  function toggle() {
    setIsOpen(!isOpen);
    try {
      window.localStorage.setItem(OPEN_KEY, String(!isOpen));
    } catch {
      // private window or blocked storage: the choice lasts until reload
    }
  }

  return (
    <section className="panel trace" aria-labelledby="trace-title">
      <h2 id="trace-title" className="panel-title">
        How Mizani got there: the agent's steps
        {run && (
          <span className="count">
            {models} model call{models === 1 ? "" : "s"} · {tools} tool call{tools === 1 ? "" : "s"} · {seconds.toFixed(1)} s ·
            run <span className="run-id">{run.runId}</span>
          </span>
        )}
        <button type="button" className="btn btn-small trace-toggle" onClick={toggle} aria-expanded={isOpen} aria-controls="trace-body">
          {isOpen ? "Hide steps" : "Show steps"}
        </button>
      </h2>
      <div id="trace-body" hidden={!isOpen}>
      {!run && (
        <p className="empty">
          Every model call and tool call of the next triage appears here as it happens, with its input and output. Click a
          step for the raw data.
        </p>
      )}
      {run && (
        <ol className="trace-list" aria-live="polite" aria-relevant="additions">
          {run.steps.map((s, i) => (
            <Step key={`${s.time}-${s.name}-${i}`} step={s} isMasked={isMasked && s.kind !== "waiting_for_nurse"} />
          ))}
          {isWorking && <li className="trace-wait">the agent is working…</li>}
        </ol>
      )}
      </div>
    </section>
  );
}
