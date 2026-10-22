"""Hash-chained audit log. Each row's hash covers its content and the previous hash, so editing
or deleting any past row breaks the chain from that point on."""

import hashlib
import json
import sqlite3

from mizani.schemas import AuditEntry

GENESIS = "0" * 64


def _hash(prev_hash: str, body: str) -> str:
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def record(
    con: sqlite3.Connection,
    entry: AuditEntry,
    triage_id: str | None = None,
    run_id: str | None = None,
) -> str:
    """Append one entry and return its hash. `run_id` ties together the calls of one agent run."""
    body = json.dumps(
        {**entry.model_dump(mode="json"), "triage_id": triage_id, "run_id": run_id},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    con.execute("BEGIN IMMEDIATE")  # one writer at a time keeps the chain linear
    try:
        row = con.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else GENESIS
        digest = _hash(prev, body)
        con.execute(
            "INSERT INTO audit (time, tool, triage_id, run_id, approver, body, prev_hash, hash)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.time.isoformat(),
                entry.tool,
                triage_id,
                run_id,
                entry.approver,
                body,
                prev,
                digest,
            ),
        )
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return digest


def verify_chain(con: sqlite3.Connection) -> int | None:
    """Return the id of the first row that fails the check, or None if the chain is intact."""
    prev = GENESIS
    for row in con.execute("SELECT id, body, prev_hash, hash FROM audit ORDER BY id"):
        if row["prev_hash"] != prev or row["hash"] != _hash(prev, row["body"]):
            return row["id"]
        prev = row["hash"]
    return None


def entries(con: sqlite3.Connection, triage_id: str | None = None) -> list[dict]:
    """Logged entries, oldest first. For one triage: its own calls plus every call from the
    agent run that proposed it (the checks that ran before the triage id existed)."""
    sql, args = "SELECT body, hash FROM audit", ()
    if triage_id is not None:
        sql += (
            " WHERE triage_id = ? OR (run_id IS NOT NULL"
            " AND run_id = (SELECT run_id FROM triage WHERE id = ?))"
        )
        args = (triage_id, triage_id)
    rows = con.execute(sql + " ORDER BY id", args).fetchall()
    return [{**json.loads(r["body"]), "hash": r["hash"]} for r in rows]
