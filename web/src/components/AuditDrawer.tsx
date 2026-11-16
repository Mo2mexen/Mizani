import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { clock } from "../format";
import { useAccount } from "../session";
import type { AuditRow, Shadow } from "../types";

type Props = { isOpen: boolean; onClose: () => void };

const HEIGHT_KEY = "mizani.auditHeight"; // a per-person convenience in this browser only
const MIN_HEIGHT = 160;
const maxHeight = () => Math.round(window.innerHeight * 0.85);
const clampHeight = (h: number) => Math.min(Math.max(h, MIN_HEIGHT), maxHeight());

function savedHeight(): number {
  try {
    const saved = Number(localStorage.getItem(HEIGHT_KEY));
    if (saved > 0) return clampHeight(saved);
  } catch {
    // storage can be blocked: the default height is fine
  }
  return clampHeight(Math.round(window.innerHeight * 0.45));
}

const PAGE = 100; // a busy desk logs thousands of calls; drawing them all at once is slow

/** What the model used across the log: tokens in and out, per agent run, and the cost. */
function modelUse(rows: AuditRow[]) {
  const counted = rows.filter((r) => r.tokens_in != null || r.tokens_out != null);
  const runs = new Set(counted.filter((r) => (r.tokens_in ?? 0) + (r.tokens_out ?? 0) > 0).map((r) => r.run_id)).size;
  const tokensIn = counted.reduce((t, r) => t + (r.tokens_in ?? 0), 0);
  const tokensOut = counted.reduce((t, r) => t + (r.tokens_out ?? 0), 0);
  const cost = rows.reduce((t, r) => t + r.cost, 0);
  return { runs, tokensIn, tokensOut, cost };
}

/** Every logged tool call and account event (sign-ins, account changes), newest first, and
 *  whether the hash chain is intact. Escape closes it. */
export function AuditDrawer({ isOpen, onClose }: Props) {
  const isNurse = useAccount().role === "nurse"; // the shadow-mode report is the nurses'
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [isIntact, setIsIntact] = useState<boolean | null>(null);
  const [shadow, setShadow] = useState<Shadow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const [height, setHeight] = useState(savedHeight);
  const dragging = useRef(false);

  // Keep the page's last rows reachable above the drawer, and remember the height
  useEffect(() => {
    if (!isOpen) return;
    document.documentElement.style.setProperty("--audit-drawer", `${height}px`);
    try {
      localStorage.setItem(HEIGHT_KEY, String(height));
    } catch {
      // not saved: it still works for this visit
    }
    return () => {
      document.documentElement.style.removeProperty("--audit-drawer");
    };
  }, [isOpen, height]);
  const [shown, setShown] = useState(PAGE); // newest rows first, a page at a time
  const closeRef = useRef<HTMLButtonElement>(null);

  function load() {
    return Promise.all([api.audit(), api.verify(), isNurse ? api.shadow() : null])
      .then(
        ([audit, verify, agreement]) => {
          setRows(audit);
          setIsIntact(verify.intact);
          setShadow(agreement);
          setError(null);
          setLoadedAt(new Date());
        },
        (err: unknown) => setError(err instanceof Error ? err.message : "Audit not loaded"),
      )
      .finally(() => setIsLoading(false));
  }

  function refresh() {
    setIsLoading(true);
    void load();
  }

  useEffect(() => {
    if (!isOpen) return;
    const opener = document.activeElement; // focus goes back here on close
    void load();
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (opener instanceof HTMLElement) opener.focus();
    };
  }, [isOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!isOpen) return null;
  const use = rows && modelUse(rows);
  return (
    <aside className="drawer" role="dialog" aria-modal="false" aria-labelledby="audit-title" style={{ height }}>
      <div
        className="drawer-resize"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize the audit log: drag, or use the up and down arrow keys"
        aria-valuemin={MIN_HEIGHT}
        aria-valuemax={maxHeight()}
        aria-valuenow={height}
        tabIndex={0}
        onPointerDown={(e) => {
          dragging.current = true;
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (dragging.current) setHeight(clampHeight(window.innerHeight - e.clientY));
        }}
        onPointerUp={() => {
          dragging.current = false;
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowUp" || e.key === "ArrowDown") {
            e.preventDefault();
            setHeight((h) => clampHeight(h + (e.key === "ArrowUp" ? 48 : -48)));
          }
        }}
      >
        <span className="drawer-grip" aria-hidden="true" />
      </div>
      <header className="drawer-head">
        <h2 id="audit-title" className="panel-title">
          Audit log {rows && <span className="count">{rows.length} entries</span>}
        </h2>
        {isIntact !== null && (
          <span className={isIntact ? "chain ok" : "chain broken"}>
            {isIntact ? "hash chain intact" : "HASH CHAIN BROKEN"}
          </span>
        )}
        {shadow && shadow.assessed > 0 && (
          <span className="chain">
            shadow mode: nurse and Mizani agreed {shadow.agreed} of {shadow.assessed}
          </span>
        )}
        {use && use.runs > 0 && (
          <span className="count">
            model: {use.runs} run{use.runs === 1 ? "" : "s"} · on average {Math.round(use.tokensIn / use.runs).toLocaleString()} in ·{" "}
            {Math.round(use.tokensOut / use.runs).toLocaleString()} out · ${use.cost.toFixed(4)} in all
          </span>
        )}
        {loadedAt && <span className="count">updated {clock(loadedAt)}</span>}
        <button type="button" className="btn btn-small" onClick={refresh} disabled={isLoading}>
          {isLoading ? "Refreshing…" : "Refresh"}
        </button>
        <button ref={closeRef} type="button" className="btn btn-small" onClick={onClose}>
          Close
        </button>
      </header>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="drawer-body" tabIndex={0} aria-label="Audit rows, newest first">
        {rows === null && !error && <p className="empty">Loading the audit log…</p>}
        {rows?.length === 0 && (
          <p className="empty">Nothing logged yet. Every tool call and every sign-in is logged here.</p>
        )}
        {rows && rows.length > 0 && (
          <table className="audit">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Tool</th>
                <th scope="col">Approver</th>
                <th scope="col">Model</th>
                <th scope="col" className="num">
                  Tokens in
                </th>
                <th scope="col" className="num">
                  Tokens out
                </th>
                <th scope="col" className="num">
                  Latency ms
                </th>
                <th scope="col" className="num">
                  Cost $
                </th>
                <th scope="col">Result</th>
              </tr>
            </thead>
            <tbody>
              {[...rows].reverse().slice(0, shown).map((r) => (
                <tr key={r.hash} className={r.error ? "is-error" : undefined}>
                  <td>{clock(r.time)}</td>
                  <td>{r.tool}</td>
                  <td>{r.approver ?? ""}</td>
                  <td>{r.model ?? ""}</td>
                  <td className="num" title={r.tokens != null ? `${r.tokens} in all` : undefined}>
                    {r.tokens_in ?? ""}
                  </td>
                  <td className="num">{r.tokens_out ?? ""}</td>
                  <td className="num">{Math.round(r.latency_ms)}</td>
                  <td className="num">{r.cost.toFixed(4)}</td>
                  <td>{r.error ? "refused / failed" : "ok"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {rows && rows.length > shown && (
          <p className="more">
            Showing the newest {shown} of {rows.length}.{" "}
            <button type="button" className="btn btn-small" onClick={() => setShown(shown + PAGE)}>
              Show {Math.min(PAGE, rows.length - shown)} more
            </button>
          </p>
        )}
      </div>
    </aside>
  );
}
