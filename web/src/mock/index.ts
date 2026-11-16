// Fake API for building the screens without the backend (VITE_USE_MOCK=true).
// FAKE DATA: every colour, target time, rule and patient below is made up for the mock only.
// The real values come from the API (rule pack and engine); the UI never hard-codes them.
import type {
  Account,
  AuditRow,
  CalledIn,
  Colour,
  Committed,
  Decision,
  HandoverDraft,
  HandoverReleased,
  InboxEntry,
  ModelChoice,
  ModelSetting,
  ModelTest,
  NewAccount,
  PatientRecord,
  Proposal,
  QueueEntry,
  Role,
  Shadow,
  TraceStep,
  Vitals,
} from "../types";
import { ApiError, type StreamHandlers } from "../api";

// FAKE accounts. Sign in with any password; the user name picks the role: "doctor…", "chief…"
// (a doctor with the chief flag), "admin…", anything else is a nurse. "new…" asks for a new password.
function fakeAccount(id: number, username: string, full_name: string, role: Role, chief = false): Account {
  return {
    id,
    username,
    full_name,
    role,
    chief,
    chief_by: chief ? "FAKE Admin" : null,
    chief_at: chief ? new Date().toISOString() : null,
    active: true,
    must_change: false,
    locked_until: null,
    created_at: new Date().toISOString(),
    created_by: "FAKE Admin",
    last_login_at: null,
  };
}
let people: Account[] = [
  fakeAccount(1, "admin", "FAKE Admin", "admin"),
  fakeAccount(2, "nurse.a", "FAKE Nurse A", "nurse"),
  fakeAccount(3, "doctor.d", "FAKE Doctor D", "doctor"),
  fakeAccount(4, "chief.c", "FAKE Chief C", "doctor", true),
];
let signedIn: Account | null = null;
const FAKE_LOCAL = { base_url: "http://ollama:11434/v1", model: "FAKE qwen3.5:4b-q4_K_M" };
let modelNow: ModelSetting = {
  location: "this_server",
  ...FAKE_LOCAL,
  reasoning_effort: "none",
  has_key: false,
  updated_at: null,
  updated_by: null,
  this_server: FAKE_LOCAL,
};

function update(id: number, change: Partial<Account>): Account {
  people = people.map((p) => (p.id === id ? { ...p, ...change } : p));
  return people.find((p) => p.id === id)!;
}

function whoAmI(): Account {
  if (!signedIn) throw new ApiError(401, "You are signed out. Sign in again to continue.");
  return signedIn;
}

const FAKE_COLOURS: Colour[] = [
  { colour: "red", label: "Emergency", target_minutes: 0, source: "FAKE mock target" },
  { colour: "orange", label: "Very urgent", target_minutes: 10, source: "FAKE mock target" },
  { colour: "yellow", label: "Urgent", target_minutes: 60, source: "FAKE mock target" },
  { colour: "green", label: "Routine", target_minutes: 240, source: "FAKE mock target" },
];

let queue: QueueEntry[] = [];
const streams = new Map<string, StreamHandlers>(); // like the API, a decision ends the run's stream
const audit: AuditRow[] = [];
const now = () => new Date().toISOString();

function fakeProposal(triageId: string): Proposal {
  return {
    triage_id: triageId,
    colour: "orange",
    status: "pending",
    source: "FAKE mock: discriminator 'Chest pain' -> orange",
    tews: {
      total: 2,
      components: [
        { field: "respiratory_rate", value: 24, points: 2, source: "FAKE mock rule" },
        { field: "heart_rate", value: 88, points: 0, source: "FAKE mock rule" },
      ],
    },
    discriminators: [
      { name: "Chest pain", colour: "orange", quote: "chest pain since this morning", source: "FAKE mock rule; matched from input: complaint" },
    ],
    flags: [
      { kind: "remeasure", message: "Temperature is missing (FAKE mock)", source: "input: temperature" },
      { kind: "investigation", message: "Immediate ECG (FAKE mock)", source: "FAKE mock rule" },
    ],
  };
}

