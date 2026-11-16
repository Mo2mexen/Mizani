"""Settings an admin changes in the page, kept in the triage database. Today one: where the model
runs (Admin › Model). A saved choice wins over `.env`; with none saved, `.env` decides, as before.

The model server's API key is stored here, beside the patient records it protects, and is never
returned to the page or written to the audit log.
"""

import json
import os
import sqlite3
from datetime import UTC, datetime

from mizani import audit
from mizani.agent.model import DEFAULT_BASE_URL, DEFAULT_MODEL

MODEL = "model"
LOCATIONS = {
    "this_server": "this server",
    "hospital": "a GPU server in this hospital",
    "cloud": "a cloud GPU server",
}


def get(con: sqlite3.Connection, key: str) -> dict | None:
    row = con.execute(
        "SELECT value, updated_at, updated_by FROM setting WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    return {
        **json.loads(row["value"]),
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


def _put(con: sqlite3.Connection, key: str, value: dict, actor: str) -> None:
    con.execute(
        "INSERT INTO setting (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, json.dumps(value), datetime.now(UTC).isoformat(), actor),
    )


def this_server() -> dict:
    """The model `.env` sets up: by default the 4B model that came with Mizani, in compose."""
    return {
        "base_url": os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
        "model": os.environ.get("LLM_MODEL", DEFAULT_MODEL),
    }


def model(con: sqlite3.Connection) -> dict | None:
    """The admin's model choice, as `chat_model` takes it, or None for `.env`."""
    return get(con, MODEL)


def save_model(con: sqlite3.Connection, actor: str, choice: dict) -> None:
    """Save where the model runs, or go back to this server (`location` "this_server"). The
    audit row names the server and the model, and whether a key is set, never the key."""
    location = choice["location"]
    if location == "this_server":
        con.execute("DELETE FROM setting WHERE key = ?", (MODEL,))
        kept = {"location": location, **this_server()}
    else:
        kept = {k: choice.get(k) for k in ("location", "base_url", "model", "reasoning_effort")}
        _put(con, MODEL, {**kept, "api_key": choice.get("api_key") or ""}, actor)
    audit.event(
        con,
        "model_changed",
        actor,
        {**kept, "has_key": bool(choice.get("api_key")), "where": LOCATIONS[location]},
        {"complaint_text_leaves_the_hospital": location == "cloud"},
    )


def public_model(con: sqlite3.Connection) -> dict:
    """What Admin › Model shows: the choice without its key (only whether there is one)."""
    saved = model(con)
    if saved is None:
        return {
            "location": "this_server",
            **this_server(),
            "reasoning_effort": os.environ.get("LLM_REASONING_EFFORT", "none"),
            "has_key": bool(os.environ.get("LLM_API_KEY")),
            "updated_at": None,
            "updated_by": None,
            "this_server": this_server(),
        }
    shown = {k: v for k, v in saved.items() if k != "api_key"}
    return {**shown, "has_key": bool(saved.get("api_key")), "this_server": this_server()}
