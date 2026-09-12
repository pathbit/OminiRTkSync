# Configuration

Everything is reachable from the environment. You never have to open the dashboard to configure
the service — that is a hard contract, covered by `tests/test_config_env.py`.

Values are read from the process environment first, then from a `.env` file in the working
directory (`load_dotenv` never overwrites a variable that is already set).

---

## Database and host discovery

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DB_PATH` | auto-detected | Path to the OmniRoute SQLite file. When unset, the first existing candidate wins: `/app/data/storage.sqlite`, `/app/data/data.sqlite`, `/app/data/storage.sqlite`, `~/.omniroute/data/storage.sqlite`, `~/.omniroute/data.sqlite`. |
| `HOST_HOME` | auto-detected | Host home directory mounted into the container. Falls back to `/root/host`, then `/host`, then the process home. |
| `DATA_DIR` | — | Base directory for the panel's own state files (`.dashboard_auth.json`, `.dashboard_recovery`, `ui_prefs.sqlite`). Defaults to the directory holding `DB_PATH`. |
| `ANTIGRAVITY_TOKEN_PATH` | — | Extra path to an Antigravity/Gemini credential file, searched before the built-in list. |
| `MODULE` | `all` | Which combos to sync: `all`, `antigravity`, `oauth`, `gemini`. |

---

## Gateway connectivity

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OMNIROUTE_URL` | `http://127.0.0.1:20128` | Base URL of the OmniRoute gateway, used by `/healthz` and by the **Test connection** button. |

The gateway probe result is cached for 30 seconds. Without that cache, every Docker health check
would pay an outbound HTTP call of up to 3 seconds — which is what used to make the probe time
out and produce `BrokenPipeError` in the logs.

---

## Synchronization and scheduling

| Variable | Default | Description |
| :--- | :--- | :--- |
| `SYNC_INTERVAL` | `300` | Seconds between synchronization passes. |
| `REFRESH_MARGIN` | `900` | Seconds of remaining validity below which a token is renewed. |
| `CRON_INTERVAL` | inherits `SYNC_INTERVAL` | Dedicated interval for the scheduler, when you want it to differ from the sync pass. |
| `CRON_ENABLED` | `1` | `0` disables the automatic scheduler entirely. Synchronization then only happens on a manual trigger (`--once`, the **Run now** button, or `POST /api/sync`). |
| `CREDENTIAL_CHECK_ENABLED` | `1` | Asks each provider whether the stored credential is still accepted. `0` turns the live check off and the panel falls back to reporting `Not checked`. |
| `CREDENTIAL_CHECK_TIMEOUT` | `8` | Seconds allowed per credential probe. |

> **A token is only renewed inside the margin.** With the defaults, a token with 24 minutes left
> is *not* renewed, because 24 min > 15 min. That is correct behavior, not a failure — the
> dashboard states the reason per connection. See [Troubleshooting](Troubleshooting).

---

## Web dashboard

| Variable | Default | Description |
| :--- | :--- | :--- |
| `ENABLE_WEB_DASHBOARD` | `1` | `0` runs the synchronizer headless, with no HTTP server at all. |
| `WEB_HOST` | `0.0.0.0` | Listen interface **inside** the container. Keep the published port bound to `127.0.0.1` on the host. |
| `WEB_PORT` | `9090` | Internal port. Identical in both synchronizers; the published host port is what differs (`9092` here, `9091` for 9RTKSync). |

---

## Authentication

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DASHBOARD_USER` | `admin` | Panel user. |
| `DASHBOARD_PASSWORD` | *(empty)* | Panel password. Left empty, the first sign-in uses the recovery credential generated on first boot. |
| `DASHBOARD_RECOVERY_HASH` | generated | Break-glass credential: sign in as `admin` with this value as the password. When unset, a random value is generated on first boot, stored with mode `0600` and written once to the log. |

**Headless mode.** Setting `DASHBOARD_USER` and/or `DASHBOARD_PASSWORD` makes the environment the
source of truth: the `.dashboard_auth.json` file written by the screen is ignored, and changing
the password from the panel answers `409 Conflict`. Comment both variables out to hand control
back to the dashboard.

Full rules in [Authentication](Authentication).

---

## Logging

| Variable | Default | Description |
| :--- | :--- | :--- |
| `LOG_DIR` | `<DB_PATH dir>/logs` | Directory for log files. Falls back to `~/.ominirtksync/logs`. |
| `LOG_RETENTION_DAYS` | `30` | Days before rotated files are purged. Minimum `1`. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `LOG_TO_STDOUT` | `1` | `0` stops mirroring events on the container stdout. |

Details in [Logging](Logging).

---

## CLI overrides

Command-line flags take precedence over the environment for a single run:

```bash
OminiRTKSync --status  --db-path /path/to/data.sqlite
OminiRTKSync --once    --db-path /path/to/data.sqlite
OminiRTKSync --daemon  --db-path /path/to/data.sqlite --interval 60 --margin 1200 --port 9090
OminiRTKSync --daemon  --no-web
```

---

## Fully headless example

No dashboard, no interactive setup, scheduler on a one-minute cadence, logs kept for 90 days:

```yaml
environment:
  - DB_PATH=/app/data/storage.sqlite
  - OMNIROUTE_URL=http://omniroute:20128
  - SYNC_INTERVAL=60
  - REFRESH_MARGIN=1200
  - ENABLE_WEB_DASHBOARD=0
  - LOG_DIR=/app/data/logs
  - LOG_RETENTION_DAYS=90
  - LOG_TO_STDOUT=0
```
