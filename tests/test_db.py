import sqlite3

from mizani.db import _connect


def test_old_databases_gain_the_run_id_column(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE triage (id TEXT PRIMARY KEY)")
    old.execute("CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT)")
    old.commit()
    old.close()

    con = _connect(str(path))
    for table in ("triage", "audit"):
        assert "run_id" in {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
