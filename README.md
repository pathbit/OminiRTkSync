# OminiRTKSync · OminiRoute Universal Token & Connection Synchronizer

[![CI](https://github.com/pathbit/OminiRTkSync/actions/workflows/ci.yml/badge.svg)](https://github.com/pathbit/OminiRTkSync/actions/workflows/ci.yml)
[![Release and Docker Package](https://github.com/pathbit/OminiRTkSync/actions/workflows/release.yml/badge.svg)](https://github.com/pathbit/OminiRTkSync/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.14.7-blue.svg)](https://www.python.org/ftp/python/3.14.7/python-3.14.7-macos11.pkg)
[![Docker Package](https://img.shields.io/badge/docker-ghcr.io%2Fpathbit%2Fominirtksync-blue)](https://github.com/pathbit/OminiRTkSync/pkgs/container/ominirtksync)

O **`OminiRTKSync`** (*OminiRoute Universal Token & Connection Synchronizer*) é o sincronizador e guardião de conexões dedicado ao gateway [OmniRoute](https://github.com/diegosouzapw/OmniRoute). Ele gerencia a persistência relacional de credenciais, auto-renovação de tokens OAuth e prevenção de interrupções de rota em inteligência artificial.

Caso esteja utilizando o 9Router original, utilize o projeto irmão [9RTKSync](https://github.com/pathbit/9RTKSync) configurado para a arquitetura do [9Router](https://github.com/decolua/9router).

---

## Recursos Principais

* **Compatibilidade com Schema Relacional do OmniRoute**
  * Sincronização direta com a tabela `provider_connections` do SQLite (`storage.sqlite`), manipulando campos nativos como `access_token`, `refresh_token`, `expires_at` e `test_status`.
* **Renovação Contínua de Tokens OAuth**
  * Auto-renovação de contas Google Antigravity e Gemini CLI antes de sua expiração com margem de segurança ajustável.
* **Auto-Detecção de Bancos de Dados**
  * Detecção automática entre caminhos padrão do container (`/app/data/storage.sqlite`) e instalações locais (`~/.omniroute/data/storage.sqlite`).
* **Dashboard Web Embutido**
  * Painel de controle na porta `9191` para monitoramento do estado de cada conexão registrada e acionamento sob demanda de sincronização.
* **Isolamento Completo em Virtual Environment**
  * Execução segura e isolada em ambiente virtual Python tanto em containers Docker (`/opt/venv`) quanto em instalações de desenvolvimento local (`.venv`).

---

## Como Executar via Docker

O pacote Docker oficial do OminiRTKSync é distribuído via GitHub Container Registry (GHCR):

```bash
docker pull ghcr.io/pathbit/ominirtksync:latest
```

### Exemplo no Docker Compose

Integre o `OminiRTKSync` ao seu `docker-compose.yml` junto ao [OmniRoute](https://github.com/diegosouzapw/OmniRoute):

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
    container_name: OminiRTKSync
    restart: unless-stopped
    ports:
      - "127.0.0.1:9191:9191"
    volumes:
      - omniroute_data:/app/data
      - ${HOME}:/root/host:ro
    environment:
      - HOST_HOME=/root/host
      - DB_PATH=/app/data/storage.sqlite
      - OMNIROUTE_URL=http://omniroute:20128
      - SYNC_INTERVAL=300
      - REFRESH_MARGIN=900
      - ENABLE_WEB_DASHBOARD=1
      - WEB_PORT=9191
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

## Como Executar Localmente em Virtual Environment

Para executar diretamente no host utilizando [Python 3.14.7](https://www.python.org/ftp/python/3.14.7/python-3.14.7-macos11.pkg):

### 1. Clonar o Repositório

```bash
git clone https://github.com/pathbit/OminiRTkSync.git
cd OminiRTkSync
```

### 2. Criar e Ativar o Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### 3. Comandos Disponíveis

```bash
# Exibir status das conexões do OmniRoute
OminiRTKSync --status --db-path /caminho/para/storage.sqlite

# Executar uma rodada única imediata de sincronização
OminiRTKSync --once --db-path /caminho/para/storage.sqlite

# Executar em modo daemon contínuo com dashboard web
OminiRTKSync --daemon --db-path /caminho/para/storage.sqlite
```

---

## Variáveis de Ambiente

| Variável | Padrão | Descrição |
| :--- | :--- | :--- |
| `DB_PATH` | `/app/data/storage.sqlite` | Caminho do arquivo SQLite do OmniRoute |
| `OMNIROUTE_URL` | `http://127.0.0.1:20128` | URL base do gateway OmniRoute para testes de conectividade |
| `SYNC_INTERVAL` | `300` | Intervalo em segundos entre varreduras no modo daemon |
| `REFRESH_MARGIN` | `900` | Margem prévia em segundos para renovação de tokens |
| `ENABLE_WEB_DASHBOARD` | `1` | Ativa o dashboard web embutido (`1` para sim, `0` para não) |
| `WEB_PORT` | `9191` | Porta do dashboard web HTTP |
| `WEB_HOST` | `0.0.0.0` | Interface de rede para o servidor web |
| `ANTIGRAVITY_TOKEN_PATH` | auto | Caminho customizado para arquivo de token do Antigravity |

---

## Dashboard Web

Com `ENABLE_WEB_DASHBOARD=1`, acesse no navegador:

👉 **http://localhost:9191**

Recursos do painel:
* Monitoramento de todas as conexões cadastradas no OmniRoute.
* Estado de ativação de chaves de API e contas OAuth 2.0.
* Disparo de sincronização imediata via API REST (`POST /api/sync`).

---

## Testes Unitários

Execute a suíte de testes completa dentro do virtual environment:

```bash
source .venv/bin/activate
PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"
```

---

## Contribuição e Proteção da Branch Master

* A branch `master` é protegida. Toda contribuição deve ser enviada via Pull Request e passar pela suíte de integração contínua.
* Questões e sugestões podem ser submetidas em [Issues](https://github.com/pathbit/OminiRTkSync/issues).
* Referência oficial do projeto base: [OmniRoute no GitHub](https://github.com/diegosouzapw/OmniRoute).

---

## 📄 Licença

Distribuído sob a Licença MIT. O texto completo está em [LICENSE](https://github.com/pathbit/OminiRTkSync/blob/master/LICENSE).

Na prática: use, copie, altere e redistribua à vontade, inclusive comercialmente, desde que o aviso de copyright e a licença acompanhem as cópias. O software é fornecido como está, sem garantias.

---

Desenvolvido com ❤️ pela [Pathbit](https://pathbit.co/)
