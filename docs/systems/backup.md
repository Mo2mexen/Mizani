# Backup and restore

Copies everything Mizani keeps (the triage database with its audit log, the agent's saved runs and the FHIR store) into one dated folder, and puts a backup back after checking it has not changed. A restart or a power cut keeps the data already (Docker volumes); this covers a broken disk, a deleted volume or a bad change.

## Where it lives
- Code: `src/mizani/backup.py` (command `mizani-backup`)
- Tests: `tests/test_backup.py`
- Config: the `backup` service in `docker-compose.yml` (profile `tools`, so `docker compose up` never starts it); `MIZANI_DATA_DIR`, `MIZANI_HAPI_DIR`, `MIZANI_BACKUP_DIR` (in Docker `/data`, `/hapi`, `/backups`; outside Docker the defaults are the folder of `MIZANI_DB`, `.cache/hapi` and `backups`); backups land in `./backups/` beside `docker-compose.yml`, which git and the Docker build both ignore. The service uses the image `mizani:local` that `docker compose up --build` builds for `app` and `mizani-mcp`, so it is never older than the running code

## How it works
1. `docker compose run --rm backup` makes `backups/<UTC time>/`, for example `backups/2026-11-16T132005Z/`.
2. `mizani.db` (triages, the waiting room, the accounts and the audit log) and `checkpoints.db` are copied with SQLite's own backup API, which gives a consistent copy even while the API and the MCP server are writing. Each copy is stored as one self-contained file. The approval secret (`approval.secret`) is not copied: a restore onto new volumes makes a new one at the next start, which only matters for approval tokens from the last two minutes.
3. The FHIR store's files (HAPI's H2 database) go into `hapi-data.tar.gz`, leaving out H2's debug log.
4. `audit-head.json` records the audit log's row count, its latest hash and whether the chain is intact. `manifest.json` records a SHA-256 for every file, the owner of each data folder, and whether Mizani was running (`stack_running`): if it was, the command says the FHIR files may miss a write in progress.
5. The command prints the audit head. Keep that line somewhere other than this machine.
6. `docker compose run --rm backup restore <folder>` first checks the backup: every file against the manifest, and the audit log against its recorded head. It then refuses if any Mizani service answers on the network (the stack must be down). Only then does it unpack everything into a `.restoring` folder beside the live files; if that fails (a full disk, a bad archive), it stops before changing anything. Then it swaps the files in with renames, removes the old database's write-ahead log so it cannot be replayed onto the restored file, gives every file and folder back to the owner recorded in the backup (so HAPI and Mizani can open their files even on a volume the restore itself created), and checks the restored audit log against the head once more. It prints whether the chain is intact, with a warning if it is not.

## Interfaces
| Name | Input | Output | Side effects |
|---|---|---|---|
| `mizani-backup` (or `create`) | | `backups/<UTC time>/` with `mizani.db`, `checkpoints.db`, `hapi-data.tar.gz`, `audit-head.json`, `manifest.json`; prints the audit head | none on the live data (read only) |
| `mizani-backup list` | | the backup folders | none |
| `mizani-backup restore <folder>` | a folder name inside the backups folder (a path is reduced to its last part) | exit 0 and the restored audit head (with a warning when the chain was already broken in the backup); exit 1 with the reason when the backup is incomplete, changed, or Mizani is running, or when unpacking fails (nothing is changed then) | replaces the data and FHIR volumes' files |
| `create(data, hapi, out, now)` | folders, time | the backup folder | writes it |
| `check(folder)` | backup folder | its audit head | none; raises `BackupError` |
| `restore(folder, data, hapi, is_running)` | | the audit head | replaces files; raises `BackupError` |

## Depends on / used by
- Depends on: `audit.verify_chain`, the Docker volumes `mizani-data` and `hapi-data`.
- Used by: the person who runs the desk (by hand, or from a nightly scheduled task on the host).

## Safety rules this system enforces
- The audit log is not trusted on the way back in: a restore that does not match the backup's recorded head, or whose chain is broken in a different place, is refused.
- A backup changed after it was made (any file) is refused.
- No restore under running services: their open files would be replaced under them.
- Backups hold patient data, so they stay on the machine (`./backups/` is in `.gitignore` and the Docker build ignores it) and are written readable by their owner only (umask 077). Copy them only to storage the hospital controls.

## Running and testing it alone
```
uv run pytest tests/test_backup.py
docker compose run --rm backup
docker compose run --rm backup list
docker compose down && docker compose run --rm backup restore <folder> && docker compose up
```

## Known limitations
- The FHIR files are copied as they are on disk. With the stack up, a FHIR write in progress can be missed or half-copied; for a backup that is certain to be consistent, run it with the stack down (for example at a shift change). Mizani's own databases are consistent either way.
- The tool runs as root inside its container, because HAPI's volume is readable only by HAPI's user. On Linux the files in `./backups/` therefore belong to root.
- Nothing schedules it. On the host, a nightly task (Task Scheduler on Windows, cron or a systemd timer on Linux, launchd on macOS) can run `docker compose run --rm backup` from the project folder.
- No backup is deleted automatically, and none is copied off the machine; both are the hospital's policy to set.
- The audit head proves the log you restore is the log you backed up, not that nobody rewrote it before the backup: keep each printed head somewhere else, so a later rewrite shows as a mismatch.

## Extending it
A new file Mizani keeps in `/data`: add its name to `DATABASES` if it is SQLite (it is then copied with the backup API), and add a test. A new volume: mount it in the `backup` service and archive it like `hapi-data`.
