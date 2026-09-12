# Upstream Fixes

Some of what this synchronizer works around are bugs in the gateways themselves. Where that is
the case, the fix belongs upstream — a workaround in a sidecar helps only the people running the
sidecar.

This page tracks what was found and what was sent.

---

## 9Router — numeric-epoch `expiresAt` silently disabled OAuth refresh

**Upstream PR:** [decolua/9router#3997](https://github.com/decolua/9router/pull/3997)

`parseTimeMs()` in `open-sse/services/oauthCredentialManager.js` accepted a `number` and anything
`Date` can parse — but a numeric epoch **in string form** fell through to the `Date` branch,
where it is an Invalid Date:

```js
new Date("1789012345678").getTime()  // NaN  -> parseTimeMs returns null
```

A `null` expiry disables **both** proactive refresh paths:

| Call site | Effect |
| :--- | :--- |
| `shouldRefreshCredentials()` | `expiresAtMs !== null` is false — the on-request refresh never fires. |
| `selectConnectionsNeedingRefresh()` | `if (expiresAtMs === null) continue;` — the background sweep skips the connection. |

The connection then keeps an expired access token and 401s until the user re-authenticates by
hand. Same user-visible symptom as upstream issue #2546 ("session dies 40-45 min after login"),
reached through a different input shape.

**Where the shape comes from:** the bulk-import routes persist the user-supplied value verbatim —
`grok-cli/bulk-import/route.js:77`, `codex/bulk-import/route.js:97`,
`kiro/import-cli-proxy/route.js:20` — while `lib/oauth/kiroExternalIdp.js` already normalizes to
ISO. The convention existed; the parser just did not accept what those routes could store.

**Fix:** `parseTimeMs()` converts numeric strings using the same seconds/ms heuristic it already
applied to numbers, and is exported so `normalizeExpiresAt()` reuses it — an epoch already stored
self-heals to ISO on the next refresh.

---

## OmniRoute — numeric epoch expiry broke the token health check

**Upstream PR:** [diegosouzapw/OmniRoute#13444](https://github.com/diegosouzapw/OmniRoute/pull/13444)

`provider_connections.expires_at` / `token_expires_at` are **TEXT** columns, so an epoch always
reads back as a string. `getEffectiveTokenExpiryMs()` went straight to `new Date()`:

```ts
new Date("1789012345678").getTime()  // NaN
new Date(1789012345).getTime()       // 1970-01-21 — seconds read as milliseconds
```

The sibling helper right below it, `getCopilotTokenExpiryMs()`, already handled both numeric
shapes. The main connection path never got the same treatment.

Two failure modes in `checkConnection()`:

1. **Numeric string → never refreshed.** `NaN` → `0` → `hasKnownExpiry` false → `isAboutToExpire`
   false. For a rotating provider (`codex`, `claude`, `kiro`, `openai`, …) `shouldRefreshByInterval`
   is also false, so `if (!isAboutToExpire && !shouldRefreshByInterval) return;` returns early on
   every sweep. The expiry-driven refresh the surrounding comment says it prefers is silently off.
2. **Epoch seconds as a number → a refresh loop.** Parsed as milliseconds it lands in 1970, so
   `isAboutToExpire` is permanently true and *every* sweep refreshes the connection — burning
   single-use refresh-token rotations.

**Fix:** extract the numeric/string handling into one exported `parseTokenExpiryMs()` and route
both call sites through it.

---

## What we fixed on our side

This project was itself writing `expires_at` as a numeric epoch in text (`str(expires_at_ms)`)
and `test_status = 'ok'` — a value OmniRoute does not recognise as healthy. Both are fixed; it now
writes ISO-8601 and `'active'`, the gateway's native formats.

That is the loop worth naming: the sidecar wrote a shape the gateway could not read, so the
gateway stopped refreshing, so the sidecar had to do all the refreshing. Fixing one side without
the other would have left it half-broken.

---

## A note on the README claim

An earlier version of the sibling project's README stated that 9Router writing `expiresAt` as an ISO
string "breaks internal numeric validations, producing false HTTP 503 errors".

Reading the upstream source does not support that. 9Router consistently parses `expiresAt` with
`new Date(...)`, which handles ISO correctly, and no 503 path is tied to credential expiry. The
real defect is the opposite shape — a numeric epoch the parser rejects — which is what
[#3997](https://github.com/decolua/9router/pull/3997) fixes.

The normalization these synchronizers perform is still useful: it is what keeps the stored value
in a shape every reader on both sides handles.
