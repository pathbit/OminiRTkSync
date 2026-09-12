# Installation

Two supported paths: Docker (recommended) and a local Python virtual environment.

---

## Docker Compose

The official image is published to GHCR by GitHub Actions:

```bash
docker pull ghcr.io/pathbit/ominirtksync:latest
```

A working `docker-compose.yml` alongside the gateway:

```yaml
name: omniroute-stack

services:
  omniroute:
    image: diegosouzapw/OmniRoute:latest
    container_name: omniroute
    restart: unless-stopped
    ports:
      - "127.0.0.1:20128:20128"
    environment:
      - DATA_DIR=/app/data
      - PORT=20128
      - HOSTNAME=0.0.0.0
    volumes:
      - omniroute_data:/app/data

  ominirtksync:
    image: ghcr.io/pathbit/ominirtksync:latest
    container_name: ominirtksync
    restart: unless-stopped
    ports:
      # Internal port 9090 (same in OminiRTKSync); published on 9092.
      # The 127.0.0.1 bind keeps the panel and the SQLite file off the internet.
      - "127.0.0.1:9092:9090"
    volumes:
      - omniroute_data:/app/data
      - ${HOME}:/root/host:ro
      - ominirtksync_logs:/app/data/logs
    environment:
      - HOST_HOME=/root/host
      - DB_PATH=/app/data/storage.sqlite
      - OMNIROUTE_URL=http://omniroute:20128
      - SYNC_INTERVAL=300
      - REFRESH_MARGIN=900
      - WEB_PORT=9090
      - DASHBOARD_USER=admin
      - DASHBOARD_PASSWORD=change-me
      - LOG_DIR=/app/data/logs
      - LOG_RETENTION_DAYS=30
    depends_on:
      - omniroute
    healthcheck:
      test: ["CMD", "/opt/venv/bin/python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/healthz', timeout=3)"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  omniroute_data:
  ominirtksync_logs:
```

Then open **http://localhost:9092**.

### Why these details matter

- **`omniroute_data` is shared.** The synchronizer reads and writes the same SQLite file the
  gateway uses; without the shared volume it has nothing to heal.
- **`${HOME}` is mounted read-only.** Antigravity and Gemini CLI credentials live in the host
  home (`~/.gemini/`, `~/.config/antigravity/`). Read-only is enough — the synchronizer never
  writes there.
- **The port is bound to `127.0.0.1`.** The panel reads credential metadata; it must not be
  reachable from the internet.
- **A named volume for the logs.** Otherwise they die with the container. See [Logging](Logging).

---

## Local virtual environment

Requires Python 3.11+ (3.14 is what CI pins).

```bash
git clone https://github.com/pathbit/OminiRTkSync.git
cd OminiRTKSync

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### Commands

```bash
# Connection and combo status, no changes written
OminiRTKSync --status --db-path ~/.omniroute/data/storage.sqlite

# One immediate synchronization pass
OminiRTKSync --once --db-path ~/.omniroute/data/storage.sqlite

# Continuous daemon with the dashboard
OminiRTKSync --daemon --db-path ~/.omniroute/data/storage.sqlite

# Daemon without the web server
OminiRTKSync --daemon --no-web
```

`--db-path` is optional: without it the synchronizer probes the usual locations. See
[Configuration](Configuration).

---

## Running the tests

```bash
source .venv/bin/activate
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"
```

Or with no local install at all — the Makefile creates the virtualenv for you:

```bash
make venv && make test
```

---

## Upgrading

```bash
docker compose pull ominirtksync
docker compose up -d ominirtksync
```

State that survives upgrades lives in the data volume: `.dashboard_auth.json` (screen-set
credentials), `.dashboard_recovery` (break-glass hash) and `ui_prefs.sqlite` (interface
language). None of them are stored in the gateway's own database.

## Configuration: `.env` from the example

Everything is configured by environment variable, read from a `.env` next to the
compose file — Compose finds it on its own, with no flag.

```bash
make setup      # creates .env from .env.example, never overwriting an existing one
```

The target then lists exactly which variables were left blank. Fill them in and
bring the stack up.

`.env` is never versioned, and `.env.example` carries no secret value — a value
published in an example file is a public credential by definition. A test
guarantees every variable a compose requires exists in the example, so
`cp .env.example .env` never produces an incomplete `.env`.

## Ports

The three synchronizers listen on the **same port inside the container**
(`9090`) and publish on different host ports, so all three can run side by side.
Same for the gateways.

| Service | Inside | Published |
| :--- | :--- | :--- |
| 9Router | `20128` | `20128` |
| OmniRoute | `20128` | `20129` |
| LiteLLM | `4000` | `20130` |
| 9RTKSync panel | `9090` | `9091` |
| OminiRTkSync panel | `9090` | `9092` |
| LiteLlmRTKSync panel | `9090` | `9093` |

All bound to `127.0.0.1`: the gateway holds real credentials and should not be
reachable from the local network.
