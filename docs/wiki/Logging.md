# Logging

Container stdout is volatile: it disappears on `docker rm`, gets truncated by the log driver and
does not survive a restart. Events that matter for auditing — token renewals, sync failures,
dashboard access — are therefore also written to a file, with daily rotation and age-based purge.

Implemented in [`src/omini_rtksync/logs.py`](https://github.com/pathbit/OminiRTkSync/blob/master/src/omini_rtksync/logs.py),
covered by `tests/test_logs.py`.

---

## Configuration

| Variable | Default | Description |
| :--- | :--- | :--- |
| `LOG_DIR` | `<DB_PATH dir>/logs` | Destination directory. Falls back to `~/.ominirtksync/logs` when the database directory is not writable. |
| `LOG_RETENTION_DAYS` | `30` | Days a rotated file is kept. Minimum `1`; an unparseable value falls back to 30. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `LOG_TO_STDOUT` | `1` | `0` stops mirroring on stdout. The file keeps receiving everything. |

---

## Rotation and retention

- One file, `ominirtksync.log`, rotated at **UTC midnight**.
- Rotated files are named `ominirtksync.log.YYYY-MM-DD`.
- `backupCount` equals `LOG_RETENTION_DAYS`, so daily rotation keeps exactly that many days.
- On every startup, `purge_expired_logs()` also deletes rotated files whose modification time is
  older than the retention window. This catches files left behind by a container that was down
  for a while.

**Never touched:** the active `ominirtksync.log`, and any file that does not belong to this service.
Another service's logs sharing the same directory are left alone.

```
/app/data/logs/
  ominirtksync.log                ← active, never purged
  ominirtksync.log.2026-09-11     ← kept (2 days old)
  ominirtksync.log.2026-07-01     ← purged (73 days old, retention 30)
  other-service.log.2026-01-01 ← left alone, not ours
```

---

## Keeping logs longer

```yaml
environment:
  - LOG_RETENTION_DAYS=90
volumes:
  - ominirtksync_logs:/app/data/logs
```

Mount a named volume (or a host path) or the files die with the container, which defeats the
purpose.

---

## Format

```
[2026-09-12 13:46:53] [INFO] [CRON] Cycle triggered (scheduled_interval). Inspecting OAuth account connections...
[2026-09-12 13:46:53] [INFO] [STATUS] [antigravity · Google Antigravity Pro] Token valid for another 24 min
[2026-09-12 13:46:53] [INFO] [CRON] Cycle completed in 5ms: 7 accounts evaluated, 0 renewed via OAuth.
[2026-09-12 13:51:58] [WARNING] [AUTH] Recovery hash generated. To recover access use user 'admin' ...
```

Prefixes: `CRON`, `STATUS`, `SYNC`, `CURA` (self-healing), `DISCOVERY`, `AUTH`, `ERRO`, `FALHA`.

---

## Failure behaviour

The file log is **best effort**. If the directory cannot be created or written, the service still
starts and prints once to stderr:

```
[LOG] File log unavailable at /app/data/logs: [Errno 13] Permission denied
```

A synchronizer that refuses to run because it cannot write a log file would be worse than one
that runs without the log.

---

## Per-cycle logs in the dashboard

Separately from the file log, the scheduler keeps the last cycles in memory with the actions each
one produced. The **Logs** button on the scheduler card opens the history; a failed cycle is
flagged in red and its error is shown inline. See [Dashboard](Dashboard).
