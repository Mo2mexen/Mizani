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

type Sourced = { source: string };

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
  | "not_in_complaint"
  | "ignored_instruction"
  | "confirm_sign"
  | "sign_withdrawn"
  | "signs_not_checked"
  | "retriage"
  | "recheck_due";

/** A chart sign; `quote` is the complaint's words behind it (null when the vitals prove it). */
export type Sign = Sourced & { name: string; colour: string; quote?: string | null };

export type Proposal = Sourced & {
  triage_id: string;
  colour: string;
  status: "pending";
  tews: { total: number; components: ({ field: string; value: unknown; points: number } & Sourced)[] };
  discriminators: Sign[];
  flags: ({ kind: FlagKind; message: string } & Sourced)[];
  /** The chart that scored it ("sats_adult, built in", "sats_kenya, version 2"); absent on proposals before 16 Nov 2026. */
  chart?: string | null;
};

export type Committed = {
  triage_id: string;
  colour: string;
  approver: string;
  action: string;
  committed_at: string;
};

/** One saved patient as the API stores it (GET /api/queue/{triage_id}). */
export type PatientRecord = {
  id: string;
  patient_label: string;
  complaint: string;
  vitals: Record<string, string | number | boolean | null>;
  proposal: Proposal;
  colour: string;
  approver: string | null;
  decision: { action: string; colour: string | null; reason: string | null } | null;
  arrived_at: string;
  committed_at: string | null;
  retriage_of: string | null;
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
  /** The prompt and the reply of the model calls this row carries; absent on rows logged before 16 Nov 2026. */
  tokens_in?: number | null;
  tokens_out?: number | null;
  latency_ms: number;
  cost: number;
  /** The agent run that made the call; null for calls from the page or another client. */
  run_id?: string | null;
  hash: string;
};

/** The name saved with a decision is the signed-in nurse's, from the session; the page sends none. */
export type Decision = {
  action: "approve" | "override";
  colour?: string;
  reason?: string;
  /** The nurse checked the "nurse to confirm" sign with the patient; needed to approve one. */
  confirmed?: boolean;
  /** The proposal on screen; the API refuses the decision if the agent re-scored since. */
  triage_id: string;
};

export type HandoverDraft = { triage_id: string; draft_id: number; status: string; text: string };
export type HandoverReleased = { triage_id: string; text: string; approver: string; released_at: string };
export type CalledIn = { triage_id: string; approver: string; called_in_at: string; waited_minutes: number };

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

export type Role = "nurse" | "doctor" | "admin";

/** A person who signs in (mirrors `Account` in schemas.py; never the password). `chief` is the flag an admin gives a doctor. */
export type Account = {
  id: number;
  username: string;
  full_name: string;
  role: Role;
  chief: boolean;
  chief_by: string | null;
  chief_at: string | null;
  active: boolean;
  /** Signed in with a one-time password: the page asks for their own first. */
  must_change: boolean;
  locked_until: string | null;
  created_at: string;
  created_by: string | null;
  last_login_at: string | null;
};

/** POST /api/admin/users: the one-time password is in this answer only. */
export type NewAccount = { account: Account; one_time_password: string };

/** Where the model runs (Admin › Model). "this_server" is the model that came with Mizani (.env). */
export type ModelLocation = "this_server" | "hospital" | "cloud";

/** GET /api/admin/model: the key itself never comes back, only whether one is set. */
export type ModelSetting = {
  location: ModelLocation;
  base_url: string;
  model: string;
  reasoning_effort: string | null;
  has_key: boolean;
  updated_at: string | null;
  updated_by: string | null;
  /** The model .env sets up on this server, shown for the "This server" choice. */
  this_server: { base_url: string; model: string };
};

/** POST /api/admin/model and /api/admin/model/test. An empty api_key keeps the saved key for the same address. */
export type ModelChoice = {
  location: ModelLocation;
  base_url?: string;
  model?: string;
  api_key?: string;
  reasoning_effort?: string;
  leaves_hospital_confirmed?: boolean;
};

export type ModelTest = { ok: true; seconds: number; model: string; reply: string } | { ok: false; error: string };

type ChartStatus = "uploaded" | "evaluating" | "evaluated" | "failed" | "active" | "retired";

/** An uploaded chart (GET /api/charts), without its report. */
export type ChartSummary = {
  id: number;
  pack_id: string;
  version: number;
  name: string;
  label: string;
  status: ChartStatus;
  uploaded_by: string;
  uploaded_at: string;
  /** "12 of 17 runs" while evaluating; why it stopped when it failed. */
  progress: string | null;
  activated_by: string | null;
  activated_at: string | null;
  previous: string | null;
  test_patients: number;
  safety_ok: boolean;
};

type SafetyCheck = { check: string; ok: boolean | null; detail: string };

type ChartPatient = {
  id: string;
  runs: number;
  passes: number | null;
  colour: string;
  in_use_colour: string;
  agree: number;
  changes: boolean;
  failed_checks: Record<string, number>;
  seconds_mean: number;
};

/** The evaluation's report. `pass_rate` only with the chart's own test patients; otherwise only agreement. */
export type ChartReport = {
  chart: string;
  against: string;
  model: string;
  mode: "pass rate" | "agreement with the chart in use";
  runs_per_patient: number;
  pass_rate: number | null;
  agreement: number;
  changes: string[];
  patients: ChartPatient[];
  safety: SafetyCheck[];
  safety_ok: boolean;
  started: string;
  seconds: number;
};

export type ChartDetail = ChartSummary & { report: ChartReport | null };

export type ChartOverview = {
  active: { label: string; name: string; chart: ChartSummary | null };
  /** The chart in use before this one: an id, "built-in", or null when the built-in chart is in use. */
  previous: string | null;
  previous_label: string | null;
  built_in: string;
  charts: ChartSummary[];
};

export type ChartActivated = { in_use: string; previous: string; approver: string; overview: ChartOverview };

/** One row of the doctors' inbox (GET /api/handovers): the latest handover a nurse released, for a patient still waiting. */
export type InboxEntry = {
  triage_id: string;
  text: string;
  released_by: string;
  released_at: string;
  patient_label: string;
  colour: string;
  waited_minutes: number;
  target_minutes: number;
  recheck_due: boolean;
};

// What one agent run has sent so far on its event stream.
export type RunState = {
  runId: string;
  steps: TraceStep[];
  proposal: Proposal | null;
  commitError: string | null;
  committed: Committed | null;
  error: string | null;
  /** The stream dropped and the browser is reconnecting (it resumes with Last-Event-ID). */
  isReconnecting: boolean;
};
