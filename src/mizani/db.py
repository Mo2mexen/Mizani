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
    run_id TEXT,                      -- the agent run that proposed it (_meta.run_id)
    retriage_of TEXT,                 -- re-score of this earlier triage (W1)
    superseded_by TEXT,               -- set when a committed re-triage replaces this one
    recheck_raised_at TEXT            -- last raise_recheck_flag for this triage
);
CREATE TABLE IF NOT EXISTS used_token (nonce TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS handover (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    triage_id TEXT NOT NULL,
    text TEXT NOT NULL,               -- drafted by the agent; findings only
    drafted_at TEXT NOT NULL,
    released_by TEXT,                 -- the nurse who released it (approval token)
    released_at TEXT
);
CREATE TABLE IF NOT EXISTS nurse_assessment (
    triage_id TEXT PRIMARY KEY,       -- shadow mode: one blind assessment per triage
    nurse_name TEXT NOT NULL,
    colour TEXT NOT NULL,
    reason TEXT,
    recorded_at TEXT NOT NULL
);
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
ADDED_COLUMNS = [
    ("triage", "run_id"),
    ("audit", "run_id"),
    ("triage", "retriage_of"),
    ("triage", "superseded_by"),
    ("triage", "recheck_raised_at"),
]


def db_path() -> Path:
    return Path(os.environ.get("MIZANI_DB", "data/mizani.db"))


@cache
def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    for table, column in ADDED_COLUMNS:  # databases created before these columns existed
        columns = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    return con


def db() -> sqlite3.Connection:
    """The connection for the configured file (`MIZANI_DB`, default `data/mizani.db`)."""
    return _connect(str(db_path()))
