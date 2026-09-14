# Licensing and capacity: how many subscriptions for how many developers

*(Versão em português ao final.)*

The question always arrives as arithmetic. *We are twelve developers — how many
Max plans do we buy?* It sounds like a division, and it is. The trouble is that
**the divisor is not published by anybody**.

Not one of the consumer subscriptions involved here states the absolute capacity
of a single seat. What they publish is a relative multiplier and a reset window.
So a table saying "1 licence covers 4 developers" could only be produced by
inventing the number nobody discloses — and the invented number would be
repeated for years by people who had no way to check it.

This page does the three things that can honestly be done instead:

1. it gives the **formula**, with every variable named;
2. it fills in the **demand** side with numbers that were actually measured;
3. it gives the **command** that finds the missing divisor in *your* install —
   and, for this gateway, it shows where OmniRoute keeps a slot shaped exactly
   like it.

For the routing, credential and egress side of the same accounts, see
[Architecture](Architecture), [Authentication](Authentication) and
[Egress and Multi-Session](Egress-And-Multi-Session).

---

## What the vendors publish, and what they withhold

| Vendor | What is published | Absolute number? |
| :--- | :--- | :--- |
| Anthropic Pro/Max | "Your session-based usage limit will reset every five hours." · "Max 5x provides five times more usage per session than the Pro plan." · "Max 20x provides 20 times more usage per session than the Pro plan." · "Max plans also have a weekly usage limit that applies across all models." | **No.** Multiplier and window only. |
| Anthropic (limits page) | "Your usage is affected by several factors, including the length and complexity of your conversations, the features you use, which Claude model you're chatting with, and the effort level you've selected." | **No.** |
| OpenAI Codex | "Local messages and cloud chats share your plan's usage allowance. Weekly limits may also apply." · a "rolling five-hour period" · per-plan bands (Plus 10–100 / 25–200 / 250–2,000 messages depending on model) | **No** — the page itself says: "These estimates are not fixed message limits; check your usage dashboard for current limits and reset times." |
| Google Gemini API | "Rate limits depend on a variety of factors (such as your usage tier) and can be viewed in Google AI Studio." | **No.** Defers to the console. |
| Google Gemini Code Assist | Standard: **1,500** requests **per user per day** · Enterprise: **2,000** requests **per user per day** · **2** requests per second **per user** | **Yes — and per user.** |

`[FONTE: https://support.claude.com/en/articles/11049741-what-is-the-max-plan — lido em 2026-09-12]`
`[FONTE: https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work — lido em 2026-09-12]`
`[FONTE: https://learn.chatgpt.com/docs/pricing — lido em 2026-09-12]`
`[FONTE: https://ai.google.dev/gemini-api/docs/rate-limits — lido em 2026-09-12]`
`[FONTE: https://docs.cloud.google.com/gemini/docs/quotas — lido em 2026-09-12]`

The same Google page also lists "6000 requests per day for code generation and
completion" and "960 requests per day for chat and visualization in Cloud
Assist" **with no plan breakdown** — and the second is attributed to *Cloud*
Assist, not *Code* Assist. Quote them with that exact label or not at all.

**The Code Assist row is the instructive one.** The one time a vendor does print
a number, it comes stamped *per user*. There is no pot of 1,500 requests that
twelve developers share; there are twelve pots of 1,500. That is not a wording
detail — it is the shape of the whole answer, and the terms-of-use section below
arrives at the same shape from a completely different direction.

---

## The one place the number does exist: the API

`[FONTE: https://platform.claude.com/docs/en/api/rate-limits — lido em 2026-09-12]`

| Tier | RPM (Opus 5 / Sonnet 5) | Input tokens/min | Output tokens/min | Monthly spend cap |
| :--- | ---: | ---: | ---: | ---: |
| Start | 1,000 | 2,000,000 | 400,000 | US$ 500 |
| Build | 5,000 | 5,000,000 | 1,000,000 | US$ 1,000 |
| Scale | 10,000 | 10,000,000 | 2,000,000 | US$ 200,000 |

Two warnings from the same page matter to a team that switches on all at once:

> "New organizations and organizations with limited usage history may start in
> the **Evaluation tier, with limits below the standard limits** shown on this
> page."

> "You might also encounter 429 errors because of **acceleration limits** on the
> API if your organization has a sharp increase in usage."

Twelve developers onboarding on the same morning trip both. The tier computed
below is the steady-state tier, not the first-day tier.

And the detail that changes the arithmetic by an order of magnitude:

> "**For most Claude models, only uncached input tokens count toward your ITPM
> rate limits.**"

So `input_tokens` counts, `cache_creation_input_tokens` counts, and
`cache_read_input_tokens` does **not**. In the measured history below, **98.3% of
all tokens moved are cache reads**, and the ratio between median total input and
median counting input is **21.1×**. Sizing the API path by summing cache reads
buys roughly twenty times more capacity than the workload needs.

**Watch the scope of that rule.** It is stated on the **API** rate-limit page.
Nothing read here says the five-hour meter on a Pro/Max subscription ignores
cache reads. That is why the API table below uses uncached input and the
subscription table uses **total** tokens — and why what a subscription actually
counts stays an open measurement.

---

## The formula

| Symbol | Name | Unit | Where it comes from |
| :--- | :--- | :--- | :--- |
| `N` | developers on the team | people | headcount |
| `c` | concurrency factor | 0–1 | measure it — the fraction of `N` requesting at the same moment |
| `U_sim` | simultaneous active users | sessions | `U_sim = N × c` |
| `R_h` | requests per hour per active session | req/h | measured below |
| `T_in` | **uncached** input tokens per request | tokens | measured below — API path only |
| `T_tot` | **total** input tokens per request | tokens | measured below — subscription path |
| `T_out` | output tokens per request | tokens | measured below |
| `W_h` | quota reset window | hours | published: 5 h (Anthropic, Codex). The two-hour per-family reset observed on Antigravity is **a log observation**, not a published window `[FONTE: pathbit-ai-for-devs/0002_claude_gravity_utilizando_9router/article/ARTICLE.md:689]` |
| `F` | slack | dimensionless | an operations decision; 0.30 in every table here |
| `C_window` | capacity of **one** licence inside `W_h` | tokens or requests | **not published** — measure it, see below |

```
U_sim   = N × c

# API path — the ceiling ignores cache reads
D_api   = U_sim × R_h × T_in × W_h × (1 + F)

# subscription path — what the meter counts is unknown, so use the total
D_sub   = U_sim × R_h × (T_tot + T_out) × W_h × (1 + F)

L       = max( ceil(D / C_window) , L_burst )
```

Burst is checked separately, because a per-window quota and a per-minute ceiling
are different ceilings and the second one blows first:

```
RPM_required   = U_sim × R_h / 60 × (1 + F)
input_per_min  = RPM_required × T_in
output_per_min = RPM_required × T_out
L_burst        = max over the three of ceil(required / licence ceiling)
```

**The unit of `D` and the unit of `C_window` have to match.** If you calibrated
`C_window` against a subscription's usage bar, it came out in total tokens, so
`D` has to be `D_sub`. Mixing the two is the most likely silent error in this
whole method.

---

## Measured demand

**This block is a snapshot, not a constant.** Claude Code history is a living
corpus: it grows with every session, so the same script run tomorrow on the same
machine returns different numbers, and neither run is wrong. What makes a
published number checkable is the cut — session files are append-only, so
everything before a past instant stops moving. Hence `--until`, and hence the
full command:

```bash
./.venv/bin/python tools/measure_agent_usage.py --until 2026-09-13T00:00:00Z
```

`[FONTE: saída do comando acima, histórico de UMA máquina, corte em 2026-09-13T00:00:00Z]`

