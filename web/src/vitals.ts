import type { Avpu, Mobility, Vitals } from "./types";

// Form values are strings; empty means "not measured", and the backend asks for a re-measure.
export type VitalsForm = Record<keyof Vitals, string>;

export const EMPTY_VITALS: VitalsForm = {
  respiratory_rate: "",
  heart_rate: "",
  systolic_bp: "",
  temperature: "",
  spo2: "",
  avpu: "",
  mobility: "",
  trauma: "",
  glucose: "",
  ketonuria: "",
};

export const NUMBERS: { key: keyof Vitals; label: string; unit: string; step?: string; note?: string }[] = [
  { key: "respiratory_rate", label: "Respiratory rate", unit: "breaths/min" },
  { key: "heart_rate", label: "Pulse", unit: "beats/min" },
  { key: "systolic_bp", label: "Systolic BP", unit: "mmHg" },
  { key: "temperature", label: "Temperature", unit: "°C", step: "0.1" },
  { key: "spo2", label: "SpO₂", unit: "%", note: "not scored; only raises a check" },
  { key: "glucose", label: "Glucose", unit: "mmol/L", step: "0.1", note: "finger-prick, optional" },
];

export function toVitals(form: VitalsForm): Vitals {
  const vitals: Vitals = {};
  for (const { key } of NUMBERS) {
    if (form[key] !== "") (vitals as Record<string, number>)[key] = Number(form[key]);
  }
  if (form.avpu) vitals.avpu = form.avpu as Avpu;
  if (form.mobility) vitals.mobility = form.mobility as Mobility;
  if (form.trauma) vitals.trauma = form.trauma === "yes";
  if (form.ketonuria) vitals.ketonuria = form.ketonuria === "yes";
  return vitals;
}

