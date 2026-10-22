import sqlite3

from mizani.db import ADDED_COLUMNS, _connect


def test_old_databases_gain_the_new_columns(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE triage (id TEXT PRIMARY KEY)")
    old.execute("CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT)")
    old.commit()
    old.close()

    con = _connect(str(path))
    for table, column in ADDED_COLUMNS:
        assert column in {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