```
historico lido                             : ~/.claude/projects/**/*.jsonl
corte (--until)                            : 2026-09-13T00:00:00Z
sessoes analisadas                         : 114
turnos unicos (dedup por message.id)        : 6648
T_in  entrada que conta p/ ITPM  mediana    : 4178
T_in  entrada que conta p/ ITPM  p90        : 8227
T_out saida                      mediana    : 706
T_out saida                      p90        : 1361
T_cache leitura de cache         mediana    : 83413
T_tot entrada total (conta+cache) mediana   : 88353
T_tot entrada total (conta+cache) p90       : 303214
R_h   requisicoes por hora ativa mediana    : 194
R_h   requisicoes por hora ativa p90        : 343
fracao de leitura de cache no total         : 98.3%
razao entrada total / entrada que conta     : 21.1x
pico de sessoes simultaneas                 : 13
linhas de resposta (com usage)              : 34305
message.id distintos                        : 15905
inflacao de contar linha e nao resposta     : 2.16x
```

Run it **without** `--until` and you get today's history instead — larger, and
the right thing to use when you are sizing your own team. Run it with the cut
above **on the machine measured** and you get exactly the block printed here,
twice in a row. On your machine you get your own numbers, which is the point.

Four caveats have to travel with those numbers, always:

1. **Deduplication is not optional.** One API response is written to several
   `type: "assistant"` lines — the text and each tool block — repeating the same
   `message.id` and the same `usage` object. Counting lines inflates everything
   by **2.16×** — 34,305 response lines against 15,905 distinct ids, the last
   three lines of the output above. That factor is no longer a claim in prose:
   the script counts it and prints it. Any consumption figure derived from Claude
   Code history without deduplicating by `message.id` is wrong by roughly two.
2. **21.1× is a ratio of two medians**, not the median of the ratios. It is good
   for an order of magnitude, not for accounting.
3. **The machine measured runs orchestration with subagents.** The peak of 13
   simultaneous sessions is one operator's parallelism, not a team's
   concurrency. Treat `R_h ≈ 194 req/h` as an **agent session** — one request
   every ~19 s — not as "a developer typing". Interactive CLI use without
   subagents measures lower. Run the script on your own machine.
4. **It is one machine, one operator, one working style.** The cut makes the
   number *auditable*; it does not make it *general*. Nothing here says your
   history looks like this one — which is why every table below is a worked
   example of the method, not a lookup table.

---

## Sizing: subscription path

This is the path OmniRoute is built for — it multiplexes **subscription
credentials**, one connection per account. Unit: **total tokens**, cache reads
included, per the rule above. `W_h = 5 h` is published; the other two inputs are
not measurements and are not dressed as such:

> **`c = 0.6` and slack 30% are arbitrated.** Nobody measured them here. `c` is
> the fraction of the team requesting at the same instant, and the formula table
> above says plainly that it has to be measured — on your own team, by counting
> concurrent sessions, not by reading this page. It is printed at the top of
> `tools/sizing.py`'s output under the label `arbitrado` precisely so it never
> gets quoted as a finding. Section "`c` moves the answer more than `N` does"
> below is the reason to care: this is the single input that most deserves your
> own measurement.

`[FONTE: `./.venv/bin/python tools/sizing.py`, com as entradas medidas acima e c/folga arbitrados]`

| Profile | Developers | `U_sim` | Demand in one 5 h window | Licences |
| :--- | ---: | ---: | ---: | :--- |
| median | 3 | 1.8 | 202,146,118 tokens | `ceil(D / C_window)` |
| median | 12 | 7.2 | 808,584,473 tokens | `ceil(D / C_window)` |
| median | 40 | 24.0 | 2,695,281,576 tokens | `ceil(D / C_window)` |
| p90 | 3 | 1.8 | 1,222,289,932 tokens | `ceil(D / C_window)` |
| p90 | 12 | 7.2 | 4,889,159,730 tokens | `ceil(D / C_window)` |
| p90 | 40 | 24.0 | 16,297,199,100 tokens | `ceil(D / C_window)` |

The right column stays symbolic **because no vendor publishes `C_window`**. Fill
it in with your own measurement and the division closes in one line. This is
exactly where a method differs from an invented table: the method says what is
missing, in which unit, and how to obtain it.

## Sizing: API-key path

OmniRoute also holds plain API keys — four of the five connections in the
install inspected here are keys, not OAuth accounts (the query and its output are
both in the quota-subsystem section below). For those the ceiling **is**
published, so the table
resolves. Unit: **uncached input tokens**. Slack 30%, `c` arbitrated as above and
varied on purpose to show how much it moves.

| Profile | Developers | `c` | `U_sim` | RPM | Input/min | Output/min | Minimum tier |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| median | 3 | 0.6 | 1.8 | 8 | 31,611 | 5,342 | Start |
| median | 12 | 0.6 | 7.2 | 30 | 126,443 | 21,366 | Start |
| median | 12 | 1.0 | 12.0 | 50 | 210,738 | 35,611 | Start |
| median | 40 | 0.4 | 16.0 | 67 | 280,984 | 47,481 | Start |
| median | 40 | 0.6 | 24.0 | 101 | 421,477 | 71,221 | Start |
| median | 40 | 1.0 | 40.0 | 168 | 702,461 | 118,702 | Start |
| p90 | 3 | 0.6 | 1.8 | 13 | 110,053 | 18,206 | Start |
| p90 | 12 | 0.6 | 7.2 | 54 | 440,210 | 72,824 | Start |
| p90 | 40 | 0.6 | 24.0 | 178 | 1,467,368 | 242,748 | Start |
| p90 | 40 | 1.0 | 40.0 | 297 | 2,445,613 | 404,580 | **Build** |

Three things to read out of it:

- **A forty-person team on the median profile still fits inside the Start tier.**
  Only the extreme corner — forty developers, all concurrent, on the p90 profile
  — needs Build, and what pushes it there is input tokens per minute (2.45 M
  against 2.00 M), not requests per minute.
- **The burst ceiling is rarely what hurts.** Against Start's 1,000 RPM, three
  developers at `c = 0.6` need 8 RPM (132× slack), twelve need 30 (33×), forty
  need 101 (9×).
- **`c` moves the answer more than `N` does.** Forty developers at `c = 0.4` and
  twelve at `c = 1.0` land in the same tier. Measuring concurrency is worth more
  than counting chairs.

---

## What this synchronizer shows you about it

The capacity question reaches the panel through exactly one chain, and it is
worth naming each link, because the field names differ at every step.

**The saturation signal.** OmniRoute stores the hold as a column,
`rate_limited_until TEXT` on `provider_connections` — or as `rateLimitedUntil`
inside the JSON `data` column on installs migrated from the single-column schema.
`get_all_connections` projects both onto one key, `rateLimitedUntil`
(`src/omini_rtksync/gateway.py:193`). `ConnectionRecord.rate_limit_active`
reads it as **a deadline, not a flag** (`src/omini_rtksync/models.py:157-169`) — it
holds the instant the provider's window reopens, so treating the field's mere
presence as "limited" left a connection yellow forever after its first 429.
When the deadline is still in the future, `health_status` returns
`rate_limited`, which the panel paints as the **Rate limited** badge in the
health column of the connections table (`src/omini_rtksync/i18n.py:92`,
`src/omini_rtksync/render.py:36`). See [Dashboard](Dashboard) for where that
column sits.

**The counter that closes the loop.** When the deadline passes, the sync clears
the hold and records the action as `Trava de rate limit vencida removida`
(`src/omini_rtksync/cli.py:143-149`). Those entries accumulate in the scheduler
**Logs** modal, one per cycle. Counting them per account per day for a week is
the only evidence that actually settles whether `L` was right: an account that
gets held every day is undersized; an account that never gets held is slack you
can put more people on. Everything before this section is projection.

