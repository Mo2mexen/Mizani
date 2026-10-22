// Every call to the Mizani API, in one place. With VITE_USE_MOCK=true (web/.env.local) the same
// functions answer from src/mock/ instead, so the screens work without the backend.
import * as mock from "./mock";
import type {
  AuditRow,
  Colour,
  Committed,
  Decision,
  HandoverDraft,
  HandoverReleased,
  QueueEntry,
  Shadow,
  Vitals,
} from "./types";

const USE_MOCK = import.meta.env.VITE_USE_MOCK === "true";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
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
      colours: () => call<Colour[]>("GET", "/api/colours"),
      intake: (patient_label: string, complaint: string, vitals: Vitals) =>
        call<{ triage_id: string }>("POST", "/api/intake", { patient_label, complaint, vitals }),
      decide: (runId: string, decision: Decision) =>
        call<Committed>("POST", `/api/triage/${runId}/decision`, decision),
      remeasure: (runId: string, vitals: Vitals) =>
        call<{ triage_id: string }>("POST", `/api/triage/${runId}/vitals`, { vitals }),
      assess: (runId: string, nurse_name: string, colour: string, reason?: string) =>
        call<unknown>("POST", `/api/triage/${runId}/assessment`, { nurse_name, colour, reason }),
      shadow: () => call<Shadow>("GET", "/api/shadow"),
      queue: () => call<QueueEntry[]>("GET", "/api/queue"),
      retriage: (triageId: string, vitals: Vitals) =>
        call<{ triage_id: string }>("POST", `/api/queue/${triageId}/retriage`, { vitals }),
      draftHandover: (triageId: string) =>
        call<HandoverDraft>("POST", `/api/queue/${triageId}/handover`),
      releaseHandover: (triageId: string, draftId: number, nurse_name: string) =>
        call<HandoverReleased>("POST", `/api/queue/${triageId}/handover/${draftId}/release`, { nurse_name }),
      audit: () => call<AuditRow[]>("GET", "/api/audit"),
      verify: () => call<{ intact: boolean; first_broken_row: number | null }>("GET", "/api/audit/verify"),
    };

/** The run's server-sent events: "step", "proposal", "committed", "error". Returns a close function. */
export function openStream(
  runId: string,
  on: { [event in "step" | "proposal" | "committed" | "error"]: (data: never) => void },
): () => void {
  if (USE_MOCK) return mock.openStream(runId, on);
  const source = new EventSource(`/api/triage/${runId}/stream`);
  for (const event of ["step", "proposal", "committed", "error"] as const) {
    source.addEventListener(event, (e) => {
      on[event](JSON.parse((e as MessageEvent).data) as never);
      if (event === "committed" || event === "error") source.close(); // the stream ends here
    });
  }
  return () => source.close();
}
