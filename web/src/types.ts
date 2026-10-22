// Shapes of the Mizani API (team contract, mirrors src/mizani/schemas.py). The UI only shows
// these values; it never computes or changes a colour.

export type Avpu = "alert" | "confused" | "voice" | "pain" | "unresponsive";
export type Mobility = "walking" | "with_help" | "stretcher";

export type Vitals = {
  respiratory_rate?: number;
  heart_rate?: number;
  systolic_bp?: number;
  temperature?: number;
  spo2?: number; // not scored, only flagged
  avpu?: Avpu;
  glucose?: number; // mmol/L
  ketonuria?: boolean;
  mobility?: Mobility;
  trauma?: boolean; // any injury in the past 48 hours
};

export type Sourced = { source: string };

export type TraceStep = {
  time: string;
  kind: "tool_call" | "model" | "waiting_for_nurse";
  name: string;
  input: unknown;
  output: unknown;
  latency_ms: number;
};

export type FlagKind =
  | "remeasure"
  | "incomplete"
  | "investigation"
  | "not_on_chart"
  | "conflicts_with_vitals"
  | "retriage"
  | "recheck_due";

export type Proposal = Sourced & {
  triage_id: string;
  colour: string;
  status: "pending";
  tews: { total: number; components: ({ field: string; value: unknown; points: number } & Sourced)[] };
  discriminators: ({ name: string; colour: string } & Sourced)[];
  flags: ({ kind: FlagKind; message: string } & Sourced)[];
};

export type Committed = {
  triage_id: string;
  colour: string;
  approver: string;
  action: string;
  committed_at: string;
};

export type Colour = { colour: string; label: string; target_minutes: number; source: string };

export type QueueEntry = {
  triage_id: string;
  patient_label: string;
  colour: string;
  arrived_at: string;
  waited_minutes: number;
  minutes_since_triage: number;
  target_minutes: number;
  recheck_due: boolean;
  recheck_raised: boolean;
};

export type AuditRow = {
  time: string;
  tool: string;
  inputs: unknown;
  outputs: unknown;
  error: boolean;
  approver: string | null;
  model: string | null;
  tokens: number | null;
  latency_ms: number;
  cost: number;
  hash: string;
};

export type Decision = {
  nurse_name: string;
  action: "approve" | "override";
  colour?: string;
  reason?: string;
  /** The proposal on screen; the API refuses the decision if the agent re-scored since. */
  triage_id: string;
};

export type HandoverDraft = { triage_id: string; draft_id: number; status: string; text: string };
export type HandoverReleased = { triage_id: string; text: string; approver: string; released_at: string };

export type Shadow = {
  assessed: number;
  agreed: number;
  agreement_rate: number | null;
  disagreements: {
    triage_id: string;
    nurse_colour: string;
    mizani_colour: string;
    mizani_source: string;
    nurse_reason: string | null;
  }[];
};

// What one agent run has sent so far on its event stream.
export type RunState = {
  runId: string;
  steps: TraceStep[];
  proposal: Proposal | null;
  commitError: string | null;
  committed: Committed | null;
  error: string | null;
};
