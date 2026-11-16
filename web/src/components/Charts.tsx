import { useEffect, useState } from "react";
import { api } from "../api";
import { clock, day } from "../format";
import type { ChartDetail, ChartOverview, ChartSummary } from "../types";

const REFRESH_MS = 5000; // while an evaluation runs
const SECONDS_PER_RUN = 15; // measured on a laptop with a 4 GB GPU, the model warm
const BUILT_IN_PATIENTS = 39; // evals/cases/core.yaml, used when a chart comes without its own

const STATUS: Record<ChartSummary["status"], string> = {
  uploaded: "Checked, not evaluated yet",
  evaluating: "Evaluating",
  evaluated: "Evaluated",
  failed: "Evaluation stopped",
  active: "In use",
  retired: "Was in use",
};

function percent(share: number): string {
  return `${Math.round(share * 100)}%`;
}

/** Charts, for the chief doctor: the chart in use, uploading a new one with its test patients,
 *  its evaluation on the desk's model, the report, and activation with the chief doctor's approval.
 *  Every value a chart gives still cites its rule; the server refuses a chart that does not. */
export function Charts() {
  const [overview, setOverview] = useState<ChartOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const isRunning = overview?.charts.some((c) => c.status === "evaluating") ?? false;

  function load() {
    return api.charts().then(
      (o) => {
        setOverview(o);
        setError(null);
      },
      (err: unknown) => setError(err instanceof Error ? err.message : "Not loaded"),
    );
  }

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (!isRunning) return;
    const id = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(id);
  }, [isRunning]);

  async function goBack() {
    setError(null);
    try {
      const done = await api.rollbackChart();
      setOverview(done.overview);
      setNote(`${done.in_use} is in use again, activated under your name.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not changed");
    }
  }

  return (
    <main className="charts">
      <section className="panel" aria-labelledby="in-use-title">
        <h2 id="in-use-title" className="panel-title">
          Chart in use
        </h2>
        {overview && (
          <>
            <p className="chart-in-use">
              <strong>{overview.active.name}</strong> <code>{overview.active.label}</code>
            </p>
            <p className="hint">
              {overview.active.chart?.activated_by && overview.active.chart.activated_at
                ? `Activated by ${overview.active.chart.activated_by} on ${day(overview.active.chart.activated_at)}, ${clock(overview.active.chart.activated_at)}.`
                : "The chart Mizani came with. Every proposal shows which chart scored it."}
            </p>
            {overview.previous_label && (
              <GoBack label={overview.previous_label} onConfirm={() => void goBack()} />
            )}
          </>
        )}
        {note && (
          <p className="ok" role="status">
            {note}
          </p>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </section>

      <Upload onUploaded={() => void load()} />

      <section className="panel" aria-labelledby="uploaded-title">
        <h2 id="uploaded-title" className="panel-title">
          Uploaded charts {overview && <span className="count">{overview.charts.length}</span>}
        </h2>
        {overview?.charts.length === 0 && (
          <p className="empty">No chart uploaded yet. Upload one above; it scores nobody until you activate it.</p>
        )}
        <ul className="chart-list">
          {overview?.charts.map((c) => (
            <ChartRow
              key={c.id}
              chart={c}
              isOpen={open === c.id}
              isOtherRunning={isRunning && c.status !== "evaluating"}
              onToggle={() => setOpen(open === c.id ? null : c.id)}
              onChanged={(o) => (o ? setOverview(o) : void load())}
            />
          ))}
        </ul>
      </section>
    </main>
  );
}

function GoBack({ label, onConfirm }: { label: string; onConfirm: () => void }) {
  const [isAsking, setIsAsking] = useState(false);
  return isAsking ? (
    <div className="confirm-box" role="group" aria-label="Confirm">
      <p>Go back to {label}? New patients are scored with it at once; patients already waiting keep their colour.</p>
      <div className="decision-row">
        <button type="button" className="btn btn-small btn-primary" onClick={onConfirm}>
          Go back to {label}
        </button>
        <button type="button" className="btn btn-small" onClick={() => setIsAsking(false)}>
          Cancel
        </button>
      </div>
    </div>
  ) : (
    <button type="button" className="btn btn-small" onClick={() => setIsAsking(true)}>
      Go back to {label}
    </button>
  );
}

function Upload({ onUploaded }: { onUploaded: () => void }) {
  const [chartFile, setChartFile] = useState<File | null>(null);
  const [casesFile, setCasesFile] = useState<File | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formKey, setFormKey] = useState(0);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!chartFile) return;
    setIsBusy(true);
    setError(null);
    try {
      await api.uploadChart(await chartFile.text(), casesFile ? await casesFile.text() : null);
      onUploaded();
      setChartFile(null);
      setCasesFile(null);
      setFormKey((k) => k + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not uploaded");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <form key={formKey} className="panel" onSubmit={handleSubmit} aria-labelledby="upload-title">
      <h2 id="upload-title" className="panel-title">
        Upload a chart
      </h2>
      <p className="hint">
        A chart is a YAML rule pack like the one Mizani came with: every rule cites the chart or manual it comes from.
        Mizani checks it at once and refuses a rule without a source, a vital value that falls in no band, or a colour
        the chart does not list. Never a PDF: a chart the model read could not cite its rules.
      </p>
      <div className="upload-fields">
        <label className="field" htmlFor="chart-file">
          <span className="field-label">Chart file (.yaml)</span>
          <input
            id="chart-file"
            type="file"
            accept=".yaml,.yml,text/yaml"
            onChange={(e) => setChartFile(e.target.files?.[0] ?? null)}
            required
          />
        </label>
        <label className="field" htmlFor="cases-file">
          <span className="field-label">
            Test patients (.yaml) <span className="unit">optional</span>
          </span>
          <input
            id="cases-file"
            type="file"
            accept=".yaml,.yml,text/yaml"
            onChange={(e) => setCasesFile(e.target.files?.[0] ?? null)}
          />
          <span className="field-note">
            Made-up patients with the answer you expect for each (the format of evals/cases/core.yaml). Without them
            the report can only say how often the new chart agrees with the one in use, not how often it is right.
          </span>
        </label>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="btn btn-primary" disabled={isBusy || !chartFile}>
        {isBusy ? "Checking…" : "Upload and check"}
      </button>
    </form>
  );
}

function ChartRow({
  chart,
  isOpen,
  isOtherRunning,
  onToggle,
  onChanged,
}: {
  chart: ChartSummary;
  isOpen: boolean;
  isOtherRunning: boolean;
  onToggle: () => void;
  onChanged: (overview?: ChartOverview) => void;
}) {
  const [asking, setAsking] = useState<"evaluate" | "activate" | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const patients = chart.test_patients || BUILT_IN_PATIENTS;
  const minutes = Math.max(1, Math.round(((patients * 5 + 2) * SECONDS_PER_RUN) / 60));
  const canEvaluate = ["uploaded", "evaluated", "failed"].includes(chart.status) && !isOtherRunning;
  const canActivate = chart.status === "evaluated" && chart.safety_ok;

  async function act(work: () => Promise<ChartOverview | undefined>) {
    setIsBusy(true);
    setError(null);
    try {
      onChanged(await work());
      setAsking(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Not done");
    } finally {
      setIsBusy(false);
    }
  }

  const state =
    chart.status === "evaluating"
      ? `Evaluating: ${chart.progress ?? "starting"}`
      : chart.status === "evaluated"
        ? chart.safety_ok
          ? "Evaluated: every safety check held"
          : "Evaluated: a safety check failed"
        : chart.status === "failed"
          ? `Evaluation stopped${chart.progress ? `: ${chart.progress}` : ""}`
          : STATUS[chart.status];

  return (
    <li className={`chart-row status-${chart.status}${chart.status === "evaluated" && !chart.safety_ok ? " is-unsafe" : ""}`}>
      <div className="chart-row-head">
        <strong>{chart.name}</strong> <code>{chart.label}</code>
        <span className="chart-state">{state}</span>
      </div>
      <p className="card-time">
        Uploaded by {chart.uploaded_by} on {day(chart.uploaded_at)}.{" "}
        {chart.test_patients
          ? `${chart.test_patients} test patient${chart.test_patients === 1 ? "" : "s"}.`
          : "No test patients: compared with the chart in use."}
      </p>
      <div className="card-actions">
        {canEvaluate && (
          <button type="button" className="btn btn-small" disabled={isBusy} onClick={() => setAsking("evaluate")}>
            {chart.status === "uploaded" ? "Run evaluation" : "Run evaluation again"}
          </button>
        )}
        {(chart.status === "evaluated" || chart.status === "active" || chart.status === "retired") && (
          <button type="button" className="btn btn-small" aria-expanded={isOpen} onClick={onToggle}>
            Report
          </button>
        )}
        {canActivate && (
          <button type="button" className="btn btn-small btn-primary" disabled={isBusy} onClick={() => setAsking("activate")}>
            Activate
          </button>
        )}
      </div>
      {asking === "evaluate" && (
        <div className="confirm-box" role="group" aria-label="Confirm">
          <p>
            The evaluation runs {patients} test patient{patients === 1 ? "" : "s"} 5 times each on the model the desk
            uses: about {minutes} minute{minutes === 1 ? "" : "s"}, and triage at the desk is slower meanwhile. Run it
            off-hours, or choose another model server in Admin › Model first.
          </p>
          <div className="decision-row">
            <button
              type="button"
              className="btn btn-small btn-primary"
              disabled={isBusy}
              onClick={() => void act(async () => (await api.evaluateChart(chart.id), undefined))}
            >
              Start the evaluation
            </button>
            <button type="button" className="btn btn-small" onClick={() => setAsking(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {asking === "activate" && (
        <div className="confirm-box" role="group" aria-label="Confirm">
          <p>
            Activate {chart.label}? Every new patient is scored with it, under your approval; patients already waiting
            keep their colour. You can go back to the chart in use now with one click.
          </p>
          <div className="decision-row">
            <button
              type="button"
              className="btn btn-small btn-primary"
              disabled={isBusy}
              onClick={() => void act(async () => (await api.activateChart(chart.id)).overview)}
            >
              Activate {chart.label}
            </button>
            <button type="button" className="btn btn-small" onClick={() => setAsking(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {isOpen && <Report chartId={chart.id} />}
    </li>
  );
}

function Report({ chartId }: { chartId: number }) {
  const [detail, setDetail] = useState<ChartDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.chart(chartId).then(setDetail, (err: unknown) => setError(err instanceof Error ? err.message : "Not loaded"));
  }, [chartId]);

  if (error) return <p className="error">{error}</p>;
  if (!detail) return <p className="empty">Loading the report…</p>;
  const r = detail.report;
  if (!r) return <p className="empty">No report yet: run the evaluation first.</p>;
  const runs = r.patients.length * r.runs_per_patient;
  const changed = r.patients.filter((p) => p.changes);
  return (
    <div className="report">
      <p className="report-head">
        {r.mode === "pass rate" ? (
          <>
            Pass rate: <strong>{percent(r.pass_rate ?? 0)}</strong> of {runs} runs against your test patients'
            expected answers.
          </>
        ) : (
          <>
            Agreement with the chart in use: <strong>{percent(r.agreement)}</strong> of {runs} runs. This is agreement,
            not accuracy: the chart came without test patients, so nothing says which answer is right.
          </>
        )}
      </p>
      <p className="hint">
        Model {r.model}, {r.runs_per_patient} runs per patient, compared with {r.against}. It took{" "}
        {Math.round(r.seconds / 60)} min.
      </p>
      <h3 className="sub-title">Safety checks {r.safety_ok ? "(all held)" : "(one failed: it cannot be activated)"}</h3>
      <ul className="safety">
        {r.safety.map((s) => (
          <li key={s.check} className={s.ok === false ? "is-failed" : undefined}>
            <span className="safety-result">{s.ok === null ? "Not checked" : s.ok ? "Held" : "Failed"}</span>
            <span>
              {s.check}
              <span className="src">{s.detail}</span>
            </span>
          </li>
        ))}
      </ul>
      <h3 className="sub-title">Patients whose colour changes against the chart in use ({changed.length})</h3>
      {changed.length === 0 ? (
        <p className="empty">None: every test patient gets the same colour as with the chart in use.</p>
      ) : (
        <table className="audit">
          <thead>
            <tr>
              <th scope="col">Patient</th>
              <th scope="col">Chart in use</th>
              <th scope="col">This chart</th>
            </tr>
          </thead>
          <tbody>
            {changed.map((p) => (
              <tr key={p.id}>
                <td>{p.id}</td>
                <td>{p.in_use_colour.toUpperCase()}</td>
                <td>
                  <strong>{p.colour.toUpperCase()}</strong>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {r.mode === "pass rate" && (
        <>
          <h3 className="sub-title">Each test patient</h3>
          <table className="audit">
            <thead>
              <tr>
                <th scope="col">Patient</th>
                <th scope="col" className="num">
                  Passed
                </th>
                <th scope="col">Colour</th>
                <th scope="col">Failed checks</th>
              </tr>
            </thead>
            <tbody>
              {r.patients.map((p) => (
                <tr key={p.id}>
                  <td>{p.id}</td>
                  <td className="num">
                    {p.passes} of {p.runs}
                  </td>
                  <td>{p.colour.toUpperCase()}</td>
                  <td>
                    {Object.entries(p.failed_checks)
                      .map(([check, n]) => `${check} (${n})`)
                      .join("; ") || "none"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
