"""Back up and restore Mizani's data: one dated folder per backup, checked before it is restored.

In Docker (the `backup` service in docker-compose.yml):
    docker compose run --rm backup                   # a new backup in ./backups/<UTC time>/
    docker compose run --rm backup list              # the backups there are
    docker compose down                              # restore only with Mizani stopped
    docker compose run --rm backup restore <folder>  # then: docker compose up

A backup holds the triage database (`mizani.db`: triages, waiting room, audit log), the agent's
saved runs (`checkpoints.db`), the FHIR store's files (`hapi-data.tar.gz`), `audit-head.json`
(the audit chain's row count and latest hash) and `manifest.json` (a SHA-256 of every file).
SQLite files are copied with SQLite's own backup API, so they are consistent even while Mizani
runs; the FHIR files are only certain to be consistent with the stack down.

Copy the "audit head" line somewhere else as well (a notebook, another machine). The chain has no
key, so someone who can write the data volume could rewrite every row; a head kept elsewhere
shows that the log you restore is the log you had.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from mizani import audit

DATABASES = ("mizani.db", "checkpoints.db")
HAPI_ARCHIVE = "hapi-data.tar.gz"
# Services that answer while the stack is up, by their compose names. Any HTTP answer, even an
# error, means "running"; only "cannot connect" means stopped.
RUNNING_CHECKS = (
    "http://mizani-mcp:8000/health",
    "http://app:8090/api/health",
    "http://hapi-fhir:8080/fhir/metadata",
)


class BackupError(Exception):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _copy_sqlite(source: Path, target: Path) -> None:
    """A consistent copy of a live SQLite file, write-ahead log included."""
    src = _read_only(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        # One self-contained file: reading the backup later then creates no -wal or -shm beside
        # it. Mizani switches the restored file back to write-ahead logging when it opens it.
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        src.close()
        dst.close()


def _audit_head(database: Path) -> dict:
    con = _read_only(database)
    con.row_factory = sqlite3.Row
    try:
        broken = audit.verify_chain(con)
        row = con.execute("SELECT COUNT(*) AS n, MAX(id) AS last FROM audit").fetchone()
        last = con.execute("SELECT hash FROM audit WHERE id = ?", (row["last"],)).fetchone()
    finally:
        con.close()
    return {
        "rows": row["n"],
        "latest_hash": last["hash"] if last else audit.GENESIS,
        "chain_intact": broken is None,
        "first_broken_row": broken,
    }


def create(data: Path, hapi: Path, out: Path, now: datetime | None = None) -> Path:
    """Write a new backup folder under `out` and return it."""
    if not (data / "mizani.db").is_file():
        raise BackupError(f"no {data / 'mizani.db'}: nothing to back up")
    now = now or datetime.now(UTC)
    folder = out / now.strftime("%Y-%m-%dT%H%M%SZ")
    folder.mkdir(parents=True)
    for name in DATABASES:
        if (data / name).is_file():
            _copy_sqlite(data / name, folder / name)
    if hapi.is_dir():
        with tarfile.open(folder / HAPI_ARCHIVE, "w:gz") as tar:
            for item in sorted(hapi.iterdir()):
                if not item.name.endswith(".trace.db"):  # H2's debug log, not data
                    tar.add(item, arcname=item.name)
    head = _audit_head(folder / "mizani.db")
    (folder / "audit-head.json").write_text(json.dumps(head, indent=1), encoding="utf-8")
    files = {f.name: _sha256(f) for f in sorted(folder.iterdir())}
    manifest = {"created_at": now.isoformat(), "files": files}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return folder


def check(folder: Path) -> dict:
    """Refuse a backup that is incomplete or changed since it was made; return its audit head."""
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        head = json.loads((folder / "audit-head.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError(f"{folder.name}: not a complete backup ({exc})") from exc
    for name, digest in manifest["files"].items():
        if not (folder / name).is_file() or _sha256(folder / name) != digest:
            raise BackupError(f"{folder.name}/{name} is missing or changed since the backup")
    if _audit_head(folder / "mizani.db") != head:
        raise BackupError(f"{folder.name}: the audit log does not match its recorded head")
    return head


def stack_is_running(urls: tuple[str, ...] = RUNNING_CHECKS) -> bool:
    for url in urls:
        try:
            urllib.request.urlopen(url, timeout=2).close()
            return True
        except urllib.error.HTTPError:
            return True  # it answered, with an error: it is running
        except (urllib.error.URLError, OSError):
            continue
    return False


def restore(
    folder: Path, data: Path, hapi: Path, is_running: Callable[[], bool] = stack_is_running
) -> dict:
    """Put a checked backup back. Refuses while Mizani runs: its open files would be replaced
    under it."""
    head = check(folder)
    if is_running():
        raise BackupError("Mizani is running: run `docker compose down` first, then restore")
    data.mkdir(parents=True, exist_ok=True)
    for name in DATABASES:
        for leftover in (data / f"{name}-wal", data / f"{name}-shm"):
            leftover.unlink(missing_ok=True)  # the old file's log would be replayed onto the new
        if (folder / name).is_file():
            shutil.copyfile(folder / name, data / f"{name}.restoring")
            os.replace(data / f"{name}.restoring", data / name)
        else:
            (data / name).unlink(missing_ok=True)
    if (folder / HAPI_ARCHIVE).is_file():
        hapi.mkdir(parents=True, exist_ok=True)
        for item in hapi.iterdir():
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        with tarfile.open(folder / HAPI_ARCHIVE, "r:gz") as tar:
            tar.extractall(hapi, filter="data")
    if hasattr(os, "geteuid") and os.geteuid() == 0:  # in Docker the tool runs as root
        _give_back(hapi, hapi.rglob("*"))  # to the FHIR server's user, who owns its folder
        _give_back(data, (data / name for name in DATABASES if (data / name).is_file()))
    if _audit_head(data / "mizani.db") != head:
        raise BackupError("after the restore the audit log does not match the backup's head")
    return head


def _give_back(folder: Path, items) -> None:
    """Restored files belong to the folder's owner, so each service can open its own files."""
    owner = folder.stat()
    for item in items:
        os.chown(item, owner.st_uid, owner.st_gid)


