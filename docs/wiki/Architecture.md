# Architecture

OminiRTKSync is a sidecar. It shares the OmniRoute SQLite file through a Docker volume and repairs the
state the gateway keeps about its own connections.

```
        ┌───────────────────────────────┐
        │  host home (read-only mount)  │
        │  ~/.gemini, ~/.config/…       │
        └───────────────┬───────────────┘
                        │ discovery
                        ▼
 ┌────────────┐   ┌───────────┐   shared volume   ┌──────────────┐
 │  browser   │──►│ OminiRTKSync  │◄─────────────────►│ data.sqlite  │
 │  :9092     │   │  :9090    │                   │ providerConn │
 └────────────┘   └─────┬─────┘                   └──────▲───────┘
                        │ HTTP probe                     │
                        ▼                                │
                  ┌───────────┐                          │
                  │  OmniRoute  │──────────────────────────┘
                  │  :20128   │
                  └───────────┘
```

---

## Modules

| Module | Responsibility |
| :--- | :--- |
| `cli.py` | Argument parsing, bootstrap of logging and the recovery hash, entry points. |
| `daemon.py` | `SyncEngine.sync_all()` — one full pass over every connection. |
| `cron.py` | Background scheduler; keeps per-cycle history with the actions each produced. |
| `database.py` | SQLite reads and writes against `provider_connections` and `combos`. |
| `models.py` | `ConnectionRecord` and its derived properties (`is_oauth`, `is_local`, `remaining_seconds`, `health_status`). |
| `normalizer.py` | Credential-format self-healing and stale-lock removal. |
| `discovery.py` | Finds provider credentials on the host filesystem. |
| `providers/` | One handler per credential family: Google, generic OAuth, API key, local. |
| `combos.py` | Keeps the fallback combos registered and up to date. |
| `web.py` | HTTP server, routing, actions. |
| `render.py` | Server-side HTML rendering. |
| `i18n.py`, `prefs.py` | Interface language and its SQLite persistence. |
| `auth.py`, `logs.py` | Credential rules and the persistent file log. |

---

## One synchronization pass

`SyncEngine.sync_all()` per connection:

1. **Self-heal the format.** `normalize_connection_data()` converts `expiresAt` into the numeric
   epoch the gateway's own readers expect, derives it from `expiresIn` when absent, drops expired
   `rateLimitedUntil` (resetting `backoffLevel`) and removes expired `modelLock_*` entries.
2. **Pick a handler.** The first provider whose `can_handle()` matches wins:

   | Handler | Matches |
   | :--- | :--- |
   | `GoogleProvider` | Antigravity, Gemini CLI |
   | `GenericOAuthProvider` | Claude, Copilot, Codex, Kiro, Windsurf and other OAuth families |
   | `ApiKeyProvider` | Static API keys |
   | `LocalProvider` | Ollama, vLLM, LM Studio, OpenAI-compatible, any local `baseUrl` |

3. **Renew or probe.** OAuth handlers renew when the remaining validity drops below
   `REFRESH_MARGIN`, or when the connection carries an error or lock. `LocalProvider` queries the
   instance's model catalog. `ApiKeyProvider` checks liveness.
4. **Write back.** Only when something actually changed.

Every action is recorded twice: in the file log, and in the cycle entry the dashboard's **Logs**
button shows.

---

## Credential discovery on the host

`HostDiscoveryEngine` scans the read-only host mount for provider credential files — Antigravity
and Gemini CLI tokens under `~/.gemini/` and `~/.config/antigravity/`, plus any path given in
`ANTIGRAVITY_TOKEN_PATH`. A fresher refresh token found on the host is promoted into the gateway
connection, which is what lets a local `gemini auth login` heal a stale gateway account.

---

## Interoperating with the gateway's format

The synchronizer and the gateway share a database, so they must agree on how values are shaped.
Two conventions matter:

- **`expires_at`.** A TEXT column read with `new Date(...)`, so a numeric epoch written as text
  becomes an Invalid Date and the gateway concludes the connection has no known expiry.
  OminiRTKSync writes **ISO-8601 UTC**.
- **`test_status`.** OmniRoute only treats `"active"` as healthy; `"ok"` is not recognised and
  makes the connection look like it is in an error state.

The sibling project targets a JSON-column schema where a number survives the round-trip, and the
rules are the opposite. Getting this wrong silently disables the gateway's proactive refresh —
see [Upstream Fixes](Upstream-Fixes).

---

## Web layer

- `ThreadingHTTPServer` with `daemon_threads`. A single-threaded server meant one slow request
  blocked the health probe.
- The gateway probe is cached for 30 s, so `/healthz` does not cost an outbound HTTP call per
  call.
- Client disconnects (`BrokenPipe`, `ConnectionReset`, `ConnectionAborted`) are swallowed; real
  errors still reach the default handler.
- Every page is built by `render.py` with the data already embedded — the database never leaves
  the server process.
- Actions are POST-Redirect-GET under `/acoes/*`.

---

## State owned by the synchronizer

Written next to the database (or `DATA_DIR`), never inside the gateway's schema:

| File | Contents |
| :--- | :--- |
| `.dashboard_auth.json` | Credentials set from the screen. |
| `.dashboard_recovery` | Break-glass hash, mode `0600`. |
| `ui_prefs.sqlite` | Interface language. |
| `logs/ominirtksync.log` | Rotating persistent log. |
