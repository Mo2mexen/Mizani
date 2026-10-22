// Fake API for building the screens without the backend (VITE_USE_MOCK=true).
// FAKE DATA: every colour, target time, rule and patient below is made up for the mock only.
// The real values come from the API (rule pack and engine); the UI never hard-codes them.
import type {
  AuditRow,
  Colour,
  Committed,
  Decision,
  HandoverDraft,
  HandoverReleased,
  Proposal,
  QueueEntry,
  Shadow,
  TraceStep,
  Vitals,
} from "../types";

const FAKE_COLOURS: Colour[] = [
  { colour: "red", label: "Emergency", target_minutes: 0, source: "FAKE mock target" },
  { colour: "orange", label: "Very urgent", target_minutes: 10, source: "FAKE mock target" },
  { colour: "yellow", label: "Urgent", target_minutes: 60, source: "FAKE mock target" },
  { colour: "green", label: "Routine", target_minutes: 240, source: "FAKE mock target" },
];

let queue: QueueEntry[] = [];
type Handlers = { [event in "step" | "proposal" | "committed" | "error"]: (data: never) => void };
const streams = new Map<string, Handlers>(); // like the API, a decision ends the run's stream
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
    discriminators: [{ name: "Chest pain", colour: "orange", source: "FAKE mock rule" }],
    flags: [
      { kind: "remeasure", message: "Temperature is missing (FAKE mock)", source: "input: temperature" },
      { kind: "investigation", message: "Immediate ECG (FAKE mock)", source: "FAKE mock rule" },
    ],
  };
}

function step(kind: TraceStep["kind"], name: string, input: unknown, output: unknown): TraceStep {
  return { time: now(), kind, name, input, output, latency_ms: Math.round(Math.random() * 40) };
}

export const api = {
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
      approver: decision.nurse_name,
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
  releaseHandover: async (triageId: string, _draftId: number, nurse_name: string): Promise<HandoverReleased> => {
    void _draftId;
    return { triage_id: triageId, text: "FAKE mock handover", approver: nurse_name, released_at: now() };
  },
  audit: async () => audit,
  verify: async () => ({ intact: true, first_broken_row: null }),
};

export function openStream(
  runId: string,
  on: { [event in "step" | "proposal" | "committed" | "error"]: (data: never) => void },
): () => void {
  const triageId = `mock-${runId}`;
  streams.set(runId, on);
  const events: [keyof typeof on, unknown][] = [
    ["step", step("model", "extract", { attempt: 1 }, { tool_calls: [], tokens: 1640, model: "FAKE mock model" })],
    ["step", step("tool_call", "validate_vitals", { vitals: {} }, [])],
    ["step", step("tool_call", "compute_tews", { vitals: {} }, { total: 2 })],
    ["step", step("tool_call", "match_discriminators", { discriminator_ids: ["chest_pain"] }, {})],
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
