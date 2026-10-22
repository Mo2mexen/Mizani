// One readable line per trace step, for the nurse and for anyone watching the agent work.
// Display only: it shows what the tools sent back and never decides anything. Click a step to
// see the raw input and output.
import { FIELD_LABELS, short } from "./format";
import type { Sign, TraceStep } from "./types";

type Line = { input: string; output: string };
type Flag = { kind: string; message: string };
type Call = { name: string; args: Record<string, unknown> };

const SHORT_NAMES: Record<string, string> = {
  respiratory_rate: "RR",
  heart_rate: "HR",
  systolic_bp: "SBP",
  temperature: "T",
  spo2: "SpO₂",
  glucose: "glucose",
};

function vitals(v: unknown): string {
  if (!v || typeof v !== "object") return "none";
  const parts = Object.entries(v as Record<string, unknown>)
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([key, value]) => {
      if (key === "trauma") return value ? "injury in 48 h" : "no injury";
      if (key === "ketonuria") return value ? "ketones" : "no ketones";
      if (key === "avpu" || key === "mobility") return String(value).replace("_", " ");
      return `${SHORT_NAMES[key] ?? FIELD_LABELS[key] ?? key} ${String(value)}`;
    });
  return parts.length ? parts.join(", ") : "none";
}

function claims(signs: unknown): string {
  if (!Array.isArray(signs) || signs.length === 0) return "no chart sign";
  return signs
    .map((s: { id?: string; quote?: string }) => (s.quote ? `${s.id} “${s.quote}”` : `${s.id} (no words given)`))
    .join("; ");
}

function flags(list: Flag[], none: string): string {
  return list.length ? list.map((f) => f.message).join("; ") : none;
}

function signs(list: Sign[]): string {
  return list.length ? list.map((d) => `${d.name} (${d.colour})`).join(", ") : "no chart sign applies";
}

// Tool inputs and outputs are JSON whose shapes are in types.ts; read loosely, for display only.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const LINES: Record<string, (input: any, output: any) => Line> = {
  extract: (input, output) => {
    const calls: Call[] = output?.tool_calls ?? [];
    const asked = calls.map((c) => (c.name === "match_discriminators" ? claims(c.args.discriminators) : c.name));
    return {
      input: input?.retry_reason ? `retry ${input.attempt}: ${input.retry_reason}` : "the nurse's entry and the chart signs",
      output: asked.length ? `asks for ${asked.join(" · ")}` : short(output?.text ?? "no tool call", 160),
    };
  },
  validate_vitals: (input, output) => ({
    input: vitals(input?.vitals),
    output: Array.isArray(output) ? flags(output, "all seven TEWS vitals present and plausible") : "",
  }),
  compute_tews: (input, output) => ({
    input: vitals(input?.vitals),
    output: output?.components
      ? `TEWS ${output.total}: ` +
        output.components
          .filter((c: { points: number }) => c.points > 0)
          .map((c: { field: string; points: number }) => `${SHORT_NAMES[c.field] ?? FIELD_LABELS[c.field] ?? c.field} +${c.points}`)
          .join(", ")
      : "",
  }),
  match_discriminators: (input, output) => ({
    input: claims(input?.discriminators),
    output: output?.discriminators ? `${signs(output.discriminators)}${output.flags?.length ? ` · ${flags(output.flags, "")}` : ""}` : "",
  }),
  propose_priority: (input, output) => ({
    input: `${input?.patient_label ?? ""} · ${claims(input?.discriminators)}`,
    output: output?.colour ? `${String(output.colour).toUpperCase()}, pending the nurse · ${output.source}` : "",
  }),
  commit_triage: (input, output) => ({
    input: `triage ${input?.triage_id} with the nurse's approval token (hidden)`,
    output: output?.colour ? `saved ${String(output.colour).toUpperCase()}, ${output.action} by ${output.approver}` : "",
  }),
  fhir_create: (input, output) => ({
    input: `triage ${input?.triage_id}`,
    output: output?.Encounter
      ? `Patient/${output.Patient}, Encounter/${output.Encounter}, ${output.Observation?.length ?? 0} Observations`
      : "",
  }),
  nurse: (input) => ({ input: `proposal ${input?.triage_id}`, output: "waiting for a named nurse to decide" }),
  remeasure: (input) => ({ input: vitals(input), output: "re-scoring the new vitals, no model call" }),
  fallback: (_input, output) => ({ input: "", output: output?.warning ?? "" }),
};

/** A one-line input and output for a step; the raw JSON when the tool is not known here. */
export function traceLine(step: TraceStep): Line {
  const output = step.output as { error?: string } | null;
  if (output && typeof output === "object" && "error" in output && output.error) {
    return { input: short(step.input, 160), output: `refused: ${output.error}` };
  }
  const line = LINES[step.name];
  try {
    if (line) return line(step.input, step.output);
  } catch {
    // an unexpected shape falls through to the raw view
  }
  return { input: short(step.input, 160), output: short(step.output, 160) };
}
