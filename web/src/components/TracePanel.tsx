import { useEffect, useState } from "react";

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

type Usage = { tokens?: number; tokens_in?: number; tokens_out?: number };

/** "1,982 in · 143 out · ": what the model read and wrote; the total alone for runs logged before the split. */
function tokenLabel({ tokens, tokens_in, tokens_out }: Usage): string {
  if (tokens_in || tokens_out) return `${(tokens_in ?? 0).toLocaleString()} in · ${(tokens_out ?? 0).toLocaleString()} out · `;
  return tokens ? `${tokens.toLocaleString()} tok · ` : "";
}

function Step({ step, isMasked }: { step: TraceStep; isMasked: boolean }) {
  const [isOpen, setIsOpen] = useState(false);
  const output = step.output as { error?: string; tokens?: number; tokens_in?: number; tokens_out?: number } | null;
  const isError = Boolean(output && typeof output === "object" && "error" in output && output.error);
  const line = traceLine(step);
  return (
    <li className={`trace-step kind-${step.kind}${isError ? " is-error" : ""}`}>
      <button type="button" className="trace-row" onClick={() => setIsOpen(!isOpen)} aria-expanded={isOpen}>
        <span className="trace-time">{clock(step.time)}</span>
        <span className="trace-kind">{KIND_LABEL[step.kind]}</span>
        <span className="trace-name">{step.kind === "waiting_for_nurse" ? "waiting for the nurse" : step.name}</span>
        <span className="trace-meta">
          {step.kind === "model" && output ? tokenLabel(output) : ""}
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

type Running = { kind: TraceStep["kind"]; name: string; doing: string };

const READING: Running = { kind: "model", name: "extract", doing: "the model is reading the complaint" };
const REREADING: Running = { kind: "model", name: "extract", doing: "the model is reading again, with the reason" };
// What runs now, from the step that finished last (the graph's order: agent/graph.py).
const AFTER: Record<string, Running> = {
  extract: { kind: "tool_call", name: "validate_vitals", doing: "checking the vitals" },
  fallback: { kind: "tool_call", name: "validate_vitals", doing: "checking the nurse's vitals" },
  validate_vitals: { kind: "tool_call", name: "compute_tews", doing: "scoring the vitals (TEWS)" },
  compute_tews: { kind: "tool_call", name: "match_discriminators", doing: "checking each sign and its quote" },
  match_discriminators: { kind: "tool_call", name: "propose_priority", doing: "setting the colour" },
  review: { kind: "tool_call", name: "match_discriminators", doing: "checking the signs again" },
};

/** The step running now. A refused call, or a model answer without both tool calls, goes back to the model. */
function runningAfter(last: TraceStep | undefined): Running {
  if (!last) return READING;
  const output = last.output as { error?: string; tool_calls?: { name: string }[] } | null;
  if (last.kind === "tool_call" && output?.error) return REREADING;
  if (last.name === "extract" && !output?.error) {
    const called = new Set((output?.tool_calls ?? []).map((c) => c.name));
    if (!called.has("validate_vitals") || !called.has("match_discriminators")) return REREADING;
  }
  return AFTER[last.name] ?? { kind: "tool_call", name: "", doing: "working" };
}

/** The live row while the agent works: what runs now and for how long. Its key changes with every finished step, so the count restarts. */
function RunningStep({ last }: { last: TraceStep | undefined }) {
  const [started] = useState(() => Date.now());
  const [now, setNow] = useState(started);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const step = runningAfter(last);
  return (
    <li className={`trace-step kind-${step.kind} is-running`}>
      <div className="trace-row">
        <span className="trace-time">now</span>
        <span className="trace-kind">{KIND_LABEL[step.kind]}</span>
        <span className="trace-name">{step.name}</span>
        <span className="trace-meta">{Math.floor((now - started) / 1000)} s</span>
      </div>
      <p className="trace-wait">{step.doing}…</p>
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
  const used = (run?.steps ?? [])
    .filter((s) => s.kind === "model")
    .reduce<Required<Usage>>(
      (sum, s) => {
        const o = (s.output ?? {}) as Usage;
        return { tokens: sum.tokens + (o.tokens ?? 0), tokens_in: sum.tokens_in + (o.tokens_in ?? 0), tokens_out: sum.tokens_out + (o.tokens_out ?? 0) };
      },
      { tokens: 0, tokens_in: 0, tokens_out: 0 },
    );

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
            {models} model call{models === 1 ? "" : "s"} · {tokenLabel(used)}
            {tools} tool call{tools === 1 ? "" : "s"} · {seconds.toFixed(1)} s ·
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
          {isWorking && <RunningStep key={run.steps.length} last={run.steps[run.steps.length - 1]} />}
        </ol>
      )}
      </div>
    </section>
  );
}
