"""Triage records: created pending by `propose_priority`, committed only with a valid approval."""

import json
import sqlite3
from datetime import UTC, datetime

from mizani.approval import ApprovalError
from mizani.rules.pack import RulePack
from mizani.schemas import Proposal, Vitals


def now() -> datetime:
    return datetime.now(UTC)


def create(
    con: sqlite3.Connection, patient_label: str, complaint: str, vitals: Vitals, proposal: Proposal
) -> None:
    con.execute(
        "INSERT INTO triage (id, patient_label, complaint, vitals, proposal, status, arrived_at)"
        " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (
            proposal.triage_id,
            patient_label,
            complaint,
            vitals.model_dump_json(),
            proposal.model_dump_json(),
            now().isoformat(),
        ),
    )


def link_run(con: sqlite3.Connection, triage_id: str, run_id: str) -> None:
    """Remember which agent run proposed this triage, so its timeline shows the whole run."""
    con.execute("UPDATE triage SET run_id = ? WHERE id = ?", (run_id, triage_id))


def get(con: sqlite3.Connection, triage_id: str) -> dict | None:
    row = con.execute("SELECT * FROM triage WHERE id = ?", (triage_id,)).fetchone()
    if row is None:
        return None
    record = dict(row)
    for key in ("vitals", "proposal", "decision"):
        if record[key] is not None:
            record[key] = json.loads(record[key])
    return record


def commit(con: sqlite3.Connection, triage_id: str, approval: dict) -> dict:
    """Commit a pending triage with a verified approval payload. Each token works once."""
    con.execute("BEGIN IMMEDIATE")
    try:
        record = get(con, triage_id)
        if record is None:
            raise ApprovalError(f"no triage with id {triage_id}")
        if record["status"] != "pending":
            raise ApprovalError("this triage is already committed")
        try:
            con.execute("INSERT INTO used_token (nonce) VALUES (?)", (approval["nonce"],))
        except sqlite3.IntegrityError:
            raise ApprovalError("this approval token was already used") from None
        override = approval["action"] == "override"
        colour = approval["colour"] if override else record["proposal"]["colour"]
        decision = {k: approval[k] for k in ("action", "colour", "reason")}
        committed_at = now().isoformat()
        con.execute(
            "UPDATE triage SET status = 'committed', colour = ?, approver = ?, decision = ?,"
            " committed_at = ? WHERE id = ?",
            (colour, approval["nurse_name"], json.dumps(decision), committed_at, triage_id),
        )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return {
        "triage_id": triage_id,
        "colour": colour,
        "approver": approval["nurse_name"],
        "action": approval["action"],
        "committed_at": committed_at,
    }


def queue(con: sqlite3.Connection, pack: RulePack) -> list[dict]:
    """Committed patients, most urgent colour first, then by arrival. Read only: the order comes
    from the nurses' committed colours, never from the agent."""
    targets = {c.colour: c.target_minutes for c in pack.colours}
    rows = con.execute(
        "SELECT id, patient_label, colour, arrived_at FROM triage WHERE status = 'committed'"
    ).fetchall()
    out = []
    for row in rows:
        waited = (now() - datetime.fromisoformat(row["arrived_at"])).total_seconds() / 60
        out.append(
            {
                "triage_id": row["id"],
                "patient_label": row["patient_label"],
                "colour": row["colour"],
                "arrived_at": row["arrived_at"],
                "waited_minutes": round(waited, 1),
                "target_minutes": targets[row["colour"]],
                "over_target": waited > targets[row["colour"]],
            }
        )
    return sorted(out, key=lambda r: (pack.rank(r["colour"]), r["arrived_at"]))
