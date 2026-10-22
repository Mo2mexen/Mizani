import { useEffect, useState } from "react";
import { api } from "../api";
import { clock } from "../format";
import type { AuditRow, Shadow } from "../types";

type Props = { isOpen: boolean; onClose: () => void };

/** Every logged tool call, newest first, and whether the hash chain is intact. */
export function AuditDrawer({ isOpen, onClose }: Props) {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [isIntact, setIsIntact] = useState<boolean | null>(null);
  const [shadow, setShadow] = useState<Shadow | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    if (isOpen) void load();
  }, [isOpen]);

  if (!isOpen) return null;
  return (
    <aside className="drawer" aria-labelledby="audit-title">
      <header className="drawer-head">
        <h2 id="audit-title" className="panel-title">
          Audit log <span className="count">{rows.length} calls</span>
        </h2>
        {isIntact !== null && (
          <span className={isIntact ? "chain ok" : "chain broken"}>
            {isIntact ? "hash chain intact" : "HASH CHAIN BROKEN"}
          </span>
        )}
        {shadow && shadow.assessed > 0 && (
          <span className="chain">
            shadow mode: nurse and Mizani agreed {shadow.agreed}/{shadow.assessed}
          </span>
        )}
        <button type="button" className="btn btn-small" onClick={() => void load()}>
          Refresh
        </button>
        <button type="button" className="btn btn-small" onClick={onClose}>
          Close
        </button>
      </header>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="drawer-body">
        <table className="audit">
          <thead>
            <tr>
              <th>Time</th>
              <th>Tool</th>
              <th>Approver</th>
              <th>Model</th>
              <th className="num">Tokens</th>
              <th className="num">Latency ms</th>
              <th className="num">Cost</th>
              <th>Result</th>
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
      </div>
    </aside>
  );
}
