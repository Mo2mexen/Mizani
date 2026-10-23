// Display helpers only. Nothing here computes a colour, a score or a threshold.
import type { Colour, FlagKind } from "./types";

export const FIELD_LABELS: Record<string, string> = {
  respiratory_rate: "Respiratory rate",
  heart_rate: "Pulse",
  systolic_bp: "Systolic BP",
  temperature: "Temperature",
  spo2: "SpO₂",
  avpu: "Consciousness (AVPU)",
  mobility: "Mobility",
  trauma: "Trauma (48 h)",
  glucose: "Glucose",
  ketonuria: "Urine ketones",
};

export const FLAG_LABELS: Record<FlagKind, string> = {
  remeasure: "Re-measure",
  incomplete: "Incomplete",
  investigation: "Investigation",
  not_on_chart: "Not on chart, ignored",
  conflicts_with_vitals: "Conflicts with vitals, ignored",
  not_in_complaint: "Not in the complaint, ignored",
  ignored_instruction: "Instruction in the complaint, ignored",
  confirm_sign: "Nurse to confirm",
  retriage: "Re-triage",
  recheck_due: "Re-check due",
};

/** The colour's label from the rule pack, e.g. "Very urgent"; the colour name itself if unknown. */
export function colourLabel(colours: Colour[], colour: string): string {
  return colours.find((c) => c.colour === colour)?.label ?? colour;
}

export function minutes(value: number): string {
  if (value < 1) return "<1 min";
  const total = Math.round(value); // round once, so 179.7 is "3 h", never "2 h 60 min"
  if (total < 120) return `${total} min`;
  const rest = total % 60;
  return rest ? `${Math.floor(total / 60)} h ${rest} min` : `${total / 60} h`;
}

/** One time format everywhere, 12-hour with seconds ("3:13:48 PM"), whatever the browser's language. */
export function clock(time: string | Date): string {
  return new Date(time).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true });
}

export function short(value: unknown, max = 160): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (text === undefined) return "";
  return text.length > max ? `${text.slice(0, max)}…` : text;
}
