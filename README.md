# OminiRTKSync · OminiRoute Universal Token & Connection Synchronizer

[![CI](https://github.com/pathbit/OminiRTkSync/actions/workflows/ci.yml/badge.svg)](https://github.com/pathbit/OminiRTkSync/actions/workflows/ci.yml)
[![Release and Docker Package](https://github.com/pathbit/OminiRTkSync/actions/workflows/release.yml/badge.svg)](https://github.com/pathbit/OminiRTkSync/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.14.7-blue.svg)](https://www.python.org/ftp/python/3.14.7/python-3.14.7-macos11.pkg)
[![Docker Package](https://img.shields.io/badge/docker-ghcr.io%2Fpathbit%2Fominirtksync-blue)](https://github.com/pathbit/OminiRTkSync/pkgs/container/ominirtksync)

O **`OminiRTKSync`** (*OminiRoute Universal Token & Connection Synchronizer*) é o sincronizador e guardião de conexões dedicado ao gateway [OmniRoute](https://github.com/diegosouzapw/OmniRoute). Ele gerencia a persistência relacional de credenciais, auto-renovação de tokens OAuth e prevenção de interrupções de rota em inteligência artificial.

Caso esteja utilizando o 9Router original, utilize o projeto irmão [9RTKSync](https://github.com/pathbit/9RTKSync) configurado para a arquitetura do [9Router](https://github.com/decolua/9router).


## Documentation

The full documentation lives in the [project wiki](../../wiki): installation, the complete
environment-variable contract, the dashboard, authentication and break-glass recovery,
persistent logging, architecture, troubleshooting, and the upstream gateway fixes.

Wiki pages are generated from [`docs/wiki/`](docs/wiki) — edit them there and open a pull
request; a push to `master` republishes the wiki automatically.

---

## Recursos Principais

* **Compatibilidade com Schema Relacional do OmniRoute**
  * Sincronização direta com a tabela `provider_connections` do SQLite (`storage.sqlite`), manipulando campos nativos como `access_token`, `refresh_token`, `expires_at` e `test_status`.
* **Renovação Contínua de Tokens OAuth**
  * Auto-renovação de contas Google Antigravity e Gemini CLI antes de sua expiração com margem de segurança ajustável.
* **Auto-Detecção de Bancos de Dados**
  * Detecção automática entre caminhos padrão do container (`/app/data/storage.sqlite`) e instalações locais (`~/.omniroute/data/storage.sqlite`).
* **Dashboard Web Embutido**
  * Painel de controle na porta `9090` (publicada em `9092`) para monitoramento do estado de cada conexão registrada e acionamento sob demanda de sincronização.
* **Isolamento Completo em Virtual Environment**
  * Execução segura e isolada em ambiente virtual Python tanto em containers Docker (`/opt/venv`) quanto em instalações de desenvolvimento local (`.venv`).

---

---

## 🔑 Como entrar no painel

| | |
| :--- | :--- |
| **Endereço** | `http://localhost:9092` |
| **Usuário** | `admin` — ou o que você definir em `DASHBOARD_USER` |
| **Senha** | o valor de `DASHBOARD_PASSWORD` no seu `.env` |

**Não existe senha de fábrica**, e isso é deliberado: uma senha fixa publicada
na imagem vira credencial pública no instante em que a imagem é publicada. Você
escolhe a sua uma vez, num lugar só:

```bash
cp .env.example .env
# edite o .env:
DASHBOARD_USER=admin
DASHBOARD_PASSWORD=<a senha que voce escolher>
```

Suba a stack em seguida. Esse usuário e essa senha são o que o painel aceita.

### Subiu sem definir senha e agora não entra?

No primeiro boot com `DASHBOARD_PASSWORD` vazio, o container gera uma
**credencial de recuperação** e a grava dentro do diretório de dados. Leia com:

```bash
docker exec ominirtk-sync cat /app/data/.dashboard_recovery
```

Entre como `admin` com esse valor e defina a sua senha pela tela. A credencial
de recuperação continua valendo depois disso — ela é o arrombamento de vidro, e
uma que parasse de funcionar assim que você define uma senha seria inútil
justamente quando é necessária.

> **English:** the panel asks for user and password. The user is `admin` (or
> whatever `DASHBOARD_USER` says) and the password is the one **you** set in
> `DASHBOARD_PASSWORD` — there is no factory password, because a fixed value
> shipped in an image is a public credential. If you brought the stack up
> without setting one, use the command above to read the recovery credential.

## Como Executar via Docker

### Configuração: `.env` a partir do exemplo

A configuração inteira vem de variáveis de ambiente, lidas de um `.env` ao lado
do `docker-compose.yml` — o Compose o encontra sozinho, sem nenhuma flag.

```bash
make setup      # cria o .env a partir do .env.example, sem sobrescrever um existente
```

O alvo lista, ao final, exatamente quais variáveis ficaram em branco e precisam
ser preenchidas. Preencha e suba a stack.

O `.env` **nunca** é versionado, e o `.env.example` não carrega nenhum valor de
segredo — um valor publicado num arquivo de exemplo é, por definição, uma
credencial pública. Um teste garante que toda variável exigida por um compose
existe no exemplo, para que `cp .env.example .env` nunca produza um `.env`
incompleto.

### Portas, e por que cada uma é diferente

Os três sincronizadores escutam na **mesma porta dentro do container** (`9090`)
e publicam em portas diferentes no host, para que os três possam rodar lado a
lado. O mesmo vale para os gateways: cada um tem a sua.

| Serviço | Porta interna | Publicada no host |
| :--- | :--- | :--- |
| 9Router | `20128` | `8081` |
| OmniRoute | `20128` | `8082` |
| LiteLLM | `4000` | `8083` |
| 9RTKSync (painel) | `9090` | `9091` |
| OminiRTkSync (painel) | `9090` | `9092` |
| LiteLlmRTKSync (painel) | `9090` | `9093` |

A stack dos artigos (`claudegravity`) fica com a **`20128`**, a porta padrão do
9Router. As stacks dos repositórios saem dessa faixa de propósito: assim você
roda o artigo e os três sincronizadores ao mesmo tempo, sem conflito.

Tudo preso a `127.0.0.1`: o gateway carrega credenciais reais e não deve ficar
acessível na rede local. Para mudar qualquer uma, altere o lado esquerdo do
mapeamento no compose — o lado direito é a porta interna, que o processo escuta.


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
    container_name: ominirtk-router
    restart: unless-stopped
    ports:
      # 20128 dentro do container; 8082 no host.
      - "127.0.0.1:8082:20128"
    environment:
      - DATA_DIR=/app/data
      - PORT=20128
      - HOSTNAME=0.0.0.0
      - NEXT_PUBLIC_BASE_URL=http://localhost:8082
      - NODE_ENV=production
      - INITIAL_PASSWORD=${INITIAL_PASSWORD:?defina no .env}
      - JWT_SECRET=${JWT_SECRET:?openssl rand -hex 32}
      - REQUIRE_API_KEY=false
      - REQUIRE_LOGIN=false
    volumes:
      - omniroute_data:/app/data

  ominirtksync:
    image: ghcr.io/pathbit/ominirtksync:latest
    container_name: ominirtk-sync
    restart: unless-stopped
    ports:
      - "127.0.0.1:9092:9090"
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
      - WEB_PORT=${WEB_PORT:-9090}
      - DASHBOARD_USER=${DASHBOARD_USER:-admin}
      - DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD:-}
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

### 3. Configurar Variáveis de Ambiente (.env)

Copie o modelo oficial para criar seu `.env` local (o arquivo `.env` é estritamente ignorado no git):

```bash
cp .env.example .env
```

### 4. Comandos Disponíveis

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
| `SYNC_INTERVAL` | `300` | Intervalo em segundos entre varreduras no modo daemon e cron |
| `REFRESH_MARGIN` | `900` | Margem prévia em segundos para renovação de tokens |
| `ENABLE_WEB_DASHBOARD` | `1` | Ativa o dashboard web embutido (`1` para sim, `0` para não) |
| `WEB_PORT` | `9090` | Porta do dashboard web HTTP |
| `WEB_HOST` | `0.0.0.0` | Interface de rede para o servidor web |
| `DASHBOARD_USER` | `admin` | Usuário de autenticação HTTP Basic Auth |
| `DASHBOARD_PASSWORD` | *(vazio)* | Senha do painel. Vazia, o primeiro acesso usa a credencial de recuperação gerada no primeiro boot. |
| `ANTIGRAVITY_TOKEN_PATH` | auto | Caminho customizado para arquivo de token do Antigravity |

---

## Dashboard Web

Com `ENABLE_WEB_DASHBOARD=1`, acesse no navegador:

👉 **http://localhost:9092**

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
