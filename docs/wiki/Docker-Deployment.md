# Docker & Docker Compose Deployment Guide

*(Versão em português disponível na segunda metade desta página.)*

Complete guide to deploying **OminiRTkSync** and **OmniRoute** via Docker and Docker Compose, including token optimization with **Headroom** and **Caveman**, remote access via **Cloudflare Tunnel** and **Tailscale**, and automated container package retention.

---

## 1. Official Docker Packages & 3-Version Retention Policy

Official multi-architecture Docker images (`linux/amd64` and `linux/arm64`) are published to the GitHub Container Registry (GHCR):

```bash
# Pull latest stable release
docker pull ghcr.io/pathbit/ominirtksync:latest
```

### Automated Package Retention (Last 3 Versions)

To ensure high availability, rollback safety, and clean storage, the GitHub Actions CI/CD pipeline enforces an automated package retention policy:
- **Always keeps the last 3 versions**: the active `latest` tag plus the 2 most recent tagged releases / commit SHAs.
- Multi-architecture manifest lists are resolved before deleting orphan manifests, ensuring platform layers remain fully functional.
- Runs automatically after every successful release build and on a weekly schedule.

> [!NOTE]
> **GHCR Visibility & Permissions**: GitHub Container Registry packages in organizations default to Private upon initial push. If `docker pull` returns `unauthorized`, an organization administrator must navigate to `github.com/orgs/pathbit/packages/container/ominirtksync/settings` and set the package visibility to **Public**.

---

## 2. Quick Start with Standalone Docker

If you already have an OmniRoute gateway running on your host machine or network:

```bash
docker run -d \
  --name ominirtk-sync \
  --restart unless-stopped \
  -p 127.0.0.1:9092:9090 \
  -v omniroute_data:/app/data \
  -v "${HOME}:/root/host:ro" \
  -v ominirtksync_logs:/app/logs \
  -e DB_PATH=/app/data/storage.sqlite \
  -e OMNIROUTE_URL=http://host.docker.internal:20128 \
  -e DASHBOARD_USER=admin \
  -e DASHBOARD_PASSWORD=your-secure-password \
  ghcr.io/pathbit/ominirtksync:latest
```

---

## 3. Production Deployment with Docker Compose

The standard production setup runs OmniRoute (gateway) and OminiRTkSync (synchronizer) on an isolated internal management network (`ominirtksync-net`), with optional shared inference networking (`rtk-inference-net`).

### Step 1: Initialize Configuration

```bash
cp .env.example .env
# Edit .env and configure INITIAL_PASSWORD, JWT_SECRET, and DASHBOARD_PASSWORD
```

### Step 2: Launch Stack

```bash
docker compose -f docker-compose.example.yml up -d
```

### Service Map and Port Allocation

| Service | Container Name | Internal Port | Host Published Port | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **OmniRoute** | `ominirtk-router` | `20128` | `127.0.0.1:8082` | Gateway API & Admin |
| **OminiRTkSync** | `ominirtk-sync` | `9090` | `127.0.0.1:9092` | Guardian Dashboard & Healthz |
| **Headroom** *(optional)* | `ominirtk-headroom` | `8787` | `127.0.0.1:8787` | Token Compression Proxy |

Dashboard access: **http://localhost:9092**

---

## 4. Token & Cost Optimization: Headroom & Caveman

AI coding agents (such as **Claude Code**, **Cursor**, **Windsurf**, and **Roo Code**) consume vast amounts of tokens when sending large files, terminal logs, git diffs, and conversational explanations. Using **Headroom** and **Caveman** together drastically cuts token consumption and prevents rate limits.

