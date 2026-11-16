import json
import sqlite3
from datetime import UTC, datetime

import pytest

from mizani import audit
from mizani.backup import BackupError, check, create, main, restore
from mizani.db import close_all, db
from mizani.schemas import AuditEntry

NOW = datetime(2026, 11, 16, 13, 20, 5, tzinfo=UTC)


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """A data folder with an audit log of three rows and a saved run, and a FHIR folder."""
    data, hapi, out = tmp_path / "data", tmp_path / "hapi", tmp_path / "backups"
    monkeypatch.setenv("MIZANI_DB", str(data / "mizani.db"))
    for i in range(3):
        entry = AuditEntry(time=NOW, tool=f"tool_{i}", inputs={}, outputs={}, latency_ms=1)
        audit.record(db(), entry)
    saved = sqlite3.connect(data / "checkpoints.db")
    saved.execute("CREATE TABLE runs (id TEXT)")
    saved.execute("INSERT INTO runs VALUES ('run-1')")
    saved.commit()
    saved.close()
    hapi.mkdir()
    (hapi / "h2.mv.db").write_bytes(b"fhir records")
    (hapi / "h2.trace.db").write_bytes(b"debug log")
    yield data, hapi, out
    close_all()  # Windows can't delete open database files


def stopped():
    return False


def test_a_backup_holds_every_file_and_the_audit_head(stack):
    data, hapi, out = stack
    folder = create(data, hapi, out, NOW)
    assert folder.name == "2026-11-16T132005Z"
    names = {p.name for p in folder.iterdir()}
    assert names == {
        "mizani.db",
        "checkpoints.db",
        "hapi-data.tar.gz",
        "audit-head.json",
        "manifest.json",
    }
    head = json.loads((folder / "audit-head.json").read_text(encoding="utf-8"))
    last = db().execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()["hash"]
    assert head == {"rows": 3, "latest_hash": last, "chain_intact": True, "first_broken_row": None}
    assert check(folder) == head


def test_rows_written_after_the_backup_are_not_in_it(stack):
    data, hapi, out = stack
    folder = create(data, hapi, out, NOW)
    audit.record(db(), AuditEntry(time=NOW, tool="later", inputs={}, outputs={}, latency_ms=1))
    assert check(folder)["rows"] == 3


def test_restore_puts_back_the_data_and_checks_the_chain(stack, tmp_path):
    data, hapi, out = stack
    folder = create(data, hapi, out, NOW)
    new_data, new_hapi = tmp_path / "new-data", tmp_path / "new-hapi"
    new_data.mkdir()
    stale = b"a stale log from the old database"  # it would be replayed onto the restored file
    (new_data / "mizani.db-wal").write_bytes(stale)
    head = restore(folder, new_data, new_hapi, is_running=stopped)
    assert head["rows"] == 3
    wal = new_data / "mizani.db-wal"
    assert not wal.exists() or wal.read_bytes() != stale
    con = sqlite3.connect(new_data / "mizani.db")
    con.row_factory = sqlite3.Row
    assert audit.verify_chain(con) is None
    assert [r["tool"] for r in con.execute("SELECT tool FROM audit ORDER BY id")] == [
        "tool_0",
        "tool_1",
        "tool_2",
    ]
    con.close()
    runs = sqlite3.connect(new_data / "checkpoints.db")
    assert runs.execute("SELECT id FROM runs").fetchall() == [("run-1",)]
    runs.close()
    assert (new_hapi / "h2.mv.db").read_bytes() == b"fhir records"
    assert not (new_hapi / "h2.trace.db").exists()  # H2's debug log is not data


def test_restore_refuses_while_mizani_is_running(stack, tmp_path):
    data, hapi, out = stack
    folder = create(data, hapi, out, NOW)
    with pytest.raises(BackupError, match="docker compose down"):
        restore(folder, tmp_path / "d", tmp_path / "h", is_running=lambda: True)
    assert not (tmp_path / "d").exists()  # nothing was touched


def test_a_changed_backup_is_refused(stack, tmp_path):
    data, hapi, out = stack
    folder = create(data, hapi, out, NOW)
    with (folder / "hapi-data.tar.gz").open("ab") as f:
        f.write(b"tampered")
    with pytest.raises(BackupError, match="hapi-data.tar.gz is missing or changed"):
        restore(folder, tmp_path / "d", tmp_path / "h", is_running=stopped)


def test_a_rewritten_audit_log_is_refused_even_with_a_matching_manifest(stack, tmp_path):
    data, hapi, out = stack
    folder = create(data, hapi, out, NOW)
    head = json.loads((folder / "audit-head.json").read_text(encoding="utf-8"))
    head["latest_hash"] = "f" * 64  # someone edits the recorded head and the manifest to match
    (folder / "audit-head.json").write_text(json.dumps(head), encoding="utf-8")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    from mizani.backup import _sha256

    manifest["files"]["audit-head.json"] = _sha256(folder / "audit-head.json")
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError, match="does not match its recorded head"):
        check(folder)


def test_nothing_to_back_up_is_an_error_not_an_empty_backup(tmp_path):
    with pytest.raises(BackupError, match="nothing to back up"):
        create(tmp_path / "empty", tmp_path / "hapi", tmp_path / "out", NOW)


def test_the_command_line(stack, monkeypatch, capsys):
    data, hapi, out = stack
    monkeypatch.setenv("MIZANI_DATA_DIR", str(data))
    monkeypatch.setenv("MIZANI_HAPI_DIR", str(hapi))
    monkeypatch.setenv("MIZANI_BACKUP_DIR", str(out))
    assert main([]) == 0
    printed = capsys.readouterr().out
    assert "Backup written: backups/" in printed and "Audit head: 3 rows" in printed
    assert main(["list"]) == 0
    assert capsys.readouterr().out.strip().startswith("20")
    assert main(["restore", "no-such-backup"]) == 1  # a clear error, not a crash
    assert main(["restore", "../../etc"]) == 1  # only a folder inside the backups folder
    assert main(["delete", "x"]) == 2