function step(kind: TraceStep["kind"], name: string, input: unknown, output: unknown): TraceStep {
  return { time: now(), kind, name, input, output, latency_ms: Math.round(Math.random() * 40) };
}

const FAKE_PROPOSAL_FOR_RECORD: PatientRecord["proposal"] = {
  triage_id: "mock",
  colour: "orange",
  status: "pending",
  source: "FAKE mock: discriminator 'Chest pain' -> orange",
  tews: { total: 2, components: [{ field: "respiratory_rate", value: 24, points: 2, source: "FAKE mock rule" }] },
  discriminators: [{ name: "Chest pain", colour: "orange", quote: "chest pain since this morning", source: "FAKE mock rule" }],
  flags: [],
};

export const api = {
  me: async () => whoAmI(),
  login: async (username: string, _password: string): Promise<Account> => {
    void _password;
    const name = username.trim().toLowerCase();
    const role: Role = name.startsWith("admin") ? "admin" : name.startsWith("doctor") || name.startsWith("chief") ? "doctor" : "nurse";
    signedIn = {
      ...fakeAccount(99, name, `FAKE ${name}`, role, name.startsWith("chief")),
      must_change: name.startsWith("new"),
    };
    return signedIn;
  },
  logout: async () => {
    signedIn = null;
    return { signed_out: true };
  },
  changePassword: async (_current: string, next: string): Promise<Account> => {
    void _current;
    if (next.length < 10) throw new ApiError(400, "Choose a password of at least 10 characters.");
    signedIn = { ...whoAmI(), must_change: false };
    return signedIn;
  },
  users: async () => people,
  addUser: async (username: string, full_name: string, role: Role): Promise<NewAccount> => {
    const account = { ...fakeAccount(people.length + 10, username, full_name, role), must_change: true };
    people = [...people, account];
    return { account, one_time_password: "fake-mock-pass" };
  },
  resetPassword: async (id: number) => {
    update(id, { must_change: true, locked_until: null });
    return { one_time_password: "fake-mock-pass" };
  },
  setRole: async (id: number, role: Role) => update(id, { role, ...(role === "doctor" ? {} : { chief: false, chief_by: null, chief_at: null }) }),
  setChief: async (id: number, chief: boolean) =>
    update(id, { chief, chief_by: chief ? whoAmI().full_name : null, chief_at: chief ? now() : null }),
  setActive: async (id: number, active: boolean) => update(id, { active }),
  signOutEverywhere: async () => ({ signed_out: true }),
  modelSetting: async (): Promise<ModelSetting> => modelNow,
  testModel: async (choice: ModelChoice): Promise<ModelTest> =>
    choice.location !== "this_server" && !choice.base_url
      ? { ok: false, error: "FAKE mock: no address" }
      : { ok: true, seconds: 1.8, model: choice.model || modelNow.this_server.model, reply: "ready" },
  saveModel: async (choice: ModelChoice): Promise<ModelSetting> => {
    modelNow =
      choice.location === "this_server"
        ? { ...modelNow, location: "this_server", ...modelNow.this_server, has_key: false }
        : {
            ...modelNow,
            location: choice.location,
            base_url: choice.base_url ?? "",
            model: choice.model ?? "",
            reasoning_effort: choice.reasoning_effort ?? null,
            has_key: Boolean(choice.api_key) || modelNow.has_key,
            updated_at: now(),
            updated_by: whoAmI().full_name,
          };
    return modelNow;
  },
  handovers: async (): Promise<InboxEntry[]> =>
    queue.slice(0, 1).map((e) => ({
      triage_id: e.triage_id,
      text: "FAKE mock handover: findings line\nwhat set the colour\nWaited 0 min since arrival",
      released_by: "FAKE Nurse A",
      released_at: now(),
      patient_label: e.patient_label,
      colour: e.colour,
      waited_minutes: e.waited_minutes,
      target_minutes: e.target_minutes,
      recheck_due: e.recheck_due,
    })),
  colours: async () => FAKE_COLOURS,
  intake: async (patient_label: string, _complaint: string, _vitals: Vitals) => {
    void _complaint;
    void _vitals;
    return { triage_id: `mock-run-${patient_label.length}-${Date.now()}` };
  },
  decide: async (runId: string, decision: Decision): Promise<Committed> => {
    const committed: Committed = {
      triage_id: `mock-${runId}`,
      colour: decision.action === "override" ? (decision.colour ?? "orange") : "orange",
      approver: whoAmI().full_name,
      action: decision.action,
      committed_at: now(),
    };
    queue = [
      ...queue,
      {
        triage_id: committed.triage_id,
        patient_label: "Test Patient 01",
        colour: committed.colour,
        arrived_at: now(),
        waited_minutes: 0,
        minutes_since_triage: 0,
        target_minutes: 10,
        recheck_due: false,
        recheck_raised: false,
      },
    ];
    const on = streams.get(runId);
    on?.step(step("tool_call", "commit_triage", { triage_id: committed.triage_id, approval_token: "(hidden)" }, committed) as never);
    on?.committed(committed as never);
    return committed;
  },
  remeasure: async (runId: string, _vitals: Vitals) => {
    void _vitals;
    return { triage_id: runId };
  },
  assess: async () => ({}),
  shadow: async (): Promise<Shadow> => ({ assessed: 0, agreed: 0, agreement_rate: null, disagreements: [] }),
  queue: async () => queue,
  record: async (triageId: string): Promise<{ triage: PatientRecord }> => ({
    triage: {
      id: triageId,
      patient_label: "Test Patient 01",
      complaint: "FAKE mock complaint: chest pain since this morning",
      vitals: { respiratory_rate: 24, heart_rate: 88, systolic_bp: null, temperature: null, avpu: "alert" },
      proposal: FAKE_PROPOSAL_FOR_RECORD,
      colour: "orange",
      approver: "Nurse A",
      decision: { action: "approve", colour: null, reason: null },
      arrived_at: now(),
      committed_at: now(),
      retriage_of: null,
    },
  }),
  retriage: async (triageId: string, _vitals: Vitals) => {
    void _vitals;
    return { triage_id: `mock-retriage-${triageId}` };
  },
  draftHandover: async (triageId: string): Promise<HandoverDraft> => ({
    triage_id: triageId,
    draft_id: 1,
    status: "awaiting_release",
    text: "FAKE mock handover: findings line\nwhat set the colour\ntime waited",
  }),
  releaseHandover: async (triageId: string, _draftId: number): Promise<HandoverReleased> => {
    void _draftId;
    return { triage_id: triageId, text: "FAKE mock handover", approver: whoAmI().full_name, released_at: now() };
  },
  calledIn: async (triageId: string): Promise<CalledIn> => {
    queue = queue.filter((e) => e.triage_id !== triageId);
    return { triage_id: triageId, approver: whoAmI().full_name, called_in_at: now(), waited_minutes: 0 };
  },
  audit: async () => audit,
  verify: async () => ({ intact: true, first_broken_row: null }),
};

export function openStream(runId: string, on: StreamHandlers): () => void {
  const triageId = `mock-${runId}`;
  streams.set(runId, on);
  const events: ["step" | "proposal", unknown][] = [
    ["step", step("model", "extract", { attempt: 1 }, { tool_calls: [], tokens: 1640, tokens_in: 1520, tokens_out: 120, model: "FAKE mock model" })],
    ["step", step("tool_call", "validate_vitals", { vitals: {} }, [])],
    ["step", step("tool_call", "compute_tews", { vitals: {} }, { total: 2 })],
    ["step", step("tool_call", "match_discriminators", { discriminators: [{ id: "chest_pain", quote: "chest pain since this morning" }] }, {})],
    ["step", step("tool_call", "propose_priority", {}, { triage_id: triageId })],
    ["step", step("waiting_for_nurse", "nurse", { triage_id: triageId }, null)],
    ["proposal", { proposal: fakeProposal(triageId), error: null }],
  ];
  const timers = events.map(([event, data], i) => setTimeout(() => on[event](data as never), 400 * (i + 1)));
  return () => {
    timers.forEach(clearTimeout);
    streams.delete(runId);
  };
}
