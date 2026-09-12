# OminiRTKSync · OminiRoute Universal Token & Connection Synchronizer

[![CI](https://github.com/pathbit/OminiRTkSync/actions/workflows/ci.yml/badge.svg)](https://github.com/pathbit/OminiRTkSync/actions/workflows/ci.yml)
[![Release and Docker Package](https://github.com/pathbit/OminiRTkSync/actions/workflows/release.yml/badge.svg)](https://github.com/pathbit/OminiRTkSync/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.14.7-blue.svg)](https://www.python.org/ftp/python/3.14.7/python-3.14.7-macos11.pkg)
[![Docker Package](https://img.shields.io/badge/docker-ghcr.io%2Fpathbit%2Fominirtksync-blue)](https://github.com/pathbit/OminiRTkSync/pkgs/container/ominirtksync)

**`OminiRTKSync`** (*OminiRoute Universal Token & Connection Synchronizer*) is the dedicated connection guardian and token synchronizer for the [OmniRoute](https://github.com/diegosouzapw/OmniRoute) AI gateway. It manages relational credential persistence, continuous OAuth token renewal, and disruption-free routing across AI providers.

If you are running the original 9Router stack, refer to the sibling project [9RTKSync](https://github.com/pathbit/9RTKSync) engineered for [9Router](https://github.com/decolua/9router).

---

## Key Features

* **Relational Schema Support for OmniRoute**
  * Direct synchronization with SQLite's `provider_connections` table (`storage.sqlite`), managing native relational fields including `access_token`, `refresh_token`, `expires_at`, and `test_status`.
* **Continuous OAuth Token Renewal**
  * Automatic renewal of Google Antigravity and Gemini CLI accounts prior to expiration using a configurable safety buffer.
* **Database Auto-Discovery**
  * Automatic path detection between standard container locations (`/app/data/storage.sqlite`) and local developer setups (`~/.omniroute/data/storage.sqlite`).
* **Embedded Web Dashboard**
  * Embedded control panel on port `9191` for monitoring the status of registered connections and triggering on-demand synchronization passes.
* **Complete Virtual Environment Isolation**
  * Secure and isolated execution inside Python virtual environments both within Docker containers (`/opt/venv`) and in local development environments (`.venv`).

---

## How to Run via Docker

The official multi-arch Docker package for OminiRTKSync is published to the GitHub Container Registry (GHCR):

```bash
docker pull ghcr.io/pathbit/ominirtksync:latest
```

### Docker Compose Example

Integrate `OminiRTKSync` into your `docker-compose.yml` alongside [OmniRoute](https://github.com/diegosouzapw/OmniRoute):

```yaml
services:
  omniroute:
    image: diegosouzapw/omniroute:latest
    container_name: omniroute
    restart: unless-stopped
    ports:
      - "127.0.0.1:20128:20128"
    environment:
      - DATA_DIR=/app/data
      - PORT=20128
      - HOSTNAME=0.0.0.0
      - NEXT_PUBLIC_BASE_URL=http://localhost:20128
      - NODE_ENV=production
      - INITIAL_PASSWORD=${INITIAL_PASSWORD:-PathbitDevs2026!}
      - JWT_SECRET=${JWT_SECRET:-omniroute-jwt-secret-key-pathbit}
      - REQUIRE_API_KEY=false
      - REQUIRE_LOGIN=false
    volumes:
      - omniroute_data:/app/data

  ominirtksync:
    image: ghcr.io/pathbit/ominirtksync:latest
    container_name: router-sync
    restart: unless-stopped
    ports:
      - "127.0.0.1:9191:9191"
    volumes:
      - omniroute_data:/app/data
      - ${HOME}:/root/host:ro
    environment:
      - HOST_HOME=/root/host
      - DB_PATH=/app/data/storage.sqlite
      - OMNIROUTE_URL=${OMNIROUTE_URL:-http://omniroute:20128}
      - SYNC_INTERVAL=${SYNC_INTERVAL:-300}
      - REFRESH_MARGIN=${REFRESH_MARGIN:-900}
      - ENABLE_WEB_DASHBOARD=${ENABLE_WEB_DASHBOARD:-1}
      - WEB_PORT=${WEB_PORT:-9191}
      - DASHBOARD_USER=${DASHBOARD_USER:-admin}
      - DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD:-pathbit}
    depends_on:
      - omniroute
    healthcheck:
      test: ["CMD", "/opt/venv/bin/python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9191/healthz', timeout=3)"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  omniroute_data:
```

---

## How to Run Locally in Virtual Environment

To run directly on your host machine using [Python 3.14.7](https://www.python.org/ftp/python/3.14.7/python-3.14.7-macos11.pkg):

### 1. Clone the Repository

```bash
git clone https://github.com/pathbit/OminiRTkSync.git
cd OminiRTkSync
```

### 2. Create and Activate Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### 3. Configure Environment Variables (.env)

Copy the official template to create your local `.env` file (the `.env` file is strictly ignored by git):

```bash
cp .env.example .env
```

### 4. Available Commands

```bash
# Display OmniRoute connection status table
OminiRTKSync --status --db-path /path/to/storage.sqlite

# Execute an immediate single synchronization run
OminiRTKSync --once --db-path /path/to/storage.sqlite

# Run in continuous daemon mode with web dashboard
OminiRTKSync --daemon --db-path /path/to/storage.sqlite
```

---

## Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DB_PATH` | `/app/data/storage.sqlite` | Path to OmniRoute SQLite storage file |
| `OMNIROUTE_URL` | `http://127.0.0.1:20128` | Base URL for OmniRoute gateway health and connectivity checks |
| `SYNC_INTERVAL` | `300` | Interval in seconds between daemon passes and cron renewals |
| `REFRESH_MARGIN` | `900` | Safety buffer in seconds before expiration to trigger token refresh |
| `ENABLE_WEB_DASHBOARD` | `1` | Enable embedded HTTP web dashboard (`1` for yes, `0` for no) |
| `WEB_PORT` | `9191` | HTTP port for web dashboard |
| `WEB_HOST` | `0.0.0.0` | Network binding interface for web dashboard |
| `DASHBOARD_USER` | `admin` | Username for HTTP Basic Auth |
| `DASHBOARD_PASSWORD` | `pathbit` | Initial password for HTTP Basic Auth |
| `ANTIGRAVITY_TOKEN_PATH` | auto | Custom path to Antigravity token file |

---

## Web Dashboard

With `ENABLE_WEB_DASHBOARD=1`, open in your browser:

👉 **http://localhost:9191**

Dashboard capabilities:
* Monitoring of all connections registered in OmniRoute.
* Real-time activation status of API keys and OAuth 2.0 accounts.
* Triggering immediate synchronization via REST API (`POST /api/sync`).

---

## Unit Testing

You can run the full test suite with zero dependencies installed on your host machine (using Docker), or optionally inside a local Python virtual environment.

### Option 1. Via Docker Container (Zero Host Installation)

The only requirement is having Docker running:

```bash
# Via shell script directly
./run_tests.sh

# Or via Makefile
make test-container

# Or via Docker Compose
docker compose -f docker-compose.test.yml run --rm test
```

### Option 2. Local Virtual Environment (Optional Prerequisites)

If you prefer testing directly on your host machine with Python 3.14+:

```bash
source .venv/bin/activate
make test
# Or directly
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"
```

---

## Contributing and Branch Protection

* The `master` branch is protected. All contributions must be submitted via Pull Requests and pass the complete CI matrix.
* Feedback and bug reports can be submitted via [Issues](https://github.com/pathbit/OminiRTkSync/issues).
* Official upstream gateway repository: [OmniRoute on GitHub](https://github.com/diegosouzapw/OmniRoute).

---

## License

Distributed under the MIT License. The full text is available in [LICENSE](https://github.com/pathbit/OminiRTkSync/blob/master/LICENSE).

In short: you are free to use, copy, modify, merge, publish, distribute, sublicense, and sell copies, provided that copyright and permission notices are included in all copies. The software is provided as-is, without warranties.

---

Developed with ❤️ by [Pathbit](https://pathbit.co/)

