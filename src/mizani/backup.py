"""Back up and restore Mizani's data: one dated folder per backup, checked before it is restored.

In Docker (the `backup` service in docker-compose.yml):
    docker compose run --rm backup                   # a new backup in ./backups/<UTC time>/
    docker compose run --rm backup list              # the backups there are
    docker compose down                              # restore only with Mizani stopped
    docker compose run --rm backup restore <folder>  # then: docker compose up

A backup holds the triage database (`mizani.db`: triages, waiting room, audit log), the agent's
saved runs (`checkpoints.db`), the FHIR store's files (`hapi-data.tar.gz`), `audit-head.json`
(the audit chain's row count and latest hash) and `manifest.json` (a SHA-256 of every file and
the owner of each data folder). SQLite files are copied with SQLite's own backup API, so they are
consistent even while Mizani runs; the FHIR files are only certain to be consistent with the
stack down, and a backup made while it runs says so.

Copy the "audit head" line somewhere else as well (a notebook, another machine). The chain has no
key, so someone who can write the data volume could rewrite every row; a head kept elsewhere
shows that the log you restore is the log you had.

Outside Docker the folders default to `.cache/data` (as `MIZANI_DB`), `.cache/hapi` and
`backups`; compose sets them to the mounted volumes.
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
from mizani.db import db_path

DATABASES = ("mizani.db", "checkpoints.db")
HAPI_ARCHIVE = "hapi-data.tar.gz"
STAGING = ".restoring"  # restored files are unpacked here first, then swapped in
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


def _owner(folder: Path) -> list[int] | None:
    """Who owns a data folder (uid, gid), so a restore onto a new volume can give it back."""
    if not hasattr(os, "geteuid") or not folder.is_dir():
        return None
    st = folder.stat()
    return [st.st_uid, st.st_gid]


def create(
    data: Path, hapi: Path, out: Path, now: datetime | None = None, stack_running: bool = False
) -> Path:
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
    manifest = {
        "created_at": now.isoformat(),
        # The FHIR files of a backup made while HAPI ran may hold a write in progress.
        "stack_running": stack_running,
        "owners": {"data": _owner(data), "hapi": _owner(hapi)},
        "files": {f.name: _sha256(f) for f in sorted(folder.iterdir())},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return folder


def _manifest(folder: Path) -> dict:
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        json.loads((folder / "audit-head.json").read_text(encoding="utf-8"))
        files = manifest["files"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BackupError(f"{folder.name}: not a complete backup ({exc!r})") from exc
    if not isinstance(files, dict) or "mizani.db" not in files or "audit-head.json" not in files:
        raise BackupError(f"{folder.name}: the manifest does not list the database and its head")
    return manifest


def check(folder: Path) -> dict:
    """Refuse a backup that is incomplete or changed since it was made; return its audit head."""
    manifest = _manifest(folder)
    for name, digest in manifest["files"].items():
        if Path(name).name != name:
            raise BackupError(f"{folder.name}: the manifest names a file outside the backup")
        if not (folder / name).is_file() or _sha256(folder / name) != digest:
            raise BackupError(f"{folder.name}/{name} is missing or changed since the backup")
    head = json.loads((folder / "audit-head.json").read_text(encoding="utf-8"))
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


def _empty(folder: Path, keep: str = "") -> None:
    for item in folder.iterdir():
        if item.name != keep:
            shutil.rmtree(item) if item.is_dir() and not item.is_symlink() else item.unlink()


def _give_back(folder: Path, owner: list[int] | None) -> None:
    """The folder and everything in it go back to the recorded owner (or the folder's own), so
    each service can open its files even on a volume the restore itself created."""
    if not (hasattr(os, "geteuid") and os.geteuid() == 0):
        return  # only root can change owners; outside Docker the files are the user's already
    uid, gid = owner or [folder.stat().st_uid, folder.stat().st_gid]
    for item in [folder, *folder.rglob("*")]:
        os.chown(item, uid, gid, follow_symlinks=False)


def restore(
    folder: Path, data: Path, hapi: Path, is_running: Callable[[], bool] = stack_is_running
) -> dict:
    """Put a checked backup back. Refuses while Mizani runs: its open files would be replaced
    under it. Everything is unpacked beside the live files first and swapped in only when all of
    it is there, so a failure part-way leaves the live data as it was."""
    head = check(folder)
    manifest = _manifest(folder)
    if is_running():
        raise BackupError("Mizani is running: run `docker compose down` first, then restore")
    data.mkdir(parents=True, exist_ok=True)
    hapi.mkdir(parents=True, exist_ok=True)
    stage_data, stage_hapi = data / STAGING, hapi / STAGING
    try:
        for stage in (stage_data, stage_hapi):
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir()
        for name in DATABASES:
            if (folder / name).is_file():
                shutil.copyfile(folder / name, stage_data / name)
        if (folder / HAPI_ARCHIVE).is_file():
            with tarfile.open(folder / HAPI_ARCHIVE, "r:gz") as tar:
                tar.extractall(stage_hapi, filter="data")
    except (OSError, tarfile.TarError) as exc:
        shutil.rmtree(stage_data, ignore_errors=True)
        shutil.rmtree(stage_hapi, ignore_errors=True)
        raise BackupError(f"restore stopped before changing anything: {exc}") from exc
    # Swap in: from here on only renames and deletions in the same volume.
    for name in DATABASES:
        for leftover in (data / f"{name}-wal", data / f"{name}-shm"):
            leftover.unlink(missing_ok=True)  # the old file's log would be replayed onto the new
        if (stage_data / name).is_file():
            os.replace(stage_data / name, data / name)
        else:
            (data / name).unlink(missing_ok=True)
    stage_data.rmdir()
    if (folder / HAPI_ARCHIVE).is_file():
        _empty(hapi, keep=STAGING)
        for item in stage_hapi.iterdir():
            os.replace(item, hapi / item.name)
    shutil.rmtree(stage_hapi, ignore_errors=True)
    owners = manifest.get("owners") or {}
    _give_back(data, owners.get("data"))
    _give_back(hapi, owners.get("hapi"))
    if _audit_head(data / "mizani.db") != head:
        raise BackupError("after the restore the audit log does not match the backup's head")
    return head


def _dirs() -> tuple[Path, Path, Path]:
    env = os.environ.get
    return (
        Path(env("MIZANI_DATA_DIR") or db_path().parent),
        Path(env("MIZANI_HAPI_DIR") or ".cache/hapi"),
        Path(env("MIZANI_BACKUP_DIR") or "backups"),
    )


def _chain_line(head: dict) -> str:
    if head["chain_intact"]:
        return f"{head['rows']} audit rows, chain intact, latest hash {head['latest_hash']}"
    return (
        f"{head['rows']} audit rows, WARNING: the chain breaks at row {head['first_broken_row']} "
        f"(latest hash {head['latest_hash']})"
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    # Backups hold patient records: readable by the owner only, also on a shared Linux host.
    os.umask(0o077)
    data, hapi, out = _dirs()
    command = args[0] if args else "create"
    try:
        if command == "create" and len(args) <= 1:
            running = stack_is_running()
            folder = create(data, hapi, out, stack_running=running)
            head = json.loads((folder / "audit-head.json").read_text(encoding="utf-8"))
            print(f"Backup written: backups/{folder.name}")
            print(f"Audit head: {_chain_line(head)}")
            if running:
                print(
                    "Mizani was running: the databases are a consistent copy, the FHIR files may "
                    "miss a write in progress. For a certain copy, back up with the stack down."
                )
            print("Keep a copy of the audit head line somewhere other than this machine.")
        elif command == "list" and len(args) == 1:
            found = sorted(p.name for p in out.iterdir() if (p / "manifest.json").is_file())
            print("\n".join(found) if found else "No backups yet.")
        elif command == "restore" and len(args) == 2:
            folder = out / Path(args[1]).name  # a folder name inside the backups folder only
            head = restore(folder, data, hapi)
            print(f"Restored {folder.name}: {_chain_line(head)}")
            print("Start Mizani again with `docker compose up`.")
        else:
            print("Usage: mizani-backup [create] | list | restore <folder>", file=sys.stderr)
            return 2
    except (BackupError, OSError, sqlite3.Error, tarfile.TarError) as exc:
        print(f"Backup error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
