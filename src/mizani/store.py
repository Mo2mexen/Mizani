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
    con: sqlite3.Connection,
    patient_label: str,
    complaint: str,
    vitals: Vitals,
    proposal: Proposal,
    retriage_of: str | None = None,
) -> None:
    """A re-triage keeps the patient's original arrival time: the wait doesn't restart."""
    arrived_at = now().isoformat()
    if retriage_of is not None:
        arrived_at = get(con, retriage_of)["arrived_at"]
    con.execute(
        "INSERT INTO triage (id, patient_label, complaint, vitals, proposal, status, arrived_at,"
        " retriage_of) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
        (
            proposal.triage_id,
            patient_label,
            complaint,
            vitals.model_dump_json(),
            proposal.model_dump_json(),
            arrived_at,
            retriage_of,
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
        if record["retriage_of"] is not None:  # another re-triage may have replaced it first
            previous = get(con, record["retriage_of"])
            if previous["superseded_by"] is not None:
                raise ApprovalError("this patient was already re-triaged; propose again")
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
        if record["retriage_of"] is not None:  # the approved re-triage replaces the old entry
            con.execute(
                "UPDATE triage SET superseded_by = ? WHERE id = ?",
                (triage_id, record["retriage_of"]),
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


def _minutes_since(iso: str) -> float:
    return (now() - datetime.fromisoformat(iso)).total_seconds() / 60


def queue(con: sqlite3.Connection, pack: RulePack, triage_id: str | None = None) -> list[dict]:
    """Committed patients still waiting, most urgent colour first, then by arrival. Read only:
    the order comes from the nurses' committed colours, never from the agent.

    A re-check is due once the time since the patient's last triage passes the target time to
    be seen for their colour (manual p6, Table 3)."""
    targets = {c.colour: c.target_minutes for c in pack.colours}
    sql = (
        "SELECT id, patient_label, colour, arrived_at, committed_at, recheck_raised_at"
        " FROM triage WHERE status = 'committed' AND superseded_by IS NULL"
    )
    args: tuple = ()
    if triage_id is not None:
        sql, args = sql + " AND id = ?", (triage_id,)
    rows = con.execute(sql, args).fetchall()
    out = []
    for row in rows:
        since_triage = _minutes_since(row["committed_at"])
        out.append(
            {
                "triage_id": row["id"],
                "patient_label": row["patient_label"],
                "colour": row["colour"],
                "arrived_at": row["arrived_at"],
                "waited_minutes": round(_minutes_since(row["arrived_at"]), 1),
                "minutes_since_triage": round(since_triage, 1),
                "target_minutes": targets[row["colour"]],
                "recheck_due": since_triage > targets[row["colour"]],
                "recheck_raised": row["recheck_raised_at"] is not None
                and row["recheck_raised_at"] >= row["committed_at"],
            }
        )
    return sorted(out, key=lambda r: (pack.rank(r["colour"]), r["arrived_at"]))


def raise_recheck(con: sqlite3.Connection, triage_id: str, pack: RulePack) -> dict:
    """Record a re-check flag, but only for a waiting patient whose timer has really run out."""
    entry = next(iter(queue(con, pack, triage_id)), None)
    if entry is None:
        raise ValueError("not a committed patient in the waiting queue")
    if entry["recheck_raised"]:
        raise ValueError("a re-check is already raised for this triage")
    if not entry["recheck_due"]:
        raise ValueError(
            f"not due: triaged {entry['minutes_since_triage']:.0f} min ago, target for "
            f"{entry['colour']} is {entry['target_minutes']} min"
        )
    con.execute(
        "UPDATE triage SET recheck_raised_at = ? WHERE id = ?", (now().isoformat(), triage_id)
    )
    return entry


HANDOVER_MAX_LINES, HANDOVER_MAX_CHARS = 3, 600


def draft_handover(con: sqlite3.Connection, triage_id: str, text: str) -> dict:
    """Store a handover draft for a committed triage. It stays unreleased until a nurse
    releases it with an approval token."""
    record = get(con, triage_id)
    if record is None or record["status"] != "committed" or record["superseded_by"]:
        raise ValueError("handover drafts are only for committed triages still in the queue")
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines or len(lines) > HANDOVER_MAX_LINES or len(text) > HANDOVER_MAX_CHARS:
        raise ValueError(
            f"a handover is 1 to {HANDOVER_MAX_LINES} lines and at most "
            f"{HANDOVER_MAX_CHARS} characters"
        )
    cur = con.execute(
        "INSERT INTO handover (triage_id, text, drafted_at) VALUES (?, ?, ?)",
        (triage_id, "\n".join(lines), now().isoformat()),
    )
    return {"triage_id": triage_id, "draft_id": cur.lastrowid, "status": "awaiting_release"}


def release_handover(
    con: sqlite3.Connection, triage_id: str, draft_id: int, approval: dict
) -> dict:
    """Release the exact draft the nurse approved (the token names it). Each token works once."""
    if approval.get("draft_id") != draft_id:
        raise ApprovalError("the approval is for a different handover draft")
    con.execute("BEGIN IMMEDIATE")
    try:
        draft = con.execute(
            "SELECT h.id, h.text FROM handover h JOIN triage t ON t.id = h.triage_id"
            " WHERE h.id = ? AND h.triage_id = ? AND h.released_at IS NULL"
            " AND t.superseded_by IS NULL",
            (draft_id, triage_id),
        ).fetchone()
        if draft is None:
            raise ApprovalError("no such unreleased draft for this triage still in the queue")
        try:
            con.execute("INSERT INTO used_token (nonce) VALUES (?)", (approval["nonce"],))
        except sqlite3.IntegrityError:
            raise ApprovalError("this approval token was already used") from None
        released_at = now().isoformat()
        con.execute(
            "UPDATE handover SET released_by = ?, released_at = ? WHERE id = ?",
            (approval["nurse_name"], released_at, draft["id"]),
        )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return {
        "triage_id": triage_id,
        "text": draft["text"],
        "approver": approval["nurse_name"],
        "released_at": released_at,
    }


def handovers(con: sqlite3.Connection, triage_id: str) -> list[dict]:
    rows = con.execute("SELECT * FROM handover WHERE triage_id = ? ORDER BY id", (triage_id,))
    return [dict(row) for row in rows]


def record_assessment(
    con: sqlite3.Connection, triage_id: str, nurse_name: str, colour: str, reason: str | None
) -> dict:
    """Shadow mode: the nurse's own colour, recorded once per triage."""
    if get(con, triage_id) is None:
        raise ValueError(f"no triage with id {triage_id}")
    try:
        con.execute(
            "INSERT INTO nurse_assessment (triage_id, nurse_name, colour, reason, recorded_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (triage_id, nurse_name, colour, reason, now().isoformat()),
        )
    except sqlite3.IntegrityError:
        raise ValueError("this triage already has a nurse assessment") from None
    return {"triage_id": triage_id, "approver": nurse_name, "colour": colour}


def shadow_agreement(con: sqlite3.Connection) -> dict:
    """How often the nurse's own colour matched Mizani's proposal, with every disagreement."""
    rows = con.execute(
        "SELECT a.triage_id, a.nurse_name, a.colour AS nurse_colour, a.reason, t.proposal"
        " FROM nurse_assessment a JOIN triage t ON t.id = a.triage_id ORDER BY a.recorded_at"
    ).fetchall()
    pairs = []
    for row in rows:
        proposal = json.loads(row["proposal"])
        pairs.append(
            {
                "triage_id": row["triage_id"],
                "nurse_colour": row["nurse_colour"],
                "mizani_colour": proposal["colour"],
                "mizani_source": proposal["source"],
                "nurse_reason": row["reason"],
            }
        )
    agreed = sum(p["nurse_colour"] == p["mizani_colour"] for p in pairs)
    return {
        "assessed": len(pairs),
        "agreed": agreed,
        "agreement_rate": round(agreed / len(pairs), 3) if pairs else None,
        "disagreements": [p for p in pairs if p["nurse_colour"] != p["mizani_colour"]],
    }
