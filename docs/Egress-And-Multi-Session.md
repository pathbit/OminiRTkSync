# Multiple sessions on one account: where the traffic comes from

*(Versão em português ao final.)*

Providers let one account hold several concurrent sessions. What causes trouble
is not the number of sessions — it is **how they look from the outside**. When a
gateway fans many accounts out through a single machine, every one of those
accounts shows the same source address, and the pattern is what draws attention.

This page documents what the gateway already gives you to control that, and what
the synchronizer shows about it. Every schema detail below was read from the running `decolua/9router` and
`diegosouzapw/omniroute` images — each section names the gateway it describes.
Nothing here is a claim about any provider's policy, which we cannot verify and
do not restate.

---

## What OmniRoute already models

Three tables, all present in the shipped schema:

| Table | What it holds |
| --- | --- |
| `proxy_registry` | The egress endpoints: `host`, `port`, `username`, `password`, `region`, `country_code`, plus measured `latency_ms`, `quality_score`, `anonymity` and `google_access`. |
| `proxy_assignments` | The binding: `proxy_id`, `scope`, `scope_id`, `position`. Unique on `(scope, scope_id, proxy_id)`. |
| `proxy_scope_rotation` | Per scope: `strategy`, `cursor`, `sticky_window_minutes`, `rotated_at`. |

`scope` takes three values in the code: **`global`**, **`provider`** and
**`account`**. Two more switches live on the connection row itself:
`provider_connections.proxy_enabled` and `per_key_proxy_enabled`.

### The property that matters

The selector short-circuits when a scope resolves to exactly one proxy — with a
single entry it is returned directly, without consulting rotation at all.

That gives you the arrangement you want with no extra machinery:

> **One proxy assigned at `scope='account'` for a given connection id = that
> account always egresses from that address.**

Where rotation *is* involved, `sticky_window_minutes` keeps the same choice for
a window; the stored default when a scope row is created is **30 minutes**.
Rotation is the opposite of what you want per account: it makes one account
appear from several addresses over time.

### How to set it up

Through OmniRoute's own screens and API — the synchronizer never writes here:

1. Register each egress under **Settings → Proxies** (`/api/settings/proxies`).
2. Bind one to the connection, choosing the **account** scope, so the assignment
   lands with `scope='account'` and `scope_id` equal to the connection id.
3. Turn on `proxy_enabled` for that connection.
4. Leave exactly **one** proxy in that scope. One entry means no rotation.

Verification, without leaving the box:

```sql
SELECT c.name,
       COALESCE(NULLIF(r.name,''), r.host || ':' || r.port) AS egress
FROM provider_connections c
LEFT JOIN proxy_assignments a ON a.scope = 'account' AND a.scope_id = c.id
LEFT JOIN proxy_registry    r ON r.id = a.proxy_id
ORDER BY c.name;
```

Any row with `egress` NULL shares the host's address with every other such row.

---

## What 9Router models

9Router keeps the pools in the `proxyPools` table and the binding **inside the
connection**, under `providerSpecificData`:

- `proxyPoolId` — which pool this connection egresses through;
- `connectionProxyEnabled` — the per-connection switch, which must be `true`.

Same principle, different storage: the binding belongs to the connection rather
than to a separate assignment table.

---

## What the synchronizer shows

The panel surfaces the binding **read-only**, per connection:

- **bound** — the account has its own egress, and the panel names it;
- **shared** — no binding; this account leaves through the same address as the
  rest;
- **unknown** — the installation is older than the proxy tables.

The synchronizer never creates, edits or deletes a proxy, an assignment or a
rotation strategy. It reads, and it tells you what it found. Ownership of the
routing stays with the gateway, which is the only component that can actually
apply it to a request.

---

## Tailscale, and what it does and does not solve

Tailscale is one way to *obtain* egress addresses; it is not a replacement for
the binding above.

- An **exit node** gives the gateway host one stable outbound address — the
  node's. Useful when you want a known, stable address instead of whatever your
  ISP hands out. But it is **one address for the whole host**: with several
  accounts on one gateway, they all still share it. An exit node alone does not
  separate accounts.
- **Several exit nodes** do separate them, but only if each account is bound to
  a different one — and the binding is exactly the `scope='account'` assignment
  described above. Tailscale supplies the addresses; OmniRoute decides which
  account uses which.
- `--advertise-exit-node` plus `--exit-node` are per-host settings. To route
  per-account you still need one HTTP/SOCKS endpoint per node, registered in
  `proxy_registry` like any other egress.

The same holds for any provider of addresses — a VPS, a residential proxy, a
second uplink. The part that separates accounts is the per-account binding, not
the technology that produced the address.