**The headcount.** The metric cards carry **OAuth accounts** and **API keys**
(`src/omini_rtksync/i18n.py:35-36`). The first is the `L` of the terms-of-use
section below — accounts with their own account holder. The second is the
API-key path, which is sized by tier rather than by seat.

**The cheapest lever.** **Registered combos** and the **Resilience combos**
section list each combo and its model cascade, read from OmniRoute's `combos`
table as `name`, `kind` and `models`
(`src/omini_rtksync/gateway.py:449-473`). This matters more than it looks:
quota can be exhausted **per model family** while the account itself stays
healthy. During an Antigravity block, every Gemini-family model returned 503
while Claude and the open-weight models on the same account kept answering
`[FONTE: pathbit-ai-for-devs/0002_claude_gravity_utilizando_9router/article/ARTICLE.md:684-720]`.
A fallback combo that crosses families therefore multiplies effective capacity
**without buying a licence** — and it is the only lever here that does not run
into the terms-of-use limit below. The install inspected had **0 combos**
registered — the count command and its full output are in the quota-subsystem
section below.

**Automation.** `/api/status` returns `connectionsCount` and `combosCount`
(`src/omini_rtksync/web.py:401-412`), and the command line prints the same
inventory:

```bash
ominirtksync --status --db-path ~/.omniroute/data/storage.sqlite
```

The product tries five paths, in this order, and takes the first that exists:
`/app/data/storage.sqlite`, `/app/data/data.sqlite`,
`~/.omniroute/data/storage.sqlite`, `~/.omniroute/storage.sqlite`,
`~/.omniroute/data.sqlite`
`[FONTE: src/omini_rtksync/config.py:221-227]`. So the `data/` segment is not
required — it simply comes first. Write the path your install actually has; on
a container install that is the first, on a local one usually the third.

**What the panel does not show, and will not pretend to:** token counts, a
percentage of quota consumed, the remaining window, or `max_concurrent`. The
synchronizer reads credential health; it is not a metering product.

### One gap worth naming

`provider_connections` also carries `backoff_level INTEGER DEFAULT 0`
`[FONTE: schema lido de diegosouzapw/omniroute:latest em execução, 2026-09-12]`.
The sync clears `rate_limited_until` when the deadline passes
(`src/omini_rtksync/gateway.py:376-378`) but **never resets `backoff_level`** —
there is no occurrence of the string anywhere under `src/`. The sibling project
9RTKSync does zero its equivalent when it clears the hold
`[FONTE: 9RTKSync/src/nine_rtksync/normalizer.py:87 — `data["backoffLevel"] = 0`]`.
Whether OmniRoute decays the level on its own is `[A VERIFICAR: leia o
tratamento de backoff_level no fonte do gateway]`. It is recorded here because a
stale backoff level would make an account look more saturated than it is, which
is precisely the kind of error this page exists to avoid.

---

## The slot shaped like `C_window` — present, and partly filled

This is the genuinely interesting find about OmniRoute, and it needs to be
stated without overclaiming.

The shipped schema contains a full quota subsystem. Schema, unlike a row count,
is a durable property — it comes from the image, not from the traffic — and it is
one command away `[FONTE: `sqlite3 /tmp/omniroute.sqlite "SELECT sql FROM
sqlite_master WHERE name='provider_quota_state';"` contra
`diegosouzapw/omniroute:latest`, 2026-09-13]`:

```sql
CREATE TABLE provider_quota_state (
  connection_id TEXT NOT NULL,
  model TEXT NOT NULL,
  tokens_used INTEGER NOT NULL DEFAULT 0,
  token_limit INTEGER NOT NULL DEFAULT 0,
  window_start INTEGER NOT NULL,
  window_reset INTEGER NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (connection_id, model)
);
```

`token_limit` per connection *per model*, with the window boundaries next to it,
is `C_window` in database form — and at the granularity the Antigravity
observation says it needs, which is per model family rather than per account.
Alongside it sit `quota_snapshots` (`window_key`, `remaining_percentage`,
`is_exhausted`, `next_reset_at`, `window_duration_ms`, `raw_data`),
`provider_plans` (`dimensions_json`, `source` constrained to `auto` or `manual`),
and `quota_pools` with `quota_pool_connections`. Note `window_key`: it is
`quota_snapshots`' own per-model dimension, and it matters below.

**Counting them takes two commands, and the second one is the one that is easy
to get wrong.** The container ships no `sqlite3`, and the database runs in WAL
mode — so a `docker cp` of `storage.sqlite` alone reads a stale file and
undercounts whatever is still in the write-ahead log. Measured on one single
copy, read twice: **188 `call_logs` without the `-wal` beside it, 209 with it**.
Twenty-one rows, 10% of the table, invisible to the shorter command — and
silently, since nothing errors. Copy the `-wal` with it:

```bash
docker cp ominirtk-router:/app/data/storage.sqlite     /tmp/omniroute.sqlite
docker cp ominirtk-router:/app/data/storage.sqlite-wal /tmp/omniroute.sqlite-wal

sqlite3 -header -column /tmp/omniroute.sqlite "
SELECT 'provider_quota_state' AS tabela, count(*) AS linhas FROM provider_quota_state
UNION ALL SELECT 'quota_snapshots',      count(*) FROM quota_snapshots
UNION ALL SELECT 'provider_plans',       count(*) FROM provider_plans
UNION ALL SELECT 'quota_pools',          count(*) FROM quota_pools
UNION ALL SELECT 'daily_usage_summary',  count(*) FROM daily_usage_summary
UNION ALL SELECT 'usage_history',        count(*) FROM usage_history
UNION ALL SELECT 'call_logs',            count(*) FROM call_logs
UNION ALL SELECT 'combos',               count(*) FROM combos
UNION ALL SELECT 'provider_connections', count(*) FROM provider_connections;"
```

`[FONTE: saída do comando acima contra `diegosouzapw/omniroute:latest` em execução, instante 2026-09-13T02:20:53Z]`

```
tabela                linhas
--------------------  ------
provider_quota_state  0
quota_snapshots       48
provider_plans        0
quota_pools           0
daily_usage_summary   0
usage_history         8
call_logs             209
combos                0
provider_connections  5
```

**Read that table as an instant, not as a property.** `call_logs` and
`quota_snapshots` climb as traffic goes through: on this same container,
Run the command twice, some minutes apart, and the two counts differ — that is
the point, and it is the only evidence anybody needs here. Earlier readings of
this same install are not reproducible by you and so are not quoted. The zeros are the durable part — and the 48 is the
interesting part, because an earlier reading of this same install found
`quota_snapshots` at 0 and the page concluded, wrongly, that nothing ever fills
it. What actually happened is that the first snapshot was written at
23:29:23.587Z, *after* the last `usage_history` row at 22:54Z. The measurement was
right; the conclusion drawn from it was not.

So the honest finding is the opposite of "empty":

```bash
sqlite3 -header -column /tmp/omniroute.sqlite "
SELECT c.provider, c.auth_type, count(s.id) AS snapshots,
       count(DISTINCT s.window_key) AS modelos,
       min(s.created_at) AS primeiro, max(s.created_at) AS ultimo
