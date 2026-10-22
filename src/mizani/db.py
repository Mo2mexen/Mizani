"""One SQLite file for triages, used approval tokens and the audit log. Local only."""

import os
import sqlite3
import threading
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


_schema_lock = threading.Lock()
_registry_lock = threading.Lock()
_connections: dict[tuple[int, str], sqlite3.Connection] = {}  # (thread id, path) -> connection


def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # timeout: wait for another connection's write lock instead of failing at once
    # check_same_thread=False only so close_all() can close it; each thread still uses its own
    con = sqlite3.connect(path, isolation_level=None, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    with _schema_lock:  # one thread at a time creates or migrates the schema
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        for table, column in ADDED_COLUMNS:  # databases created before these columns existed
            columns = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    return con


def db() -> sqlite3.Connection:
    """This thread's connection to the configured file (`MIZANI_DB`, default `data/mizani.db`).
    The server runs tools in worker threads; one connection per thread keeps each
    transaction to itself, and SQLite's locking orders the writers."""
    key = (threading.get_ident(), str(db_path()))
    con = _connections.get(key)
    if con is None:
        con = _connect(key[1])
        with _registry_lock:
            _connections[key] = con
    return con


def close_all() -> None:
    """Close every open connection, from any thread. Windows can't delete an open database
    file, so the eval runner calls this before removing its temporary databases."""
    with _registry_lock:
        open_now = list(_connections.values())
        _connections.clear()
    for con in open_now:
        con.close()