### Reasonable defaults

- One egress per account when several accounts of the same provider live on one
  gateway.
- Prefer **stability over rotation** for a named account: an account whose
  address changes every few minutes looks less like a person, not more.
- Keep egress and account in the same region when the provider is
  region-sensitive; `proxy_registry.country_code` is there for that.
- Check `google_access` on the registry row before binding a Google-backed
  connection to it — the column exists because not every egress can reach it.

---

# Em português

Provedores aceitam mais de uma sessão ativa por conta. O que costuma dar
problema não é a quantidade de sessões, e sim **como elas aparecem de fora**:
quando um gateway distribui várias contas por uma única máquina, todas saem pelo
mesmo endereço, e é esse padrão que chama atenção.

Esta página documenta o que o gateway **já oferece** para controlar isso e o que
o sincronizador mostra a respeito. Cada detalhe de schema foi lido das imagens `decolua/9router` e
`diegosouzapw/omniroute` em execução — cada seção diz de qual gateway trata.
Não há aqui nenhuma afirmação sobre política de fornecedor, que não temos como
verificar.

## O que o OmniRoute já modela

- `proxy_registry` — os endereços de saída (host, porta, usuário, senha, região,
  `country_code`, `google_access`, latência e pontuação de qualidade).
- `proxy_assignments` — o vínculo: `proxy_id`, `scope`, `scope_id`, `position`.
- `proxy_scope_rotation` — por escopo: `strategy`, `cursor`,
  `sticky_window_minutes`, `rotated_at`.

Os escopos no código são **`global`**, **`provider`** e **`account`**. Na própria
linha da conexão existem ainda `proxy_enabled` e `per_key_proxy_enabled`.

**A propriedade que importa:** quando um escopo resolve para exatamente um
proxy, o seletor devolve esse proxy direto, sem consultar rotação. Ou seja:

> **Um proxy vinculado em `scope='account'` para o id de uma conexão = aquela
> conta sai sempre pelo mesmo endereço.**

Onde há rotação, `sticky_window_minutes` mantém a escolha por uma janela — o
padrão gravado ao criar a linha do escopo é de **30 minutos**. Rotação é o
oposto do que se quer por conta: faz uma conta aparecer de vários endereços.

**Como configurar** (pelas telas e pela API do próprio OmniRoute; o
sincronizador não escreve nada disso):

1. Cadastre cada saída em **Settings → Proxies**.
2. Vincule uma à conexão escolhendo o escopo **account**.
3. Ligue `proxy_enabled` naquela conexão.
4. Deixe **um único** proxy nesse escopo — um só significa sem rotação.

## O que o 9Router modela

Os pools ficam em `proxyPools` e o vínculo vive **dentro da conexão**, em
`providerSpecificData`: `proxyPoolId` diz por qual pool ela sai, e
`connectionProxyEnabled` precisa estar `true`. Mesmo princípio, armazenamento
diferente.

## O que o painel mostra

Somente leitura, por conexão: **vinculada** (com o nome da saída),
**compartilhada** (sai junto com as outras) ou **desconhecida** (instalação
anterior às tabelas de proxy). O sincronizador nunca cria, edita nem apaga
proxy, vínculo ou estratégia — quem manda no roteamento é o gateway, o único que
consegue de fato aplicá-lo a uma requisição.

## Tailscale: o que resolve e o que não resolve

- Um **exit node** dá ao host do gateway um endereço de saída estável — o do
  nó. É útil para ter um endereço conhecido em vez do que o provedor de internet
  entregar. Mas é **um endereço para o host inteiro**: com várias contas no
  mesmo gateway, todas continuam compartilhando. Exit node sozinho não separa
  contas.
- **Vários exit nodes** separam, desde que cada conta esteja vinculada a um
  deles — e o vínculo é exatamente o `scope='account'` acima. O Tailscale
  fornece os endereços; o OmniRoute decide qual conta usa qual.
- Para rotear por conta você ainda precisa de um endpoint HTTP/SOCKS por nó,
  cadastrado em `proxy_registry` como qualquer outra saída.

Vale o mesmo para qualquer origem de endereços — um VPS, um proxy residencial,
um segundo link. O que separa contas é o vínculo por conta, não a tecnologia que
produziu o endereço.

**Padrões razoáveis:** uma saída por conta quando várias contas do mesmo
provedor dividem um gateway; preferir **estabilidade a rotação** para conta
nomeada; manter saída e conta na mesma região quando o provedor for sensível a
isso; e conferir `google_access` antes de vincular uma conexão do Google.
