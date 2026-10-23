import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { clock } from "../format";
import type { AuditRow, Shadow } from "../types";

type Props = { isOpen: boolean; onClose: () => void };

/** Every logged tool call, newest first, and whether the hash chain is intact. Escape closes it. */
export function AuditDrawer({ isOpen, onClose }: Props) {
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [isIntact, setIsIntact] = useState<boolean | null>(null);
  const [shadow, setShadow] = useState<Shadow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  async function load() {
    try {
      const [audit, verify, agreement] = await Promise.all([api.audit(), api.verify(), api.shadow()]);
      setRows(audit);
      setIsIntact(verify.intact);
      setShadow(agreement);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Audit not loaded");
    }
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
  return (
    <aside className="drawer" role="dialog" aria-modal="false" aria-labelledby="audit-title">
      <header className="drawer-head">
        <h2 id="audit-title" className="panel-title">
          Audit log {rows && <span className="count">{rows.length} tool calls</span>}
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
        <button type="button" className="btn btn-small" onClick={() => void load()}>
          Refresh
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
        {rows?.length === 0 && <p className="empty">No tool calls yet. Every call of every run is logged here.</p>}
        {rows && rows.length > 0 && (
          <table className="audit">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Tool</th>
                <th scope="col">Approver</th>
                <th scope="col">Model</th>
                <th scope="col" className="num">
                  Tokens
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
              {[...rows].reverse().map((r) => (
                <tr key={r.hash} className={r.error ? "is-error" : undefined}>
                  <td>{clock(r.time)}</td>
                  <td>{r.tool}</td>
                  <td>{r.approver ?? ""}</td>
                  <td>{r.model ?? ""}</td>
                  <td className="num">{r.tokens ?? ""}</td>
                  <td className="num">{Math.round(r.latency_ms)}</td>
                  <td className="num">{r.cost.toFixed(4)}</td>
                  <td>{r.error ? "refused / failed" : "ok"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </aside>
  );
}
