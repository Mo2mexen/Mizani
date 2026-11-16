// Every call to the Mizani API, in one place. With VITE_USE_MOCK=true (web/.env.local) the same
// functions answer from src/mock/ instead, so the screens work without the backend.
import * as mock from "./mock";
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
  NewAccount,
  PatientRecord,
  QueueEntry,
  Role,
  Shadow,
  Vitals,
} from "./types";

const USE_MOCK = import.meta.env.VITE_USE_MOCK === "true";

/** Sent on window when the server answers 401: the session ended (idle, signed out elsewhere, or by an admin). */
export const SIGNED_OUT_EVENT = "mizani:signed-out";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** `background` marks the page's own polling, which does not count as use: an idle tablet still signs out. */
async function call<T>(method: "GET" | "POST", path: string, body?: unknown, background = false): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-Mizani"] = "1"; // the server refuses a change without it (another site cannot send it)
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (background) headers["X-Mizani-Background"] = "1";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/auth/login") window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
    throw new ApiError(response.status, readableError(data) ?? `${method} ${path}: ${response.status}`);
  }
  return data as T;
}

// FastAPI sends { detail: string } for refusals and { detail: [{ loc, msg }] } for invalid input.
function readableError(data: unknown): string | null {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`).join("; ");
  }
  return null;
}

export const api = USE_MOCK
  ? mock.api
  : {
      // Signing in and the account
      me: () => call<Account>("GET", "/api/auth/me"),
      login: (username: string, password: string) => call<Account>("POST", "/api/auth/login", { username, password }),
      logout: () => call<{ signed_out: boolean }>("POST", "/api/auth/logout"),
      changePassword: (current: string, next: string) =>
        call<Account>("POST", "/api/auth/password", { current, new: next }),
      // Admin › Users
      users: () => call<Account[]>("GET", "/api/admin/users"),
      addUser: (username: string, full_name: string, role: Role) =>
        call<NewAccount>("POST", "/api/admin/users", { username, full_name, role }),
      resetPassword: (id: number) =>
        call<{ one_time_password: string }>("POST", `/api/admin/users/${id}/reset-password`),
      setRole: (id: number, role: Role) => call<Account>("POST", `/api/admin/users/${id}/role`, { role }),
      setChief: (id: number, chief: boolean) => call<Account>("POST", `/api/admin/users/${id}/chief`, { chief }),
      setActive: (id: number, active: boolean) => call<Account>("POST", `/api/admin/users/${id}/active`, { active }),
      signOutEverywhere: (id: number) => call<{ signed_out: boolean }>("POST", `/api/admin/users/${id}/sign-out`),
      // The desk
      colours: () => call<Colour[]>("GET", "/api/colours", undefined, true),
      intake: (patient_label: string, complaint: string, vitals: Vitals) =>
        call<{ triage_id: string }>("POST", "/api/intake", { patient_label, complaint, vitals }),
      decide: (runId: string, decision: Decision) =>
        call<Committed>("POST", `/api/triage/${runId}/decision`, decision),
      remeasure: (runId: string, vitals: Vitals) =>
        call<{ triage_id: string }>("POST", `/api/triage/${runId}/vitals`, { vitals }),
      assess: (runId: string, colour: string, reason?: string) =>
        call<unknown>("POST", `/api/triage/${runId}/assessment`, { colour, reason }),
      shadow: () => call<Shadow>("GET", "/api/shadow"),
      queue: () => call<QueueEntry[]>("GET", "/api/queue", undefined, true),
      record: (triageId: string) => call<{ triage: PatientRecord }>("GET", `/api/queue/${triageId}`),
      retriage: (triageId: string, vitals: Vitals) =>
        call<{ triage_id: string }>("POST", `/api/queue/${triageId}/retriage`, { vitals }),
      draftHandover: (triageId: string) =>
        call<HandoverDraft>("POST", `/api/queue/${triageId}/handover`),
      releaseHandover: (triageId: string, draftId: number) =>
        call<HandoverReleased>("POST", `/api/queue/${triageId}/handover/${draftId}/release`),
      calledIn: (triageId: string) => call<CalledIn>("POST", `/api/queue/${triageId}/called-in`),
      // The doctor's screen
      handovers: () => call<InboxEntry[]>("GET", "/api/handovers", undefined, true),
      audit: () => call<AuditRow[]>("GET", "/api/audit"),
      verify: () => call<{ intact: boolean; first_broken_row: number | null }>("GET", "/api/audit/verify"),
    };

type StreamEvent = "step" | "proposal" | "committed" | "error";
export type StreamHandlers = { [event in StreamEvent]: (data: never) => void } & {
  /** true while the connection is down and the browser retries; false once it is back */
  connection: (isReconnecting: boolean) => void;
};

/** The run's server-sent events: "step", "proposal", "committed", "error". Returns a close function. */
export function openStream(runId: string, on: StreamHandlers): () => void {
  if (USE_MOCK) return mock.openStream(runId, on);
  const source = new EventSource(`/api/triage/${runId}/stream`);
  source.onopen = () => on.connection(false);
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) {
      on.error({ message: "Lost the connection to the API. Reload the page to continue." } as never);
    } else {
      on.connection(true); // the browser retries by itself and resumes with Last-Event-ID
    }
  };
  for (const event of ["step", "proposal", "committed", "error"] as const) {
    source.addEventListener(event, (e) => {
      // A plain "error" Event (no data) is the connection dropping, handled by onerror above.
      if (!(e instanceof MessageEvent) || typeof e.data !== "string") return;
      on[event](JSON.parse(e.data) as never);
      if (event === "committed" || event === "error") source.close(); // the stream ends here
    });
  }
  return () => source.close();
}