```
┌────────────────────────────────┐
│   Coding Agent / Developer     │
│   (Claude Code / Cursor)       │
└───────────────┬────────────────┘
                │
                ▼
┌────────────────────────────────┐
│      Caveman (Plugin/Skill)    │  <-- Minimizes OUTPUT tokens
│   (Enforces concise responses) │
└───────────────┬────────────────┘
                │
                ▼
┌────────────────────────────────┐
│     Headroom (Proxy: 8787)     │  <-- Compresses INPUT tokens
│  (Trims logs, AST & context)   │
└───────────────┬────────────────┘
                │
                ▼
┌────────────────────────────────┐
│    OmniRoute (Gateway: 8082)   │  <-- Routing & Relational Mapping
└───────────────▲────────────────┘
                │
┌───────────────┴────────────────┐
│  OminiRTkSync (Guardian: 9092) │  <-- OAuth renewal & rate-limit healing
└────────────────────────────────┘
```

### Headroom: Input Token Compression Proxy

Headroom acts as a middleware proxy sitting between your coding agent and OmniRoute:
- Analyzes context payloads, logs, git diffs, and structured JSON.
- Compresses input tokens by 20% to 80% without losing critical code semantics.
- To start with Docker Compose:

```bash
docker compose -f docker-compose.example.yml --profile headroom up -d
```

Configure your coding tool to point to Headroom (`http://127.0.0.1:8787/v1`) with OmniRoute credentials. Headroom automatically proxies optimized requests to `http://ominirtk-router:20128`.

### Caveman: Output Token Reduction Skill

While Headroom compresses what the LLM *reads*, Caveman minimizes what the LLM *writes*:
- Enforces an ultra-terse, compact agent communication style ("concise mode").
- Strips conversational filler, preamble, and pleasantries while maintaining strict accuracy for code edits and terminal actions.
- Configured in Claude Code (`settings.local.json` prompt instructions) or agent system instructions.

---

## 5. Remote Access: Cloudflare Tunnel & Tailscale

By default, ports are bound to `127.0.0.1` for loopback security. When you need remote access from laptops, mobile devices, or team members, enable Cloudflare Tunnel or Tailscale.

> [!WARNING]
> **Security Prerequisite**: Before enabling any public tunnel or remote exposure, you **must** set:
> 1. `REQUIRE_LOGIN=true` (with a strong custom password)
> 2. `REQUIRE_API_KEY=true` (for coding tool authentication)
> Exposing the gateway without these settings allows anyone with the URL to consume your LLM balances.

### Option A: Cloudflare Quick Tunnel (Ephemeral URL)

Useful for temporary demonstrations and remote testing without domain setup:

```bash
docker compose -f docker-compose.example.yml --profile tunel up -d
```

View the generated `https://...trycloudflare.com` URL in container logs:

```bash
docker logs ominirtk-tunel
```

### Option B: Cloudflare Named Tunnel (Persistent Domain)

For permanent custom domains (`omni.yourdomain.com`) with Cloudflare Zero Trust:

1. Create a tunnel in the Cloudflare Zero Trust dashboard.
2. Add your tunnel token to `.env`:
   ```bash
   TUNNEL_TOKEN=eyJhIjoi...
   ```
3. Start the tunnel profile:
   ```bash
   docker compose -f docker-compose.example.yml --profile tunel up -d
   ```

### Option C: Tailscale (Private Zero-Trust Tailnet — Recommended)

The safest method: your gateway joins your private tailnet and is never exposed to the public internet.

1. Generate an ephemeral or reusable auth key at `https://login.tailscale.com/admin/settings/keys`.
2. Add `TS_AUTHKEY` to `.env`:
   ```bash
   TS_AUTHKEY=tskey-auth-...
   ```
3. Start the tailnet container:
   ```bash
   docker compose -f docker-compose.example.yml --profile tailnet up -d
   ```
4. Access via MagicDNS at `http://ominirtk:20128` or Tailscale IP `100.x.y.z:20128`.