def _dirs() -> tuple[Path, Path, Path]:
    env = os.environ.get
    return (
        Path(env("MIZANI_DATA_DIR", "/data")),
        Path(env("MIZANI_HAPI_DIR", "/hapi")),
        Path(env("MIZANI_BACKUP_DIR", "/backups")),
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    data, hapi, out = _dirs()
    command = args[0] if args else "create"
    try:
        if command == "create" and len(args) <= 1:
            folder = create(data, hapi, out)
            head = json.loads((folder / "audit-head.json").read_text(encoding="utf-8"))
            print(f"Backup written: backups/{folder.name}")
            print(f"Audit head: {head['rows']} rows, latest hash {head['latest_hash']}")
            if not head["chain_intact"]:
                print(f"WARNING: the audit chain breaks at row {head['first_broken_row']}")
            print("Keep a copy of the audit head line somewhere other than this machine.")
        elif command == "list" and len(args) == 1:
            found = sorted(p.name for p in out.iterdir() if (p / "manifest.json").is_file())
            print("\n".join(found) if found else "No backups yet.")
        elif command == "restore" and len(args) == 2:
            folder = out / Path(args[1]).name  # a folder name inside the backups folder only
            head = restore(folder, data, hapi)
            print(f"Restored {folder.name}: {head['rows']} audit rows, chain checked.")
            print("Start Mizani again with `docker compose up`.")
        else:
            print("Usage: mizani-backup [create] | list | restore <folder>", file=sys.stderr)
            return 2
    except (BackupError, OSError) as exc:
        print(f"Backup error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
