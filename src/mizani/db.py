"""One SQLite file for triages, used approval tokens and the audit log. Local only."""

import os
import sqlite3
from functools import cache
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS triage (
    id TEXT PRIMARY KEY,
    patient_label TEXT NOT NULL,
    complaint TEXT NOT NULL,          -- patient text: stored as data, never followed
    vitals TEXT NOT NULL,             -- JSON
    proposal TEXT NOT NULL,           -- JSON, the engine's pending proposal
    status TEXT NOT NULL CHECK (status IN ('pending', 'committed')),
    arrived_at TEXT NOT NULL,
    colour TEXT,                      -- set on commit: proposed colour or the nurse's override
    approver TEXT,
    decision TEXT,                    -- JSON: action, colour, reason
    committed_at TEXT,
    run_id TEXT                       -- the agent run that proposed it (_meta.run_id)
);
CREATE TABLE IF NOT EXISTS used_token (nonce TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    tool TEXT NOT NULL,
    triage_id TEXT,
    run_id TEXT,                      -- the agent run, so calls before the triage exists link up
    approver TEXT,
    body TEXT NOT NULL,               -- JSON AuditEntry, the hashed content
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
"""


def db_path() -> Path:
    return Path(os.environ.get("MIZANI_DB", "data/mizani.db"))


@cache
def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    for table in ("triage", "audit"):  # databases created before run_id existed
        columns = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
        if "run_id" not in columns:
            con.execute(f"ALTER TABLE {table} ADD COLUMN run_id TEXT")
    return con


def db() -> sqlite3.Connection:
    """The connection for the configured file (`MIZANI_DB`, default `data/mizani.db`)."""
    return _connect(str(db_path()))
