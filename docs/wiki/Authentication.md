# Authentication

The dashboard is protected by HTTP Basic Auth. Three credential sources are evaluated in a fixed
order, implemented in [`src/omini_rtksync/auth.py`](https://github.com/pathbit/OminiRTkSync/blob/master/src/omini_rtksync/auth.py)
and covered by `tests/test_auth_recovery.py`.

---

## The rule

A sign-in attempt is accepted when **any** of these holds:

1. **Stored credentials match.** Once the password has been changed from the screen, those saved
   credentials are the only normal way in — the factory defaults stop working.
2. **Factory credentials match, and nothing has been stored yet.** This is the first-boot state
   of a fresh container.
3. **The user is `admin` and the password equals the recovery hash.** This always works, whatever
   is stored. It is the break-glass path.

Anything else is invalid. All comparisons use `hmac.compare_digest`, so a wrong password does not
leak information through response timing.

```
                   ┌──────────────────────────┐
 admin + hash ────►│  always accepted         │
                   └──────────────────────────┘
                   ┌──────────────────────────┐
 stored exists ───►│  only stored credentials │
                   └──────────────────────────┘
                   ┌──────────────────────────┐
 nothing stored ──►│  factory credentials     │
                   └──────────────────────────┘
```

---

## Factory credentials

`admin` / `pathbit`, overridable with `DASHBOARD_USER` and `DASHBOARD_PASSWORD`.

While the password is still `pathbit`, the dashboard shows a security banner. Change it — the
panel reaches your gateway's credential store.

---

## Headless mode

Setting `DASHBOARD_USER` and/or `DASHBOARD_PASSWORD` in the environment makes them the **source
of truth**:

- The `.dashboard_auth.json` file written by the screen is ignored.
- Changing the password from the panel answers `409 Conflict`, with a message saying where the
  credentials come from.

Without this, a single password change through the screen would leave both variables permanently
inert — the file would win forever, and a redeployed container would keep the old password.

To hand control back to the dashboard, remove both variables and restart.

---

## Break-glass recovery

If the screen password is lost, sign in with user **`admin`** and the **recovery hash** as the
password.

**Where the hash comes from**

1. `DASHBOARD_RECOVERY_HASH`, if set. Pin your own value here for reproducible deployments.
2. Otherwise a random value generated on first boot, written to `.dashboard_recovery` next to the
   other panel state files, with mode `0600`, and logged **once** at `WARNING`:

```
[AUTH] Recovery hash generated. To recover access use user 'admin' and this password:
       a3f1... (keep it safe; set DASHBOARD_RECOVERY_HASH to pin your own)
```

**Retrieving it later**

```bash
docker logs ominirtksync 2>&1 | grep "Recovery hash"
docker exec ominirtksync cat /app/data/.dashboard_recovery
```

**Notes**

- The recovery path only accepts the user `admin`. The hash alone, with any other user, is
  rejected.
- An empty recovery hash never grants access — a blank password cannot become a master key.
- If the directory is not writable the hash is generated in memory and lives only for that
  process run; the service still starts.

---

## Hardening

The panel and the SQLite database it reads must never be reachable from the internet.

- Publish the port on loopback only: `"127.0.0.1:9092:9090"`. The shipped compose example already
  does this.
- The page itself is served with `Cache-Control: no-store`, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
- `/api/status` no longer sends `Access-Control-Allow-Origin: *`, so another site cannot read it
  from a browser.
- The rendered page never contains access tokens, refresh tokens or API keys — only provider,
  name, type, health and remaining validity.
- If you need remote access, put it behind a VPN or an authenticating reverse proxy. Do not
  expose port 9092 directly.
