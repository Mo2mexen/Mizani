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


def test_parallel_writers_keep_the_audit_chain_intact(tmp_path, monkeypatch):
    import threading
    from datetime import UTC, datetime

    from mizani import audit
    from mizani.db import db
    from mizani.schemas import AuditEntry

    monkeypatch.setenv("MIZANI_DB", str(tmp_path / "parallel.db"))

    def write(n):
        for i in range(25):
            entry = AuditEntry(
                time=datetime.now(UTC), tool=f"t{n}", inputs={"i": i}, outputs={}, latency_ms=0
            )
            audit.record(db(), entry)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    con = db()
    assert con.execute("SELECT count(*) FROM audit").fetchone()[0] == 200
    assert audit.verify_chain(con) is None