FROM provider_connections c LEFT JOIN quota_snapshots s ON s.connection_id = c.id
GROUP BY c.id ORDER BY snapshots DESC;"
```

```
provider     auth_type  snapshots  modelos  primeiro                  ultimo
-----------  ---------  ---------  -------  ------------------------  ------------------------
antigravity  oauth      48         16       2026-09-12T23:29:23.587Z  2026-09-13T01:55:03.806Z
openrouter   apikey     0          0
mistral      apikey     0          0
groq         apikey     0          0
gemini       apikey     0          0
```

**OmniRoute fills `quota_snapshots` by itself, per window key, on the OAuth
connection — and only there.** Sixteen distinct `window_key` values on one
account: model names (`claude-sonnet-4-6`, `gemini-3.1-pro-high`,
`gpt-oss-120b-medium`) next to aggregate keys (`claude_gpt_weekly`), plus two
(`chat_20706`, `chat_23310`) that look like per-conversation counters and fit
neither description. The four
API-key connections have none. That is the per-model dimension, populated,
without anybody declaring a plan in `provider_plans` first.

**And it still is not `C_window`.** Every one of the 48 rows reads
`remaining_percentage = 100.0`, `is_exhausted = 0`, with `window_duration_ms` and
`raw_data` NULL throughout — an account nobody has worked hard enough to dent.
More important than the emptiness of the numbers is their *unit*: a percentage is
`1 − p`, the consumed fraction. It is the left-hand side of the calibration
procedure at the end of this page, not its answer. To get `C_window` in tokens
you still have to pair that percentage with the tokens you actually moved in the
same window. What OmniRoute gives you for free is the `p` — read by machine, per
model, instead of eyeballed off a progress bar. That is a real shortcut, and it
is worth exactly that much.

`provider_quota_state` — the table that carries `token_limit`, the absolute
number — is still at 0. Whether OmniRoute ever writes it, and from which upstream
response, is `[A VERIFICAR: leia no fonte do gateway quem escreve em
provider_quota_state, e se depende de um plano declarado em provider_plans]`.
This synchronizer reads none of these tables: `git grep -n
'quota_snapshots\|provider_quota_state' -- src/` returns nothing.

The same reservation applies to the per-connection knobs
`max_concurrent INTEGER` and `rate_limit_protection INTEGER DEFAULT 0`, which
are where `c` would be materialised per account:

```bash
sqlite3 -header -column /tmp/omniroute.sqlite "
SELECT provider, auth_type, max_concurrent, rate_limit_protection, backoff_level,
       expires_in
FROM provider_connections;"
```

```
provider     auth_type  max_concurrent  rate_limit_protection  backoff_level  expires_in
-----------  ---------  --------------  ---------------------  -------------  ----------
antigravity  oauth                      0                      0              3599
groq         apikey                     0                      0
openrouter   apikey                     0                      0
gemini       apikey                     0                      0
mistral      apikey                     0                      0
```

`[FONTE: saída do comando acima, mesmo instante 2026-09-13T02:20:53Z]` — one OAuth
account against four API keys, `max_concurrent` NULL on all five,
`rate_limit_protection` 0 on all five. The knob exists and nobody turned it.

A note on granularity, since the sibling documentation reads differently: 9Router
keeps per-family holds as `modelLock_*` keys inside the connection JSON. **There
is no `modelLock_*` in OmniRoute.** Here the *hold* is one deadline for the whole
connection (`rate_limited_until`) — but do not conclude from that, as an earlier
version of this page did, that per-model granularity is absent. It is not: it
lives in `quota_snapshots.window_key`, which is populated, and it would live in
`provider_quota_state.model`, which is not. Two different tables, two different
answers, and only one of them empty. Do not port the sibling's paragraph across,
and do not port this one back.

---

## Measure demand here, not only on the developer's laptop

The script above reads Claude Code history on one machine. OmniRoute has
something better for a team, because it is per account: `usage_history` records
every call that went through the gateway with the exact token split the formula
needs `[FONTE: schema lido de diegosouzapw/omniroute:latest em execução,
2026-09-12]`.

```bash
# Instalação local; num deploy em contêiner, copie primeiro como na seção acima
# (com o `-wal`, ou a contagem sai menor do que é).
sqlite3 -header -column ~/.omniroute/data/storage.sqlite "
SELECT provider,
       count(*)                                  AS requisicoes,
       sum(tokens_input + tokens_cache_creation) AS entrada_que_conta,
       sum(tokens_cache_read)                    AS leitura_de_cache,
       sum(tokens_output)                        AS saida,
       min(timestamp)                            AS de,
       max(timestamp)                            AS ate
