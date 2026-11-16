import { useEffect, useState } from "react";
import { api } from "../api";
import { clock, day } from "../format";
import type { ModelChoice, ModelLocation, ModelSetting, ModelTest } from "../types";

const PLACES: { location: ModelLocation; title: string; what: string }[] = [
  {
    location: "this_server",
    title: "This server",
    what: "The model that came with Mizani, on this server. Complaint text never leaves it. Without a GPU it is the slowest choice.",
  },
  {
    location: "hospital",
    title: "A GPU server in this hospital",
    what: "A faster or larger model on another machine on the hospital network. Complaint text goes to that machine and stays inside the hospital.",
  },
  {
    location: "cloud",
    title: "A cloud GPU server",
    what: "A server outside the hospital, for example a GPU rented for evaluations. Complaint text leaves the hospital.",
  },
];

const WHERE: Record<ModelLocation, string> = {
  this_server: "this server",
  hospital: "a GPU server in this hospital",
  cloud: "a cloud GPU server",
};

/** Admin › Model: where the model runs. It drives the same settings as `.env` (address, model, key,
 *  thinking), stored on the server; the key is never shown again. A server outside the hospital
 *  needs an explicit confirmation, and every change is in the audit log. */
export function AdminModel() {
  const [now, setNow] = useState<ModelSetting | null>(null);
  const [location, setLocation] = useState<ModelLocation>("this_server");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [effort, setEffort] = useState("none");
  const [isConfirmed, setIsConfirmed] = useState(false);
  const [test, setTest] = useState<{ result: ModelTest; for: string } | null>(null);
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function show(setting: ModelSetting) {
    setNow(setting);
    setLocation(setting.location);
    setBaseUrl(setting.location === "this_server" ? "" : setting.base_url);
    setModel(setting.location === "this_server" ? "" : setting.model);
    setEffort(setting.reasoning_effort ?? "");
    setApiKey("");
    setIsConfirmed(false);
  }

  useEffect(() => {
    api.modelSetting().then(show, (err: unknown) => setError(err instanceof Error ? err.message : "Not loaded"));
  }, []);

  const choice: ModelChoice =
    location === "this_server"
      ? { location }
      : {
          location,
          base_url: baseUrl.trim(),
          model: model.trim(),
          api_key: apiKey,
          reasoning_effort: effort,
          leaves_hospital_confirmed: location === "cloud" && isConfirmed,
        };
  const fingerprint = JSON.stringify({ ...choice, leaves_hospital_confirmed: undefined });
  const isTested = test?.for === fingerprint && test.result.ok;
  const isComplete = location === "this_server" || (baseUrl.trim() !== "" && model.trim() !== "");
  const keySaved = now?.has_key && now.location === location && now.base_url === baseUrl.trim();
  const canSave =
    busy === null &&
    isComplete &&
    (location === "this_server" ? now?.location !== "this_server" : isTested) &&
    (location !== "cloud" || isConfirmed);

  function changed<T>(set: (v: T) => void) {
    return (value: T) => {
      set(value);
      setSaved(false);
      setError(null);
    };
  }

  async function runTest() {
    setBusy("test");
    setError(null);
    try {
      setTest({ result: await api.testModel(choice), for: fingerprint });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not tested");
    } finally {
      setBusy(null);
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!canSave) return;
    setBusy("save");
    setError(null);
    try {
      show(await api.saveModel(choice));
      setSaved(true);
      setTest(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not saved");
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="admin-model">
      <form className="panel" onSubmit={save} aria-labelledby="model-title">
        <h2 id="model-title" className="panel-title">
          Where the model runs
        </h2>
        {now && (
          <p className="model-now">
            Now: <strong>{WHERE[now.location]}</strong>, model <code>{now.model}</code>
            {now.location !== "this_server" && (
              <>
                {" "}
                at <code>{now.base_url}</code>
              </>
            )}
            .{" "}
            {now.updated_by && now.updated_at
              ? `Chosen by ${now.updated_by} on ${day(now.updated_at)}, ${clock(now.updated_at)}.`
              : "Set up when Mizani was installed."}
          </p>
        )}
        <p className="hint">
          The model only reads the complaint and names chart signs; the colour always comes from the chart's rules. A
          larger model on a GPU should read complaints better and answer faster; evaluate a new model before nurses
          rely on it.
        </p>
        <fieldset className="choices">
          <legend className="visually-hidden">Where the model runs</legend>
          {PLACES.map((p) => (
            <label key={p.location} className={`choice${location === p.location ? " is-on" : ""}`}>
              <input
                type="radio"
                name="location"
                value={p.location}
                checked={location === p.location}
                onChange={() => changed(setLocation)(p.location)}
              />
              <span>
                <strong>{p.title}</strong>
                <span className="field-note">
                  {p.what}
                  {p.location === "this_server" && now && ` Model: ${now.this_server.model}.`}
                </span>
              </span>
            </label>
          ))}
        </fieldset>

        {location !== "this_server" && (
          <div className="model-fields">
            <label className="field" htmlFor="model-url">
              <span className="field-label">Address</span>
              <input
                id="model-url"
                value={baseUrl}
                onChange={(e) => changed(setBaseUrl)(e.target.value)}
                placeholder={location === "cloud" ? "https://gpu.example.org/v1" : "http://10.0.0.5:11434/v1"}
                spellCheck={false}
                autoCapitalize="none"
              />
              <span className="field-note">
                An OpenAI-compatible address, ending in /v1 (Ollama, vLLM, llama.cpp).
                {location === "cloud" && " It must start with https://, so the text and the key are encrypted."}
              </span>
            </label>
            <label className="field" htmlFor="model-name">
              <span className="field-label">Model name</span>
              <input
                id="model-name"
                value={model}
                onChange={(e) => changed(setModel)(e.target.value)}
                placeholder="qwen3.5:9b-q4_K_M"
                spellCheck={false}
                autoCapitalize="none"
              />
            </label>
            <label className="field" htmlFor="model-key">
              <span className="field-label">API key</span>
              <input
                id="model-key"
                type="password"
                value={apiKey}
                onChange={(e) => changed(setApiKey)(e.target.value)}
                placeholder={keySaved ? "A key is saved; leave empty to keep it" : "Only if the server asks for one"}
                autoComplete="off"
              />
              <span className="field-note">Kept on this server. It is never shown again or written to the audit log.</span>
            </label>
            <label className="field" htmlFor="model-effort">
              <span className="field-label">Thinking</span>
              <select id="model-effort" value={effort} onChange={(e) => changed(setEffort)(e.target.value)}>
                <option value="none">Off (sends "none"; right for Ollama)</option>
                <option value="">Not sent (for a server that rejects the setting)</option>
              </select>
            </label>
          </div>
        )}

        {location === "cloud" && (
          <div className="leaves" role="group" aria-labelledby="leaves-title">
            <p id="leaves-title" className="leaves-title">
              Complaint text will leave this hospital
            </p>
            <p>
              Every patient's complaint and vital signs go to this server to be read. Use it only where the hospital's
              data-protection rules allow it, and until then with synthetic patients only.
            </p>
            <label className="check">
              <input type="checkbox" checked={isConfirmed} onChange={(e) => changed(setIsConfirmed)(e.target.checked)} />
              I understand that complaint text will leave this hospital
            </label>
          </div>
        )}

        <div className="decision-row">
          <button type="button" className="btn" disabled={busy !== null || !isComplete} onClick={() => void runTest()}>
            {busy === "test" ? "Testing…" : "Test connection"}
          </button>
          <button type="submit" className="btn btn-primary" disabled={!canSave}>
            {busy === "save" ? "Saving…" : "Save"}
          </button>
        </div>
        {test && test.for === fingerprint && (
          <p className={test.result.ok ? "ok" : "error"} role="status">
            {test.result.ok
              ? `Answered in ${test.result.seconds} s (model ${test.result.model}).`
              : `No answer: ${test.result.error}`}
          </p>
        )}
        {location !== "this_server" && isComplete && !isTested && (
          <p className="hint">Test the connection before you save: a wrong address would leave nurses without the model.</p>
        )}
        <p className="hint">A model still loading from disk can take up to three minutes to answer the first time.</p>
        {saved && (
          <p className="ok" role="status">
            Saved. The next patient uses it; a patient already being read finishes on the model it started with.
          </p>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </form>
    </main>
  );
}
