# Troubleshooting

Concrete symptoms, what they actually mean, and what to do.

---

## "The cron ran several times and never renewed the Antigravity token"

**Usually not a bug.** A token is only renewed once its remaining validity drops below
`REFRESH_MARGIN` (default 900 s = 15 min). A connection showing *24 min* remaining is correctly
left alone — renewing early would burn refresh-token rotations for nothing.

The dashboard states this per connection, in the **Renewal diagnosis** column:

> Outside the 15 min margin: renewal expected in ~9 min

**When it *is* a problem:** the diagnosis column says something else.

| Diagnosis | Meaning | Action |
| :--- | :--- | :--- |
| `No expiry recorded` | The connection has no readable `expiresAt`. | It will be renewed on the next sweep; if it persists, check the gateway wrote the field. |
| `Token expired` | Renewal is due but has not succeeded. | Open **Logs** on the scheduler card — the failing cycle carries the provider error. |
| `Local instance did not answer the model catalog` | The local Ollama/vLLM is down. | Check the instance and its `baseUrl`. |

If you want renewal to happen sooner, raise the margin rather than shortening the interval:

```
REFRESH_MARGIN=1800     # renew during the last 30 minutes
```

---

## `BrokenPipeError: [Errno 32] Broken pipe` in `serve_healthz`

```
File "/app/src/omini_rtksync/web/server.py", line 116, in serve_healthz
    self.wfile.write(b"OK")
BrokenPipeError: [Errno 32] Broken pipe
```

**Fixed.** Root cause was two compounding problems:

1. The HTTP server was single-threaded despite the module promising multi-thread, so one slow
   request blocked everything else.
2. `/healthz` made an outbound HTTP call of up to 3 s to the gateway on **every** probe. The
   Docker health check (5 s timeout, every 15 s) gave up and closed the socket before the
   response body was written, and `socketserver` printed the whole traceback.

Now the server is a `ThreadingHTTPServer`, the gateway probe is cached for 30 s, and client
disconnects are swallowed instead of logged as failures. If you still see it, you are running an
image from before the fix — pull `ghcr.io/pathbit/ominirtksync:latest` again.

---

## The local Ollama shows up but without its models

The connection is classified as **Local** and probed on `/api/tags` and `/v1/models`. If the
model list is empty:

- The connection has no `baseUrl` — the gateway stores it on the provider record; check it in the
  OmniRoute UI.
- The container cannot reach the host instance. From inside the container, `localhost` is the
  container, not your machine. Use `host.docker.internal` (Docker Desktop) or the host's LAN IP.
- The instance requires an API key the connection does not carry.

A local instance that does not answer is marked `unreachable` and shows the **Unknown** badge —
deliberately, so a dead instance is not silently reported as healthy.

---

## The dashboard shows stale data

The page is rendered on the server and served with `Cache-Control: no-store`, so a reload always
re-reads the database. Use the **Refresh** button (it is a plain link to `/`).

If the numbers still look wrong, the synchronizer may not be writing at all — check
`GET /healthz`:

| Response | Meaning |
| :--- | :--- |
| `OK` | Database readable and gateway reachable. |
| `DATABASE_NOT_READY` | `DB_PATH` points at a file that does not exist. |
| `OMNIROUTE_SERVICE_UNREACHABLE` | `OMNIROUTE_URL` is wrong, or the gateway is down. |

---

## I forgot the dashboard password

Sign in with user `admin` and the **recovery hash** as the password. Find it with:

```bash
docker logs ominirtksync 2>&1 | grep "Recovery hash"
# or, if the log file is mounted:
grep "Recovery hash" /app/data/logs/ominirtksync.log
```

If the log has already rotated past it, the value is on disk:

```bash
docker exec ominirtksync cat /app/data/.dashboard_recovery
```

To pin your own instead of relying on the generated one, set `DASHBOARD_RECOVERY_HASH` and
restart. See [Authentication](Authentication).

---

## Changing the password from the panel answers `409 Conflict`

The service is in headless mode: `DASHBOARD_USER` and/or `DASHBOARD_PASSWORD` are set in the
environment, which makes them the source of truth. Change them in the environment and restart, or
comment both out to hand control back to the dashboard.

---

## Log files are not being written

The file log is best-effort — the synchronizer never refuses to start because of it. On startup
you will see:

```
[LOG] File log unavailable at /app/data/logs: [Errno 13] Permission denied
```

Fix the volume permissions, or point `LOG_DIR` somewhere writable. Events keep going to stdout
while `LOG_TO_STDOUT=1`.

---

## Both synchronizers fight over the same port

They listen on **9090 inside their own container** by design. Only the published host port
differs: `9091` for 9RTKSync, `9092` for OminiRTKSync. If you changed `WEB_PORT`, change it in
one container only — there is no reason for the internal ports to differ.

---

## Connections keep getting skipped by the gateway

Two separate causes, worth telling apart:

- **Stale rate-limit lock.** The synchronizer removes expired `rateLimitedUntil` and
  `modelLock_*` entries and resets `backoffLevel` on every sweep. Check the scheduler **Logs**
  for a `Rate limit lock removed` line.
- **A gateway-side parsing bug.** Both upstream gateways had a bug where a credential expiry in
  certain shapes silently disabled their own proactive refresh. See [Upstream Fixes](Upstream-Fixes).