Alternatively, install Tailscale directly on the host machine:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4
```

---

## 6. Container Healthchecks & Monitoring

Verify the status of all services:

```bash
docker compose -f docker-compose.example.yml ps
```

The synchronizer exposes `/healthz` on port 9090 (host 9092):
- `OK` (HTTP 200): Synchronizer active and gateway reachable.
- `DATABASE_NOT_READY` (HTTP 503): Database path not found.
- `GATEWAY_SERVICE_UNREACHABLE` (HTTP 503): OmniRoute gateway not answering.

---

*(Versão em Português)*

# Guia de Implantação via Docker e Docker Compose

Guia completo para implantar o **OminiRTkSync** e o **OmniRoute** via Docker e Docker Compose, com otimização de tokens via **Headroom** e **Caveman**, acesso remoto seguro via **Cloudflare Tunnel** e **Tailscale**, e retenção automatizada de imagens no GitHub Packages.

## 1. Pacotes Oficiais e Política de Retenção de 3 Versões

As imagens oficiais multi-arquitetura (`linux/amd64` e `linux/arm64`) são geradas e publicadas no GitHub Container Registry (GHCR):

```bash
docker pull ghcr.io/pathbit/ominirtksync:latest
```

### Retenção Automática das 3 Últimas Versões

O pipeline do GitHub Actions garante que:
- **Sempre mantemos as 3 versões mais recentes**: a tag ativa `latest` mais as 2 versões/tags marcadas anteriores.
- Os manifestos multi-plataforma são resolvidos antes da limpeza para evitar a quebra de camadas.
- A limpeza ocorre ao término de cada build de release e em agendamento semanal.

> [!NOTE]
> **Visibilidade no GHCR**: Por padrão de segurança do GitHub, pacotes criados sob organizações nascem com visibilidade Privada. Se o comando `docker pull` responder com `unauthorized`, o administrador da organização no GitHub deve acessar as configurações do pacote (`github.com/orgs/pathbit/packages/container/ominirtksync/settings`) e alterar a visibilidade para **Public**.

## 2. Implantação Rápida com Docker Run

Para executar o sincronizador apontando para um OmniRoute já existente:

```bash
docker run -d \
  --name ominirtk-sync \
  --restart unless-stopped \
  -p 127.0.0.1:9092:9090 \
  -v omniroute_data:/app/data \
  -v "${HOME}:/root/host:ro" \
  -v ominirtksync_logs:/app/logs \
  -e DB_PATH=/app/data/storage.sqlite \
  -e OMNIROUTE_URL=http://host.docker.internal:20128 \
  -e DASHBOARD_USER=admin \
  -e DASHBOARD_PASSWORD=sua-senha-segura \
  ghcr.io/pathbit/ominirtksync:latest
```

## 3. Implantação Completa com Docker Compose

```bash
# 1. Prepare as variáveis de ambiente
cp .env.example .env

# 2. Inicie a stack
docker compose -f docker-compose.example.yml up -d
```

Acesse o painel web em: **http://localhost:9092**

## 4. Otimização de Tokens: Headroom e Caveman

A combinação de **Headroom** e **Caveman** atua em duas frentes complementares:
1. **Headroom (Entrada/Input):** Middleware proxy que intercepta context windows, logs de compilação e comandos git diff, compactando a carga antes do envio ao OmniRoute. Ative via perfil:
   ```bash
   docker compose -f docker-compose.example.yml --profile headroom up -d
   ```
   Aponte seus clientes para `http://127.0.0.1:8787/v1`.
2. **Caveman (Saída/Output):** Instrução de estilo conciso para agentes (Claude Code / Cursor) que elimina rodeios e respostas prolixas, gerando respostas diretas e economizando 30% a 60% de tokens de saída.

## 5. Acesso Remoto: Cloudflare Tunnel e Tailscale

Regra essencial de segurança: antes de expor qualquer rota, ative `REQUIRE_LOGIN=true` e `REQUIRE_API_KEY=true`.

- **Cloudflare Tunnel Rápido:** `docker compose -f docker-compose.example.yml --profile tunel up -d`
- **Cloudflare Tunnel Nomeado:** Preencha `TUNNEL_TOKEN` no `.env` e suba o perfil `tunel`.
- **Tailscale (VPN Privada):** Preencha `TS_AUTHKEY` no `.env` e execute `docker compose -f docker-compose.example.yml --profile tailnet up -d`.