FROM usage_history GROUP BY provider ORDER BY requisicoes DESC;"
```

Run against the live database, it answers:

```
provider    requisicoes  entrada_que_conta  leitura_de_cache  saida  de                        ate
----------  -----------  -----------------  ----------------  -----  ------------------------  ------------------------
gemini      4            18                 0                 237    2026-09-12T22:47:04.218Z  2026-09-12T22:54:42.006Z
mistral     2            18                 0                 4      2026-09-12T22:47:03.788Z  2026-09-12T22:54:39.507Z
groq        1            18                 0                 2      2026-09-12T22:47:42.194Z  2026-09-12T22:47:42.194Z
openrouter  1            36                 0                 12     2026-09-12T22:48:00.963Z  2026-09-12T22:48:00.963Z
```

**That is eight rows from smoke tests, not a workload.** The query shape is
proven; the volume proves nothing. Mapped onto the formula:
`T_in = tokens_input + tokens_cache_creation`, `T_tot = T_in + tokens_cache_read`,
`T_out = tokens_output`, and `R_h` comes from grouping by
`strftime('%Y-%m-%dT%H', timestamp)` together with `connection_id`.

Two limits on this source. It only sees traffic that went **through** the
gateway — a developer pointing Claude Code straight at their own subscription is
invisible to it. And `usage_history` competes with `call_logs` for the same
facts; `call_logs` carries the per-request row with `connection_id`, `status`
and the same token columns, which is the better source when you need to separate
successes from 429s.

---

## The limit that is not capacity

Everything above sizes **technical capacity**. None of it authorises sharing a
subscription, and the vendor text is explicit
`[FONTE: https://code.claude.com/docs/en/legal-and-compliance — lido em 2026-09-12]`:

> "Advertised usage limits for Pro and Max plans assume **ordinary, individual
> usage** of Claude Code and the Agent SDK."

> "**OAuth authentication is intended exclusively for purchasers** of Claude
> Free, Pro, Max, Team, and Enterprise subscription plans and is designed to
> support ordinary use of Claude Code and other native Anthropic applications."

> "Anthropic does not permit third-party developers to offer Claude.ai login
> into their own applications, or to **route requests through Free, Pro, or Max
> plan credentials on behalf of their users**."

> "**Customers may not pay for, resell, or intermediate Claude usage on their
> end users' behalf.** Each end user must authenticate with their own Anthropic
> API key, Claude subscription plan credentials, or 3P inference provider
> credential."

That closes the original question with an answer that **does not depend on
measuring anything**:

> **For Claude Pro/Max, `L = N`.** Twelve developers, twelve subscriptions, each
> bought and authenticated by its own holder. The gateway does not reduce that
> number. It exists for routing, cross-family fallback, credential renewal and
> observability over accounts that are **already individual** — and that is
> exactly where it pays for itself.

The API-key sizing applies in full to the **API path** — an organisation key,
billed to the organisation, distributed internally. The same document allows it
in as many words: *"This does not restrict how customers provision and manage
their own API keys … for use by the customer's own authorized users."*

For **Google AI Pro / Antigravity** and for **OpenAI** plans, the equivalent
terms were not read. `[A VERIFICAR: leia os termos de assinatura de cada um e
cite URL + data, como foi feito acima com a Anthropic]`. Do not assume symmetry
between vendors.

One operational consequence, since it is the natural shape of any gateway with
every account registered: several sessions on one account draw no attention, but
several accounts leaving through one address do. OmniRoute models the fix as a
per-account egress binding, and a pool that is inactive or missing its address
fails silently back to the host's — the whole of
[Egress and Multi-Session](Egress-And-Multi-Session) is about that, and
[Egress Testing](Egress-Testing) is the bench that proves where traffic really
leaves from.

---

## Credential renewal is not quota

Two different clocks. Confusing them produces the wrong diagnosis, and this
repository exists partly because they were confused.

| | Credential validity | Quota |
| :--- | :--- | :--- |
| Duration | 3,599 s ≈ 1 h, read off the connection | 5 h / weekly; 2 h per family, observed |
| Symptom | 401, "spontaneous" disconnection | 429 / 503 |
| Field | `expires_at` | `rate_limited_until` |
| Who fixes it | automatic renewal — what this synchronizer does | wait for the window, or buy a seat |
| Scales with the team? | **No** | **Yes** |

The hour in the first column is not folklore: the gateway stores the lifetime the
provider handed it, and on the OAuth connection here it reads
`expires_in = 3599` — the query is in the quota-subsystem section, same output.
`[FONTE: `SELECT provider, auth_type, expires_in FROM provider_connections WHERE auth_type='oauth'`, 2026-09-13T02:20:53Z]`
That is **one** connection on **one** provider, so read it as "this credential
lasts an hour", not as "OAuth tokens last an hour".

The "it logs itself out after an hour" complaint is a **storage format** problem,
not a shortage of quota: `expires_at` is a TEXT column that OmniRoute reads with
`new Date(...)`, so a numeric epoch written as text becomes an invalid date, the
gateway concludes the connection has no known expiry, and proactive renewal
stops for that connection. The synchronizer writes ISO-8601 there, the gateway's
own native format (`src/omini_rtksync/gateway.py:209-243`). **Neither clock
belongs in the capacity formula.** [Upstream Fixes](Upstream-Fixes) has the
gateway-side story of that parsing bug.

---

## Reproducing everything on this page

Both scripts are versioned here, because a number without a reproducible script
becomes, given enough time, an invented number. Check the claim before you trust
the page: `git ls-files tools/` has to list both of them.

| Script | What it produces |
| :--- | :--- |
| `tools/measure_agent_usage.py` | The measured demand profile. Reads **only** the numeric `usage` fields, `message.id` and `timestamp` from Claude Code history — no conversation content is read, aggregated or printed. Deduplicates by `message.id`, and prints the inflation factor that deduplication removes. `--until ISO` freezes the corpus at a past instant, which is what makes a published number checkable. |
| `tools/sizing.py` | The two sizing tables and the burst slack. Its first four lines of output label every input as `medido`, `publicado` or `arbitrado`, with the exact measurement command — the chain from history to table is meant to be walked backwards. Replace the constants with your own and recalculate. |

The numbers on this page come from three different places and the difference is
the whole point:

| Class | Where it comes from | What it is worth |
| :--- | :--- | :--- |
| Published | a vendor page, with URL and date | quote it, do not average it |
| Measured | a script in this repository, with its command | reproducible — on **that** corpus, at **that** cut |
| Arbitrated | an operations choice (`c`, slack) | a worked example; measure your own |

Anything that fits none of the three does not belong on the page.

To find `C_window` for a subscription, the only place it surfaces is
**Settings → Usage**, which shows the progress bars for the five-hour and weekly
windows `[FONTE: https://support.claude.com/en/articles/11049741-what-is-the-max-plan — lido em 2026-09-12]`:

1. Wait for the window to reset and note the time.
2. Work a typical stretch inside the window.
3. Read the consumed fraction `p` from the bar, and run the script restricted to
   that period, summing **total** tokens moved — call it `D_measured`. On an
   OAuth connection registered in OmniRoute, `quota_snapshots.remaining_percentage`
   gives you `1 − p` per window key without the eyeballing — see the section
   above for what it does and does not tell you.
4. `C_window ≈ D_measured / p`, in total tokens.

It is a measurement with a stated procedure, not a guess. And it holds for *that*
plan, *that* model and *that* effort level — the vendor's own page says all four
factors move the result.

Finally, when inspecting a stack's configuration to confirm what was injected,
always run `docker compose --no-interpolate config`. Without the flag the command
prints the secrets from the environment straight to your terminal.

---

# Em português

A pergunta chega sempre como uma conta de dividir: *somos doze, quantos planos
Max?* O problema é que **o divisor não é publicado por ninguém**. Nenhuma das
assinaturas de consumo envolvidas declara a capacidade absoluta de um assento —
o que existe é multiplicador relativo e janela de reset. Uma tabela dizendo "1
licença atende 4 devs" só poderia sair de um número inventado, e o número
inventado seria repetido por anos por quem não tem como conferir.

Então esta página entrega três coisas: a **fórmula**, a **demanda** medida de
verdade, e o **comando** que acha o divisor que falta no seu ambiente.

## O que os fornecedores publicam

Nada de absoluto, com uma exceção. A Anthropic publica janela de 5 h e
multiplicador (`Max 5x`, `Max 20x`) mais um limite semanal; a página de limites
diz que tamanho da conversa, recursos usados, modelo e nível de esforço afetam o
consumo. O Codex publica janela de cinco horas rolante e faixas por plano, e a
própria página avisa que não são limites fixos. O Gemini API remete ao painel.

A exceção é o **Gemini Code Assist**: Standard **1.500** requisições **por
usuário por dia**, Enterprise **2.000**, e **2** requisições por segundo **por
usuário**. Quando o fornecedor finalmente imprime um número, ele vem carimbado
*por usuário* — não existe um pote de 1.500 que doze devs dividem, existem doze
potes de 1.500. Essa é a forma da resposta inteira.

Fontes, todas lidas em 2026-09-12: a página do plano Max e a de limites da
Anthropic, a de preços do Codex, a de rate limits do Gemini API e a de quotas do
Gemini Code Assist, listadas com URL na seção em inglês.

## Onde o número existe: a API

A tabela de tiers da API da Anthropic é publicada `[FONTE:
https://platform.claude.com/docs/en/api/rate-limits — lido em 2026-09-12]`:
Start 1.000 RPM / 2 M entrada por minuto / 400 mil saída por minuto / US$ 500 de
teto mensal; Build 5.000 / 5 M / 1 M / US$ 1.000; Scale 10.000 / 10 M / 2 M /
US$ 200.000. A mesma página avisa que organizações novas podem começar num tier
de avaliação **abaixo** desses limites, e que um salto brusco de uso dispara
*acceleration limits* — doze devs entrando no mesmo dia acionam os dois.

E o detalhe que muda a conta: **só a entrada não-cacheada conta para o limite de
tokens por minuto da API**. No histórico medido, **98,3% de todos os tokens
trafegados são leitura de cache**, e a razão entre a mediana da entrada total e a
da entrada que conta é **21,1×**. Quem dimensiona o caminho de API somando cache
lido compra vinte vezes mais do que precisa. **Cuidado com o escopo**: essa
regra é da página da API. Nada do que foi lido diz que o medidor de 5 h de uma
assinatura ignora leitura de cache — por isso a tabela de assinatura usa **token
total**.

## A fórmula

`U_sim = N × c`, e a demanda dentro da janela é
`D = U_sim × R_h × tokens_por_requisição × W_h × (1 + F)` — com entrada
não-cacheada no caminho de API e token total no caminho de assinatura.
`L = max(ceil(D / C_janela), L_rajada)`, com a rajada conferida à parte, porque
quota por janela e limite por minuto são tetos diferentes e o segundo estoura
primeiro. **A unidade de `D` e a de `C_janela` têm de ser a mesma** — misturar as
duas é o erro silencioso mais provável aqui.

## Demanda medida

**Isto é um instantâneo, não uma constante.** O histórico do Claude Code é um
corpus vivo: cresce a cada sessão, e o mesmo script amanhã na mesma máquina dá
outro número sem que nenhum dos dois esteja errado. O que torna um número
publicado conferível é o corte — os arquivos de sessão são append-only, então
tudo que está antes de um instante passado parou de se mexer. Daí o `--until`:

```bash
./.venv/bin/python tools/measure_agent_usage.py --until 2026-09-13T00:00:00Z
```

`[FONTE: saída do comando acima, histórico de UMA máquina, corte em 2026-09-13T00:00:00Z]`
— 114 sessões, 6.648 turnos únicos: `T_in` mediana 4.178 e p90 8.227; `T_out`
mediana 706 e p90 1.361; `T_tot` mediana 88.353 e p90 303.214; `R_h` mediana 194
req/h e p90 343; pico de 13 sessões simultâneas. A saída completa, com os nomes
de campo do script, está na seção em inglês. Sem `--until` o script lê o
histórico de hoje — que é o que você quer ao dimensionar o seu próprio time.

Quatro ressalvas viajam junto, sempre:

1. **A deduplicação não é opcional.** Uma resposta da API é gravada em várias
   linhas `type: "assistant"`, repetindo o mesmo `message.id` e o mesmo `usage`.
   Contar linhas infla tudo em **2,16×** — 34.305 linhas de resposta contra
   15.905 ids distintos. Isso deixou de ser afirmação em prosa: o próprio script
   conta e imprime o fator nas últimas três linhas da saída.
2. **21,1× é razão entre duas medianas**, não a mediana das razões — serve para
   ordem de grandeza, não para contabilidade.
3. **A máquina medida roda orquestração com subagentes.** O pico de 13 sessões é
   paralelismo de um operador só. Trate `R_h ≈ 194 req/h` como **sessão de
   agente**, uma requisição a cada ~19 s, não como "um dev digitando". Rode o
   script no seu ambiente.
4. **É uma máquina, um operador, um jeito de trabalhar.** O corte torna o número
   *auditável*, não *geral*. As tabelas abaixo são exemplo resolvido do método,
   não tabela de consulta.

## Dimensionamento

`[FONTE: `./.venv/bin/python tools/sizing.py`, com as entradas medidas acima]` —
`W_h = 5 h` é publicado. Os outros dois parâmetros, não:

> **`c = 0,6` e folga de 30% são arbitrados.** Ninguém mediu isso aqui. `c` é a
> fração do time pedindo no mesmo instante, e a tabela da fórmula acima já diz
> que ele tem de ser medido — no seu time, contando sessões simultâneas, não
> lendo esta página. O `tools/sizing.py` imprime esse valor sob o rótulo
> `arbitrado` nas primeiras linhas da saída justamente para que ele nunca seja
> citado como achado. É o parâmetro que mais move o resultado: medir
> concorrência vale mais do que qualquer tabela desta página.

**Caminho de assinatura**, em token total, que é para o que o OmniRoute existe:

| Perfil | Devs | `U_sim` | Demanda numa janela de 5 h | Licenças |
| :--- | ---: | ---: | ---: | :--- |
| mediana | 3 | 1,8 | 202.146.118 tokens | `ceil(D / C_janela)` |
| mediana | 12 | 7,2 | 808.584.473 tokens | `ceil(D / C_janela)` |
| mediana | 40 | 24,0 | 2.695.281.576 tokens | `ceil(D / C_janela)` |
| p90 | 3 | 1,8 | 1.222.289.932 tokens | `ceil(D / C_janela)` |
| p90 | 12 | 7,2 | 4.889.159.730 tokens | `ceil(D / C_janela)` |
| p90 | 40 | 24,0 | 16.297.199.100 tokens | `ceil(D / C_janela)` |

A coluna da direita fica simbólica **porque nenhum fornecedor publica
`C_janela`**. É exatamente aí que método se diferencia de tabela inventada: o
método diz o que falta, em que unidade e como obter.

**Caminho de chave de API** — quatro das cinco conexões da instalação
inspecionada são chaves, não contas OAuth (o comando que mostra isso e a saída
dele estão na seção do subsistema de quota, mais abaixo). Aqui o teto é publicado
e a conta fecha, em entrada não-cacheada:

| Perfil | Devs | `c` | `U_sim` | RPM | Entrada/min | Saída/min | Tier mínimo |
| :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| mediana | 3 | 0,6 | 1,8 | 8 | 31.611 | 5.342 | Start |
| mediana | 12 | 0,6 | 7,2 | 30 | 126.443 | 21.366 | Start |
| mediana | 12 | 1,0 | 12,0 | 50 | 210.738 | 35.611 | Start |
| mediana | 40 | 0,4 | 16,0 | 67 | 280.984 | 47.481 | Start |
| mediana | 40 | 0,6 | 24,0 | 101 | 421.477 | 71.221 | Start |
| mediana | 40 | 1,0 | 40,0 | 168 | 702.461 | 118.702 | Start |
| p90 | 3 | 0,6 | 1,8 | 13 | 110.053 | 18.206 | Start |
| p90 | 12 | 0,6 | 7,2 | 54 | 440.210 | 72.824 | Start |
| p90 | 40 | 0,6 | 24,0 | 178 | 1.467.368 | 242.748 | Start |
| p90 | 40 | 1,0 | 40,0 | 297 | 2.445.613 | 404.580 | **Build** |

Um time de quarenta no perfil mediano **ainda cabe no tier Start**. Só o canto
extremo pede Build, e quem empurra é entrada por minuto (2,45 M contra 2,00 M),
não RPM. O teto de rajada raramente dói: contra os 1.000 RPM do Start sobram
132× para 3 devs, 33× para 12 e 9× para 40. E **`c` move mais o resultado que
`N`** — 40 devs a `c=0,4` e 12 a `c=1,0` caem no mesmo tier; medir concorrência
vale mais que contar cadeiras.

## O que este sincronizador te mostra sobre isso

**O sinal de saturação.** O OmniRoute grava a trava na coluna
`rate_limited_until` de `provider_connections`, ou como `rateLimitedUntil` dentro
da coluna JSON `data` em instalações migradas. O `get_all_connections` projeta as
duas numa chave só, `rateLimitedUntil` (`src/omini_rtksync/gateway.py:193`), e
`ConnectionRecord.rate_limit_active` a lê como **prazo, não bandeira**
(`src/omini_rtksync/models.py:157-169`): ela guarda o instante em que a janela do
provedor reabre, e tratar a presença do campo como "limitada" deixava a conexão
amarela para sempre depois do primeiro 429. Com o prazo no futuro, o
`health_status` vira `rate_limited` e a tela pinta o badge **Rate limit** na
coluna de saúde da tabela de conexões (`src/omini_rtksync/i18n.py:206`,
`src/omini_rtksync/render.py:36`). Onde essa coluna fica: [Dashboard](Dashboard).

**O contador que fecha o laço.** Vencido o prazo, o sincronizador limpa a trava e
registra `Trava de rate limit vencida removida`
(`src/omini_rtksync/cli.py:143-149`). Essas entradas se acumulam no modal de
**Logs** do agendador. Contá-las por conta por dia durante uma semana é a única
evidência que de fato fecha a conta: conta que trava todo dia está
subdimensionada, conta que nunca trava é folga que absorve mais gente. Todo o
resto desta página é projeção.

**O headcount.** Os cartões de métrica trazem **Contas OAuth** e **Chaves de
API** (`src/omini_rtksync/i18n.py:149-150`). O primeiro é o `L` da seção de
termos de uso; o segundo é o caminho dimensionado por tier.

**A alavanca mais barata.** **Combos registrados** e a seção **Combos de
resiliência** listam cada combo e sua cascata de modelos, lidos da tabela
`combos` do OmniRoute como `name`, `kind` e `models`
(`src/omini_rtksync/gateway.py:449-473`). A quota pode se esgotar **por família
de modelo** com a conta seguindo saudável: durante um bloqueio do Antigravity,
todos os modelos da família Gemini devolveram 503 enquanto os Claude e os de
peso aberto da mesma conta continuaram respondendo `[FONTE:
pathbit-ai-for-devs/0002_claude_gravity_utilizando_9router/article/ARTICLE.md:684-720]`.
Um combo de fallback que atravessa famílias multiplica a capacidade efetiva
**sem comprar licença**, e é a única alavanca aqui que não esbarra no limite de
termos de uso. A instalação inspecionada tinha **0 combos** registrados — o
comando de contagem e a saída completa estão na seção do subsistema de quota.

**Automação.** O `/api/status` devolve `connectionsCount` e `combosCount`
(`src/omini_rtksync/web.py:401-412`), e `ominirtksync --status --db-path
~/.omniroute/data/storage.sqlite` imprime o mesmo inventário. O produto tenta
cinco caminhos, nesta ordem, e fica com o primeiro que existir:
`/app/data/storage.sqlite`, `/app/data/data.sqlite`,
`~/.omniroute/data/storage.sqlite`, `~/.omniroute/storage.sqlite`,
`~/.omniroute/data.sqlite`
`[FONTE: src/omini_rtksync/config.py:221-227]`. Ou seja, o `data/` do meio não é
exigido — ele só vem antes. Escreva o caminho que a sua instalação realmente
tem: num contêiner é o primeiro, numa instalação local costuma ser o terceiro.

**O que o painel não mostra, e não vai fingir que mostra:** contagem de tokens,
percentual de quota consumida, janela restante ou `max_concurrent`. O
sincronizador lê saúde de credencial; não é produto de medição.

**Uma lacuna, registrada:** `provider_connections` tem também
`backoff_level INTEGER DEFAULT 0` `[FONTE: schema lido de
diegosouzapw/omniroute:latest em execução, 2026-09-12]`. O sincronizador limpa
`rate_limited_until` quando o prazo vence (`src/omini_rtksync/gateway.py:376-378`)
mas **nunca zera `backoff_level`** — a string não aparece em lugar nenhum sob
`src/`. O irmão 9RTKSync zera o equivalente ao limpar a trava `[FONTE:
9RTKSync/src/nine_rtksync/normalizer.py:87 — `data["backoffLevel"] = 0`]`. Se o
OmniRoute decai o nível sozinho é `[A VERIFICAR: leia o tratamento de
backoff_level no fonte do gateway]`. Fica anotado porque um nível de backoff
velho faria uma conta parecer mais saturada do que está.

## O lugar com o formato de `C_janela` — existe, e está parcialmente preenchido

O schema do OmniRoute traz um subsistema de quota inteiro — e schema, ao
contrário de contagem de linha, é propriedade durável: vem da imagem, não do
tráfego, e sai de um comando só (`SELECT sql FROM sqlite_master`, na seção em
inglês). O centro dele é
`provider_quota_state(connection_id, model)` com `tokens_used`, `token_limit`,
`window_start` e `window_reset`: é o `C_janela` em forma de banco, e na
granularidade que a observação do Antigravity diz ser a necessária — por família
de modelo, não por conta. Ao lado ficam `quota_snapshots` (`window_key`,
`remaining_percentage`, `is_exhausted`, `next_reset_at`, `window_duration_ms`,
`raw_data`), `provider_plans` (`dimensions_json`, `source` restrito a `auto` ou
`manual`) e `quota_pools` com `quota_pool_connections`. Repare no `window_key`:
ele é a dimensão por modelo do próprio `quota_snapshots`.

**Contar exige dois comandos, e o segundo é fácil de errar.** O contêiner não tem
`sqlite3`, e o banco roda em modo WAL — um `docker cp` só do `storage.sqlite` lê
arquivo defasado e subconta o que ainda está no log de escrita (medido numa cópia
só, lida duas vezes: 188 `call_logs` sem o `-wal` ao lado, 209 com ele — 21
linhas, 10% da tabela, invisíveis ao comando mais curto, e sem erro nenhum na
tela). Os comandos com o `-wal`
junto, e a saída completa, estão na seção em inglês. O resultado, no instante
2026-09-13T02:20:53Z: `provider_quota_state` 0, `provider_plans` 0, `quota_pools`
0, `daily_usage_summary` 0, `combos` 0, `usage_history` 8, `provider_connections`
5, `call_logs` 209 — e **`quota_snapshots` com 48 linhas**.

**Leia isso como instante, não como propriedade.** `call_logs` e
`quota_snapshots` sobem conforme passa tráfego: neste mesmo contêiner, `call_logs`
sobem enquanto você lê. Rode o comando duas vezes, com alguns minutos de
intervalo, e as duas contagens diferem — é essa a demonstração. Leituras
anteriores desta mesma instalação não são reproduzíveis por você e, por isso,
não são citadas. Os zeros são a parte durável; o 48
é a parte interessante, porque uma leitura anterior desta mesma instalação pegou
`quota_snapshots` em 0 e a página concluiu, errado, que nada nunca preenche
aquilo. O que houve é que o primeiro snapshot foi gravado às 23:29:23.587Z,
*depois* da última linha de `usage_history`, às 22:54Z. A medição estava certa; a
conclusão tirada dela, não.

Então o achado honesto é o oposto de "vazio": **o OmniRoute preenche
`quota_snapshots` sozinho, por chave de janela, na conexão OAuth — e só nela.**
São 16 `window_key` distintos numa conta só, misturando nomes de modelo
(`claude-sonnet-4-6`, `gemini-3.1-pro-high`, `gpt-oss-120b-medium`) e chaves
agregadas (`claude_gpt_weekly`), mais duas (`chat_20706`, `chat_23310`) que
parecem contadores por conversa e não se encaixam em nenhuma das descrições. As
quatro conexões por chave de API não têm
nenhum. É a dimensão por modelo, populada, sem ninguém ter declarado plano em
`provider_plans` antes.

**E mesmo assim não é o `C_janela`.** Todas as 48 linhas trazem
`remaining_percentage = 100.0`, `is_exhausted = 0`, com `window_duration_ms` e
`raw_data` NULL — conta que ninguém trabalhou o bastante para arranhar. Mais
importante que o número vazio é a **unidade**: porcentagem é `1 − p`, a fração
consumida. É o lado esquerdo do procedimento de calibração do fim desta página,
não a resposta dele. Para ter `C_janela` em tokens, ainda é preciso casar essa
porcentagem com o que você de fato trafegou na mesma janela. O que o OmniRoute dá
de graça é o `p` — lido por máquina, por modelo, em vez de estimado a olho numa
barra de progresso. É um atalho real, e vale exatamente isso.

O `provider_quota_state`, que é quem carrega o `token_limit` — o número absoluto
—, continua em 0. Se o OmniRoute chega a escrever ali, e a partir de qual
resposta do provedor, é `[A VERIFICAR: leia no fonte do gateway quem escreve em
provider_quota_state, e se depende de plano declarado em provider_plans]`. Este
sincronizador não lê nenhuma dessas tabelas: `git grep -n
'quota_snapshots\|provider_quota_state' -- src/` não devolve nada.

Vale a mesma ressalva para `max_concurrent` e `rate_limit_protection`, que são
onde o `c` seria materializado por conta: `max_concurrent` NULL nas cinco
conexões e `rate_limit_protection` em 0 nas cinco, pelo mesmo comando, no mesmo
instante. O botão existe e ninguém girou.

**Sobre granularidade:** o 9Router guarda travas por família como chaves
`modelLock_*` dentro do JSON da conexão. **No OmniRoute não existe `modelLock_*`.**
Aqui a *trava* é um prazo único para a conexão inteira — mas não conclua daí,
como uma versão anterior desta página concluiu, que falta granularidade por
modelo. Não falta: ela vive em `quota_snapshots.window_key`, que está preenchido,
e viveria em `provider_quota_state.model`, que não está. Duas tabelas, duas
respostas, e só uma delas vazia. Não porte o parágrafo do irmão para cá, e não
porte este de volta.

## Meça a demanda aqui, não só no laptop do dev

O script lê o histórico de uma máquina. O OmniRoute tem algo melhor para um
time, porque é por conta: `usage_history` registra cada chamada que passou pelo
gateway com exatamente a separação de tokens que a fórmula pede.

```bash
# Instalação local; em contêiner, copie antes com o `-wal` junto, como na seção
# do subsistema de quota -- sem ele a contagem sai menor do que é.
sqlite3 -header -column ~/.omniroute/data/storage.sqlite "
SELECT provider,
       count(*)                                  AS requisicoes,
       sum(tokens_input + tokens_cache_creation) AS entrada_que_conta,
       sum(tokens_cache_read)                    AS leitura_de_cache,
       sum(tokens_output)                        AS saida,
       min(timestamp)                            AS de,
       max(timestamp)                            AS ate
FROM usage_history GROUP BY provider ORDER BY requisicoes DESC;"
```

A saída real contra o banco vivo está na seção em inglês: quatro provedores,
oito requisições. **São oito linhas de teste de fumaça, não carga de trabalho** —
a forma da consulta está provada, o volume não prova nada. No mapeamento:
`T_in = tokens_input + tokens_cache_creation`,
`T_tot = T_in + tokens_cache_read`, `T_out = tokens_output`, e `R_h` sai de
agrupar por `strftime('%Y-%m-%dT%H', timestamp)` junto com `connection_id`.

Dois limites: a tabela só enxerga o que passou **pelo** gateway — um dev
apontando o Claude Code direto para a assinatura dele é invisível — e
`call_logs` é a fonte melhor quando você precisa separar sucesso de 429, porque
guarda a linha por requisição com `status` e `connection_id`.

## O limite que não é capacidade

Tudo acima dimensiona **capacidade técnica**. Nada disso autoriza compartilhar
assinatura, e o texto do fornecedor é explícito `[FONTE:
https://code.claude.com/docs/en/legal-and-compliance — lido em 2026-09-12]`: os
limites anunciados de Pro e Max pressupõem *"ordinary, individual usage"*; a
autenticação OAuth é *"intended exclusively for purchasers"* dos planos; a
Anthropic não permite *"route requests through Free, Pro, or Max plan
credentials on behalf of their users"*; e *"Customers may not pay for, resell, or
intermediate Claude usage on their end users' behalf. Each end user must
authenticate with their own Anthropic API key, Claude subscription plan
credentials, or 3P inference provider credential."*

Isso fecha a pergunta original com uma resposta que **não depende de medir nada**:

> **Para Claude Pro/Max, `L = N`.** Doze devs, doze assinaturas, cada uma
> comprada e autenticada pelo seu titular. O gateway não reduz esse número. Ele
> serve para roteamento, fallback entre famílias, renovação de credencial e
> observabilidade sobre contas que **já são individuais** — e é exatamente aí que
> paga o próprio custo.

O dimensionamento por tier se aplica integralmente ao **caminho de API** — chave
da organização, cobrada ao titular, distribuída internamente. O mesmo documento
ressalva isso como permitido: *"This does not restrict how customers provision
and manage their own API keys … for use by the customer's own authorized
users."*

Para **Google AI Pro / Antigravity** e para os planos da **OpenAI**, os termos
equivalentes não foram lidos: `[A VERIFICAR: leia os termos de assinatura de cada
um e cite URL + data, como foi feito com a Anthropic]`. Não presuma simetria
entre fornecedores.

Uma consequência operacional, porque é o formato natural de um gateway com todas
as contas cadastradas: várias sessões na mesma conta não incomodam; o que chama
atenção é o inverso, várias contas saindo pelo mesmo endereço. O OmniRoute modela
o remédio como vínculo de saída por conta, e um pool inativo ou sem endereço cai
em silêncio para o endereço do host — é disso que trata
[Egress and Multi-Session](Egress-And-Multi-Session), e
[Egress Testing](Egress-Testing) é a bancada que prova por onde o tráfego sai de
verdade.

## Renovação de credencial não é quota

Dois relógios diferentes; confundi-los produz o diagnóstico errado.

| | Validade da credencial | Quota |
| :--- | :--- | :--- |
| Duração | 3.599 s ≈ 1 h, lido da conexão | 5 h / semanal; 2 h por família, observado |
| Sintoma | 401, desconexão "espontânea" | 429 / 503 |
| Campo | `expires_at` | `rate_limited_until` |
| Quem resolve | renovação automática — o que este sincronizador faz | esperar a janela, ou mais uma assinatura |
| Escala com o time? | **Não** | **Sim** |

A hora da primeira coluna não é folclore: o gateway guarda a validade que o
provedor entregou, e na conexão OAuth daqui ela vem como `expires_in = 3599`
`[FONTE: `SELECT provider, auth_type, expires_in FROM provider_connections WHERE auth_type='oauth'`, 2026-09-13T02:20:53Z]`.
É **uma** conexão de **um** provedor: leia como "esta credencial dura uma hora",
não como "token OAuth dura uma hora".

O "desconecta sozinho depois de uma hora" é **formato de gravação**, não falta de
cota: `expires_at` é coluna TEXT lida com `new Date(...)`, e um epoch numérico
gravado como texto vira data inválida, o gateway conclui que a conexão não tem
expiração conhecida e para de renovar preventivamente. O sincronizador grava
ISO-8601, o formato nativo do próprio gateway
(`src/omini_rtksync/gateway.py:209-243`). **Nenhum dos dois relógios entra na
fórmula.** O [Upstream Fixes](Upstream-Fixes) conta o lado do gateway nesse bug
de parsing.

## Para reproduzir

Os dois scripts estão versionados aqui, porque número sem script que o reproduza
vira, com o tempo, número inventado. Confira antes de confiar na página: o
`git ls-files tools/` tem de listar os dois.

`tools/measure_agent_usage.py` produz o perfil de demanda — lê **apenas** os
campos numéricos de `usage`, o `message.id` e o `timestamp`, sem tocar em
conteúdo de conversa, deduplica por `message.id` e imprime o fator de inflação
que a deduplicação remove. O `--until ISO` congela o corpus num instante passado,
e é isso que torna um número publicado conferível. `tools/sizing.py` resolve as
tabelas acima e rotula cada entrada como `medido`, `publicado` ou `arbitrado` nas
primeiras linhas da saída, com o comando exato da medição: a cadeia da medição
até a tabela é para ser percorrida de trás para frente. Troque as constantes
pelas suas e recalcule.

Os números desta página vêm de três lugares diferentes, e a diferença é o ponto
inteiro: **publicado** (página do fornecedor, com URL e data — cite, não faça
média), **medido** (script deste repositório, com o comando — reproduzível
*naquele* corpus, *naquele* corte) e **arbitrado** (escolha de operação, como `c`
e a folga — exemplo resolvido, meça o seu). O que não couber em nenhum dos três
não entra na página.

Para achar `C_janela` de uma assinatura, o único lugar onde ela aparece é
**Settings → Usage**, com as barras da janela de 5 h e da semanal: espere o
reset, trabalhe uma jornada típica dentro da janela, leia a fração `p` consumida
na barra, rode o script restrito ao período somando o token **total** trafegado
(`D_medido`) e faça `C_janela ≈ D_medido / p`. É medição com procedimento
declarado, não palpite — e vale para *aquele* plano, *aquele* modelo e *aquele*
nível de esforço.

Por fim, ao conferir o que foi injetado numa stack, rode sempre
`docker compose --no-interpolate config`. Sem a flag o comando despeja os
segredos do ambiente direto no seu terminal.
