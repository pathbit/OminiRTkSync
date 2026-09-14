# OminiRTKSync · OminiRoute Universal Token & Connection Synchronizer

[![CI](https://github.com/pathbit/OminiRTkSync/actions/workflows/ci.yml/badge.svg)](https://github.com/pathbit/OminiRTkSync/actions/workflows/ci.yml)
[![Release and Docker Package](https://github.com/pathbit/OminiRTkSync/actions/workflows/release.yml/badge.svg)](https://github.com/pathbit/OminiRTkSync/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.14.7-blue.svg)](https://www.python.org/ftp/python/3.14.7/python-3.14.7-macos11.pkg)
[![Docker Package](https://img.shields.io/badge/docker-ghcr.io%2Fpathbit%2Fominirtksync-blue)](https://github.com/pathbit/OminiRTkSync/pkgs/container/ominirtksync)

**`OminiRTKSync`** (*OminiRoute Universal Token & Connection Synchronizer*) is the dedicated connection synchronizer and guardian for the [OmniRoute](https://github.com/diegosouzapw/OmniRoute) gateway. It manages relational credential persistence, proactive OAuth token renewal, and prevents routing outages in artificial intelligence workloads.

If you are running the original 9Router, use the sibling project [9RTKSync](https://github.com/pathbit/9RTKSync), configured for the [9Router](https://github.com/decolua/9router) architecture.


## Documentation

The full documentation lives in the [project wiki](../../wiki): installation, the complete
environment-variable contract, the dashboard, authentication and break-glass recovery,
persistent logging, architecture, troubleshooting, and the upstream gateway fixes.

Wiki pages are generated from [`docs/wiki/`](docs/wiki) — edit them there and open a pull
request; a push to `master` republishes the wiki automatically.

---

## Core Features

* **OmniRoute Relational Schema Compatibility**
  * Direct synchronization with the SQLite `provider_connections` table (`storage.sqlite`), handling native fields such as `access_token`, `refresh_token`, `expires_at`, and `test_status`.
* **Continuous OAuth Token Renewal**
  * Proactive renewal of Google Antigravity and Gemini CLI accounts before expiration, with an adjustable safety margin.
* **Database Auto-Detection**
  * Automatic detection between the standard container path (`/app/data/storage.sqlite`) and local installations (`~/.omniroute/data/storage.sqlite`).
* **Built-in Web Dashboard**
  * Control panel on port `9090` (published on `9092`) for monitoring the state of every registered connection and triggering synchronization on demand.
* **Strict Virtual Environment Execution**
  * Safe, isolated execution in a Python virtual environment both in Docker containers (`/opt/venv`) and in local development setups (`.venv`).

---

---

## 🔑 Signing in to the dashboard

| | |
| :--- | :--- |
| **Address** | `http://localhost:9092` |
| **User** | `admin` — or whatever you set in `DASHBOARD_USER` |
| **Password** | the value of `DASHBOARD_PASSWORD` in your `.env` |

There is **no factory password**, and that is deliberate: a fixed password shipped
in an image is public the moment the image is. You choose it once, in one place:

```bash
cp .env.example .env
# edit .env:
DASHBOARD_USER=admin
DASHBOARD_PASSWORD=<the password you choose>
```

Then bring the stack up. That user and that password are what the panel accepts.

### Did not set a password, and now cannot get in?

On first boot with `DASHBOARD_PASSWORD` empty, the container generates a
**recovery credential** and writes it inside the data directory. Read it:

```bash
docker exec ominirtk-sync cat /app/data/.dashboard_recovery
```

Sign in as `admin` with that value, then set a real password on the screen. The
recovery credential keeps working afterwards — it is break-glass, and one that
stopped working the moment you set a password would be useless exactly when you
need it.

> **Português:** o painel pede usuário e senha. O usuário é `admin` (ou o que
> estiver em `DASHBOARD_USER`) e a senha é a que **você** definir em
> `DASHBOARD_PASSWORD` no `.env` — não existe senha de fábrica, porque um valor
> fixo publicado na imagem é uma credencial pública. Se subiu sem definir senha,
> use o comando acima para ler a credencial de recuperação e entre com ela.

## Running with Docker

### Configuration: the `.env` from the example

The whole configuration comes from environment variables, read from an `.env`
sitting next to `docker-compose.yml` — Compose finds it on its own, with no flag.

```bash
make setup      # creates the .env from .env.example, never overwriting an existing one
```

The target finishes by listing exactly which variables were left blank and need
to be filled in. Fill them in, then bring the stack up.

The `.env` is **never** versioned, and `.env.example` carries no secret value —
a value published in an example file is, by definition, a public credential. A
test guarantees that every variable a compose file requires exists in the
example, so that `cp .env.example .env` never produces an incomplete `.env`.

### Ports, and why each one differs

The three synchronizers listen on the **same port inside the container** (`9090`)
and publish on different host ports, so that all three can run side by side. The
same goes for the gateways: each one has its own.

| Service | Internal port | Published on the host |
| :--- | :--- | :--- |
| 9Router | `20128` | `8081` |
| OmniRoute | `20128` | `8082` |
| LiteLLM | `4000` | `8083` |
| 9RTKSync (dashboard) | `9090` | `9091` |
| OminiRTkSync (dashboard) | `9090` | `9092` |
| LiteLlmRTKSync (dashboard) | `9090` | `9093` |

The article stack (`claudegravity`) keeps **`20128`**, the default 9Router port.
The repository stacks deliberately move out of that range: that way you can run
the article and all three synchronizers at the same time, with no conflict.

Everything is bound to `127.0.0.1`: the gateway carries real credentials and must
not be reachable on the local network. To change any of them, edit the left-hand
side of the mapping in the compose file — the right-hand side is the internal
port, the one the process listens on.


Official multi-architecture Docker images (`linux/amd64` and `linux/arm64`) are published automatically to the GitHub Container Registry (GHCR):

```bash
docker pull ghcr.io/pathbit/ominirtksync:latest
```

### Docker Compose Example

Add `OminiRTKSync` to your `docker-compose.yml` alongside [OmniRoute](https://github.com/diegosouzapw/OmniRoute):

```yaml
name: ominirtksync-stack

services:
  ominirtk-router:
    image: diegosouzapw/omniroute:latest
    container_name: ominirtk-router
    hostname: ominirtk-router
    networks:
      - ominirtksync-net
    restart: unless-stopped
    ports:
      # Porta interna 20128 (padrao do OmniRoute); publicada em 8082 no host.
      - "127.0.0.1:8082:20128"
    environment:
      - DATA_DIR=/app/data
      - PORT=20128
      - HOSTNAME=0.0.0.0
      - NEXT_PUBLIC_BASE_URL=http://localhost:8082
      - NODE_ENV=production
      # Sem valor de fallback: um default publicado em arquivo de exemplo vira
      # a senha real de toda implantacao que so copiou e colou.
      - INITIAL_PASSWORD=${INITIAL_PASSWORD:?defina INITIAL_PASSWORD no .env}
      - JWT_SECRET=${JWT_SECRET:?defina JWT_SECRET no .env (openssl rand -hex 32)}
      - REQUIRE_API_KEY=false
      # REQUIRE_LOGIN=false so e aceitavel porque a porta acima esta presa em
      # 127.0.0.1. Ao expor 20128 na rede, mude para true.
      - REQUIRE_LOGIN=false
    volumes:
      - omniroute_data:/app/data
    # Sem este healthcheck o `condition: service_healthy` la embaixo nao tem o
    # que esperar, e o compose recusa subir a stack inteira.
    healthcheck:
      test:
        - CMD-SHELL
        - >-
          node -e "require('http').get('http://127.0.0.1:20128/',r=>process.exit(r.statusCode<500?0:1)).on('error',()=>process.exit(1))"
      interval: 15s
      timeout: 5s
      retries: 10
      # O primeiro boot roda as migracoes e cria o storage.sqlite; da folga.
      start_period: 40s

  ominirtk-sync:
    # Mesmo uid do gateway. Os dois compartilham o volume, e rodando como root
    # os diretorios criados no startup nasciam com dono root: o omniroute --
    # que roda como `node` (1000) -- perdia a escrita no proprio volume.
    user: "1000:1000"
    image: ghcr.io/pathbit/ominirtksync:latest
    container_name: ominirtk-sync
    hostname: ominirtk-sync
    networks:
      - ominirtksync-net
    restart: unless-stopped
    ports:
      # Porta interna 9090 (igual no 9RTKSync); publicada em 9092 no host.
      - "127.0.0.1:9092:9090"
    volumes:
      - omniroute_data:/app/data
      - ${HOME}:/root/host:ro
      - ominirtksync_logs:/app/logs
    environment:
      - HOST_HOME=/root/host
      - DB_PATH=/app/data/storage.sqlite
      - OMNIROUTE_URL=${OMNIROUTE_URL:-http://ominirtk-router:20128}
      - SYNC_INTERVAL=${SYNC_INTERVAL:-300}
      - REFRESH_MARGIN=${REFRESH_MARGIN:-900}
      - ENABLE_WEB_DASHBOARD=${ENABLE_WEB_DASHBOARD:-1}
      - WEB_PORT=${WEB_PORT:-9090}
      - DASHBOARD_USER=${DASHBOARD_USER:-admin}
      - DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD:-}
      - LOG_DIR=${LOG_DIR:-/app/logs}
      - LOG_RETENTION_DAYS=${LOG_RETENTION_DAYS:-30}
    depends_on:
      ominirtk-router:
        # Esperar o gateway ficar saudavel, e nao apenas iniciado: o OmniRoute
        # cria o storage.sqlite durante o proprio boot, e subir antes disso faz
        # o primeiro ciclo encontrar o banco ausente.
        condition: service_healthy
    healthcheck:
      test: ["CMD", "/opt/venv/bin/python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/healthz', timeout=3)"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  omniroute_data:
  ominirtksync_logs:

networks:
  ominirtksync-net:
    name: ominirtksync-net
    # Rede propria da stack. Na rede default, duas stacks no mesmo daemon
    # resolvem o mesmo nome curto e nao da para saber a qual gateway o
    # sincronizador se conectou.
```

---

## Local Development in Virtual Environment

Following standard environment isolation, local runs strictly use a Python virtual environment with [Python 3.14.7](https://www.python.org/ftp/python/3.14.7/python-3.14.7-macos11.pkg):

### 1. Clone the Repository

```bash
git clone https://github.com/pathbit/OminiRTkSync.git
cd OminiRTkSync
```

### 2. Create and Activate the Virtual Environment

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

### 4. Available CLI Commands

```bash
# View current status of OmniRoute connections
OminiRTKSync --status --db-path /path/to/storage.sqlite

# Run an immediate one-shot synchronization pass
OminiRTKSync --once --db-path /path/to/storage.sqlite

# Run continuous background daemon with web dashboard on port 9090 (published on 9092)
OminiRTKSync --daemon --db-path /path/to/storage.sqlite
```

---

## Environment Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DB_PATH` | `/app/data/storage.sqlite` | Absolute path to the OmniRoute SQLite database |
| `OMNIROUTE_URL` | `http://127.0.0.1:20128` | Base URL of the OmniRoute gateway for diagnostics and integration |
| `SYNC_INTERVAL` | `300` | Sync and background cron loop interval in seconds |
| `REFRESH_MARGIN` | `900` | Proactive token renewal margin in seconds before expiration |
| `ENABLE_WEB_DASHBOARD` | `1` | Enable the embedded web dashboard (`1` to enable, `0` to disable) |
| `WEB_PORT` | `9090` | HTTP port for the web dashboard |
| `WEB_HOST` | `0.0.0.0` | Network binding interface for the dashboard web server |
| `DASHBOARD_USER` | `admin` | HTTP Basic Auth username for web dashboard access |
| `DASHBOARD_PASSWORD` | *(empty)* | Panel password. Left empty, the first sign-in uses the recovery credential generated on first boot. |
| `ANTIGRAVITY_TOKEN_PATH` | auto | Custom path for Antigravity OAuth token file |

---

## Web Dashboard

When running with `ENABLE_WEB_DASHBOARD=1`, access the dashboard in your browser:

👉 **http://localhost:9092**

Dashboard capabilities:
* Operational metrics (Total Connections, OAuth Accounts, API Keys, Resilience Combos).
* Six domain cards, in the same order as the sibling panels: gateway connection, scheduler, monitored connections, virtual keys, registered models, resilience combos.
* Activation state of API keys and OAuth 2.0 accounts, with the remaining validity of every connection.
* Gateway diagnostic card with millisecond latency testing, behind the **Test connection** button.
* Password change modal for credential rotation.
* The page is rendered on the server, and every button is a real request that redirects back to the freshly rendered page (POST-Redirect-GET, through `/acoes/`). The JSON endpoints are kept for automation: `POST /api/sync` and `POST /api/cron-run` trigger a pass, `GET /api/status` and `GET /api/cron-status` report state.

---

## Unit and Integration Testing

You can run the test suite inside your virtual environment, or bring up the live
bench to check the whole stack end to end.

### Option 1. Local Virtual Environment

```bash
source .venv/bin/activate
make test
# Or directly
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"
```

### Option 2. Live Bench with Docker

`docker-compose.test.yml` has no test-runner service: it is a live bench (a real
OmniRoute gateway plus this synchronizer) on its own ports and container names,
so it coexists with any other stack on the same machine.

```bash
docker compose -f docker-compose.test.yml up -d
docker compose -f docker-compose.test.yml down -v
```

---

## Contributing and Branch Protection

* The `master` branch is protected. All contributions must be submitted through Pull Requests and pass all CI checks.
* For bug reports or new provider requests, please open an issue in [GitHub Issues](https://github.com/pathbit/OminiRTkSync/issues).
* Official upstream gateway: [OmniRoute on GitHub](https://github.com/diegosouzapw/OmniRoute).

---

## 📄 License

Distributed under the MIT License. The full text is available in [LICENSE](https://github.com/pathbit/OminiRTkSync/blob/master/LICENSE).

In practice: use, copy, modify, and distribute freely, including commercially, provided that copyright and license notices accompany copies. The software is provided as is, without warranty.

---

Developed with ❤️ by [Pathbit](https://pathbit.co/)
