import type { Vitals } from "../types";
import { NUMBERS, type VitalsForm } from "../vitals";

interface Props {
  value: VitalsForm;
  onChange: (next: VitalsForm) => void;
  highlight?: Set<string>; // fields the backend asked to re-measure
  idPrefix: string;
}

export function VitalsFields({ value, onChange, highlight, idPrefix }: Props) {
  const set = (key: keyof Vitals, v: string) => onChange({ ...value, [key]: v });
  const cls = (key: string) => (highlight?.has(key) ? "field field-flagged" : "field");
  return (
    <div className="vitals-grid">
      {NUMBERS.map(({ key, label, unit, step, note }) => (
        <label key={key} className={cls(key)} htmlFor={`${idPrefix}-${key}`}>
          <span className="field-label">
            {label} <span className="unit">{unit}</span>
          </span>
          <input
            id={`${idPrefix}-${key}`}
            type="number"
            inputMode="decimal"
            step={step ?? "1"}
            value={value[key]}
            onChange={(e) => set(key, e.target.value)}
          />
          {note && <span className="field-note">{note}</span>}
        </label>
      ))}
      <label className={cls("avpu")} htmlFor={`${idPrefix}-avpu`}>
        <span className="field-label">Consciousness</span>
        <select id={`${idPrefix}-avpu`} value={value.avpu} onChange={(e) => set("avpu", e.target.value)}>
          <option value="">not measured</option>
          <option value="alert">Alert</option>
          <option value="confused">Confused</option>
          <option value="voice">Responds to voice</option>
          <option value="pain">Responds to pain</option>
          <option value="unresponsive">Unresponsive</option>
        </select>
      </label>
      <label className={cls("mobility")} htmlFor={`${idPrefix}-mobility`}>
        <span className="field-label">Mobility</span>
        <select id={`${idPrefix}-mobility`} value={value.mobility} onChange={(e) => set("mobility", e.target.value)}>
          <option value="">not measured</option>
          <option value="walking">Walking</option>
          <option value="with_help">With help</option>
          <option value="stretcher">Stretcher / immobile</option>
        </select>
      </label>
      <label className={cls("trauma")} htmlFor={`${idPrefix}-trauma`}>
        <span className="field-label">Injury in past 48 h</span>
        <select id={`${idPrefix}-trauma`} value={value.trauma} onChange={(e) => set("trauma", e.target.value)}>
          <option value="">not asked</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      </label>
      <label className={cls("ketonuria")} htmlFor={`${idPrefix}-ketonuria`}>
        <span className="field-label">Urine ketones</span>
        <select id={`${idPrefix}-ketonuria`} value={value.ketonuria} onChange={(e) => set("ketonuria", e.target.value)}>
          <option value="">not tested</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      </label>
    </div>
  );
}
