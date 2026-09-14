# Single sign-on: signing in through an identity provider

*(Versão em português ao final.)*

The panel can accept a second way in: an identity provider you already run — Google Workspace,
Microsoft Entra ID, Okta, Keycloak — through **OpenID Connect**. It is optional, off by default,
and it never replaces the local user and password form.

> **The local form never leaves the screen.** If the identity provider is unreachable, the panel
> still opens with the password you set, and the break-glass recovery credential still works. A
> panel whose only door is somebody else's server is a panel you lose when that server has a bad
> morning.

---

## What this page covers

| Section | What you get |
|---|---|
| [Before you start](#before-you-start) | the one prerequisite that breaks everything if you skip it |
| [Turning it on](#turning-it-on) | the screen, field by field |
| [Google Workspace](#setting-it-up-in-google-workspace) | a real provider, start to finish |
| [Entra ID and Okta](#entra-id-and-okta) | what changes for the other two |
| [When the provider goes down](#when-the-provider-goes-down) | four ways back in, in order of effort |
| [What is checked on the way back](#what-is-checked-on-the-way-back) | the list, and why each item is on it |
| [SAML 2.0](#saml-20-is-not-in-this-version) | why the tab is greyed out |

---

## Before you start

**The panel needs a stable public address.** Federated sign-in works by sending the browser to the
provider and having it come back to an address you registered there in advance. If that address
changes, every sign-in attempt ends with the provider refusing to come back.

This rules out the Cloudflare quick tunnel, which gets a brand new hostname on every start — see
[Remote Access](Remote-Access). Use one of:

- a **named** Cloudflare tunnel, with a hostname you own;
- **Tailscale**, using the stable machine name on your tailnet;
- any reverse proxy in front of the panel with a fixed name.

Whatever you pick becomes the **public panel address** in the form, and the panel builds the return
address from it — never from the `Host` header the browser sends, because that header is chosen by
whoever is making the request.

---

## Turning it on

Sign in with your local password, then press **Settings** in the header bar. The window has two
tabs; the OpenID Connect one is the live one.

| Field | What goes in it |
|---|---|
| Public panel address | `https://panel.example.com` — exact, no trailing slash |
| Return address | shown, not typed. Copy it to the provider |
| Issuer | `https://accounts.google.com`, or whatever your provider publishes |
| Client ID | from the application you register at the provider |
| Client secret | from the same place. Write-only: see below |
| Scopes | leave `openid email profile` unless you know why not |
| Allowed domains | `example.com,branch.example.com` |
| Allowed e-mail addresses | `boss@example.com` |
| Active provider | switch to OpenID Connect last, once the rest is filled in |
| Your current panel password | required — see below |

Three of these behave in ways worth knowing about.

**The client secret never comes back.** The field shows dots when a secret is stored and stays
blank otherwise. Saving with it blank keeps the one already there; you only ever write, never read.
It lives in a file with mode `0600` next to the recovery credential, not in the preferences
database. You can also set it in the environment as `OIDC_CLIENT_SECRET`, and then the environment
wins and the field is locked — the same rule `DASHBOARD_PASSWORD` already follows.

**The allowed list is mandatory and cannot be empty.** "Sign in with Google" without a filter means
every Google account on the planet gets into your panel. The panel refuses to switch SSO on with
both lists blank, and refuses to complete a sign-in if it somehow finds them blank later.

**Your current panel password is asked for again.** You are already signed in, so this looks like
friction for nothing. It is not: a stolen eight-hour session would otherwise be enough to point the
panel at a hostile provider and add the attacker to the allowed list — permanent access that
survives you changing your password.

---

## Setting it up in Google Workspace

1. Open the Google Cloud console and pick (or create) a project.
2. **APIs & Services → OAuth consent screen.** Choose **Internal** if the panel is only for people
   in your Workspace; that alone stops outside accounts from ever reaching the consent screen.
   Fill in the app name and the support e-mail.
3. **APIs & Services → Credentials → Create credentials → OAuth client ID.**
   Application type: **Web application**.
4. Under **Authorised redirect URIs**, paste the *Return address* the panel showed you. It ends in
   `/sso/oidc/callback`. It must match character for character, including `https` and the port if
   there is one.
5. Save. Google shows the client ID and client secret once — copy both into the panel now.
6. In the panel: issuer `https://accounts.google.com`, allowed domains = your Workspace domain,
   active provider = OpenID Connect, type your panel password, save.
7. Sign out and look at the sign-in screen. Below the password form there is now a
   **Sign in with accounts.google.com** button.

If the button is missing, the panel could not reach the provider's discovery document. That is a
network problem, not a configuration one — the panel deliberately hides the button instead of
offering a door that does not open.

## Entra ID and Okta

The shape is the same; only the names move.

**Microsoft Entra ID** — register an application under *App registrations*, add the return address
as a **Web** redirect URI, create a secret under *Certificates & secrets*, and use the issuer from
the *OpenID Connect metadata document* link (it looks like
`https://login.microsoftonline.com/<tenant>/v2.0`). Paste it without the
`/.well-known/openid-configuration` part.

**Okta** — create an *OIDC / Web Application*, set the sign-in redirect URI to the return address,
and use your org URL as the issuer (`https://your-org.okta.com`, or the custom authorisation server
URL if you use one).

For both, the issuer you type must be **exactly** the value the provider reports as `issuer` in its
own metadata. The panel compares the two and refuses to go on if they differ — that check is what
stops a response from one provider being accepted as if it came from another.

---

## When the provider goes down

In order of how little you have to do:

1. **Just sign in with your password.** The form is right there. This is the answer almost every
   time, and it is why the form never goes away.
2. **The recovery credential still works.** `admin` plus the recovery hash gets in whatever the
   state of the provider, exactly as described in [Authentication](Authentication).
3. **Scripts and monitoring never noticed.** Anything that does not ask for HTML — `curl`, cron,
   your uptime check — authenticates the way it always did. Federated sign-in only ever applies to
   the browser door.
4. **Switch it off without opening the panel.** Set `SSO_DISABLED=1` in the environment and bring
   the service back up. It beats whatever is in the database, touches nothing, and is reversible by
   removing the variable. Use it when the provider is not merely slow but wrong — sending people to
   the wrong place, or refusing a domain it used to accept.

To turn it off from the screen instead: Settings → Active provider → *Off (password only)* → type
your panel password → save.

> **One thing that is not a bug.** *Sign out* clears the panel session only. Your session at the
> identity provider is still open, so the next click on the SSO button may walk straight back in
> without asking for anything. Federated logout is not implemented in this version.

---

## What is checked on the way back

When the provider sends the browser back, the panel refuses on the first thing that does not add
up, and every refusal produces **the same message on screen**. Telling you whether it was the state
or the allowed list would also tell an attacker how far they got; the real reason goes to the log.

| Checked | Why it is on the list |
|---|---|
| Rate limit for the address | the two new routes are public, so they get the same ceiling as the form |
| State cookie present and its signature intact | without the signature, whoever writes both sides always matches |
| State in the URL equals state in the cookie | this is what stops login CSRF — being signed into the attacker's account |
| State not used before | apart from the cookie, the server remembers what it consumed: single use for real |
| No error, and a code is present | |
| The code exchanged with proof of key possession | the code travels in the address bar and stays in history; the key never leaves the cookie |
| `iss` equals the configured issuer | one provider at a time, so there is one right answer |
| `aud` contains the client ID | a valid token issued for a *different* service must not work here |
| Not expired, and issued within five minutes | a generous tolerance turns a deadline into decoration |
| Nonce equals the one from the cookie | ties the token to that particular trip |
| The user info and the token describe the same subject | proves the exchange actually happened |
| E-mail confirmed by the provider | otherwise an unverified address is enough to get in |
| E-mail or domain on the allowed list | |

Only then is a session issued — **the same signed cookie** the password form issues, with the same
eight-hour life and the same properties. There is no second kind of session.

The landing page is a real page with a redirect in it, and not an HTTP redirect: browsers do not
carry a strict same-site cookie through a redirect chain that started on another site, and you
would land back on the sign-in form holding a perfectly good session. The destination is always the
panel root — no parameter in the URL is ever used as a destination.

Every one of these refusals has a test that exercises it with bad input, in
`tests/test_sso_oidc.py`.

---

## SAML 2.0 is not in this version

The tab is there, the fields are there, and they are disabled. This is a technical limit, not a
missing afternoon of work.

SAML signs over *Exclusive XML Canonicalization 1.0*. The Python standard library canonicalises XML
too — but with C14N 2.0, a different algorithm, which makes every valid signature look invalid. The
standard library also has no RSA verification at all, and its XML parser does not defend against
signature wrapping, the attack specific to this protocol, where a signed assertion is moved
elsewhere in the document and a forged one takes its place.

Writing that by hand produces validation that appears to work and accepts forged assertions in
silence. So SAML support waits for a library: `python3-saml` is declared in `pyproject.toml` under
the optional `saml` extra, and the image will need `libxml2`, `libxslt` and `xmlsec` before it can
be enabled. Until then the panel refuses to turn it on, and says so.

Also out of scope for this version, and deliberately: federated logout, and encrypted SAML
assertions.

---

## Where everything is stored

| What | Where | Why there |
|---|---|---|
| Issuer, client ID, public address, allowed lists | the panel preferences database, keys prefixed `sso.` | none of it is secret; it is the same table as the language setting |
| Client secret | a file with mode `0600` in the data directory | secrets do not belong in a database that the screen reads from |
| Which provider is active | preferences, one at a time | two providers at once is how a response from one gets accepted as the other |
| The trip state | a short-lived signed cookie, plus a consumed-state list in memory | it dies in ten minutes and grants nothing on its own |

The data directory is the one the rest of the panel already uses — see `DATA_DIR` in
[Configuration](Configuration).

---
---

# Entrada federada: entrar por um provedor de identidade

O painel pode aceitar uma segunda porta: um provedor de identidade que você já opera — Google
Workspace, Microsoft Entra ID, Okta, Keycloak — por **OpenID Connect**. É opcional, nasce
desligada, e nunca substitui o formulário local de usuário e senha.

> **O formulário local nunca sai da tela.** Se o provedor de identidade estiver fora do ar, o painel
> continua abrindo com a senha que você definiu, e a credencial de recuperação continua entrando.
> Um painel cuja única porta é o servidor de outra pessoa é um painel que você perde no dia em que
> aquele servidor amanhece mal.

---

## Antes de começar

**O painel precisa de um endereço público estável.** A entrada federada funciona mandando o
navegador ao provedor e fazendo-o voltar a um endereço que você registrou lá antes. Se esse
endereço muda, toda tentativa de entrar termina com o provedor recusando a volta.

Isso descarta o túnel rápido da Cloudflare, que ganha um nome novo a cada subida — veja
[Remote Access](Remote-Access). Use uma destas opções:

- um túnel Cloudflare **nomeado**, com um nome que é seu;
- **Tailscale**, com o nome estável da máquina na sua tailnet;
- qualquer proxy reverso na frente do painel, com nome fixo.

O que você escolher vira o **endereço público do painel** no formulário, e é dele que o painel monta
o endereço de retorno — nunca do cabeçalho `Host` que o navegador manda, porque quem escolhe esse
cabeçalho é quem faz a requisição.

---

## Ligando

Entre com a senha local e clique em **Configurações**, no cabeçalho. A janela tem duas abas; a de
OpenID Connect é a que funciona.

| Campo | O que vai nele |
|---|---|
| Endereço público do painel | `https://painel.exemplo.com` — exato, sem barra no fim |
| Endereço de retorno | é exibido, não digitado. Copie para o provedor |
| Issuer | `https://accounts.google.com`, ou o que seu provedor publicar |
| ID do cliente | da aplicação que você registrar no provedor |
| Segredo do cliente | do mesmo lugar. Só de escrita: veja abaixo |
| Escopos | deixe `openid email profile` a menos que saiba por que não |
| Domínios autorizados | `exemplo.com,filial.exemplo.com` |
| E-mails autorizados | `chefe@exemplo.com` |
| Provedor ativo | mude para OpenID Connect por último, com o resto preenchido |
| Sua senha atual do painel | obrigatória — veja abaixo |

Três desses campos se comportam de um jeito que vale conhecer.

**O segredo do cliente nunca volta.** O campo mostra pontinhos quando há um segredo guardado, e
fica vazio quando não há. Salvar com ele em branco MANTÉM o que já estava lá; só se escreve, nunca
se lê. Ele mora num arquivo de permissão `0600` ao lado da credencial de recuperação, e não no banco
de preferências. Você também pode defini-lo no ambiente, em `OIDC_CLIENT_SECRET`: aí o ambiente
vence e o campo fica travado — a mesma regra que `DASHBOARD_PASSWORD` já segue.

**A lista de autorizados é obrigatória e não pode ficar vazia.** "Entrar com o Google" sem filtro
significa que toda conta Google do planeta entra no seu painel. O painel recusa ligar o SSO com as
duas listas em branco, e recusa concluir uma entrada se de algum modo as encontrar vazias depois.

**Sua senha atual do painel é pedida de novo.** Você já está autenticado, então isso parece atrito à
toa. Não é: sem ela, uma sessão de oito horas roubada bastaria para apontar o painel a um provedor
hostil e pôr o atacante na lista de autorizados — acesso permanente, que sobrevive a você trocar a
senha.

---

## Configurando no Google Workspace

1. Abra o console do Google Cloud e escolha (ou crie) um projeto.
2. **APIs e serviços → Tela de consentimento OAuth.** Escolha **Interno** se o painel é só para
   gente do seu Workspace; só isso já impede que contas de fora cheguem à tela de consentimento.
   Preencha o nome do aplicativo e o e-mail de suporte.
3. **APIs e serviços → Credenciais → Criar credenciais → ID do cliente OAuth.**
   Tipo de aplicativo: **Aplicativo da Web**.
4. Em **URIs de redirecionamento autorizados**, cole o *Endereço de retorno* que o painel mostrou.
   Ele termina em `/sso/oidc/callback`. Tem de bater caractere por caractere, inclusive o `https` e
   a porta, se houver.
5. Salve. O Google exibe o ID e o segredo do cliente uma única vez — copie os dois para o painel
   agora.
6. No painel: issuer `https://accounts.google.com`, domínios autorizados = o domínio do seu
   Workspace, provedor ativo = OpenID Connect, digite a senha do painel, salve.
7. Saia e olhe a tela de entrada. Abaixo do formulário de senha há agora um botão
   **Entrar com accounts.google.com**.

Se o botão não aparecer, o painel não conseguiu ler o documento de descoberta do provedor. É
problema de rede, não de configuração — o painel esconde o botão de propósito, em vez de oferecer
uma porta que não abre.

## Entra ID e Okta

O formato é o mesmo; só os nomes mudam de lugar.

**Microsoft Entra ID** — registre uma aplicação em *Registros de aplicativo*, acrescente o endereço
de retorno como URI de redirecionamento do tipo **Web**, crie um segredo em *Certificados e
segredos*, e use o issuer que aparece no link do *documento de metadados do OpenID Connect* (algo
como `https://login.microsoftonline.com/<locatario>/v2.0`). Cole sem a parte
`/.well-known/openid-configuration`.

**Okta** — crie uma *OIDC / Web Application*, aponte a URI de redirecionamento de entrada para o
endereço de retorno, e use a URL da sua organização como issuer (`https://sua-org.okta.com`, ou a
URL do servidor de autorização personalizado, se usar um).

Nos dois casos, o issuer que você digita tem de ser **exatamente** o valor que o provedor declara
como `issuer` nos metadados dele. O painel compara os dois e recusa seguir se diferirem — é essa
conferência que impede a resposta de um provedor ser aceita como se fosse de outro.

---

## Quando o provedor cai

Em ordem de quão pouco você precisa fazer:

1. **Entre com a senha, simplesmente.** O formulário está ali. É a resposta quase sempre, e é por
   isso que ele nunca sai da tela.
2. **A credencial de recuperação continua valendo.** `admin` mais o hash de recuperação entra
   qualquer que seja o estado do provedor, como descrito em [Authentication](Authentication).
3. **Scripts e monitoramento nem perceberam.** Tudo que não pede HTML — `curl`, cron, seu
   monitor de disponibilidade — autentica como sempre autenticou. A entrada federada vale só para a
   porta do navegador.
4. **Desligue sem abrir o painel.** Defina `SSO_DISABLED=1` no ambiente e suba o serviço de novo.
   Isso vence o que estiver no banco, não toca em nada, e se desfaz removendo a variável. Use quando
   o provedor não estiver apenas lento, e sim errado — mandando gente para o lugar errado, ou
   recusando um domínio que antes aceitava.

Para desligar pela tela: Configurações → Provedor ativo → *Desligado (só senha)* → digite a senha do
painel → salvar.

> **Uma coisa que não é defeito.** *Sair* apaga apenas a sessão do painel. Sua sessão no provedor de
> identidade continua aberta, então o clique seguinte no botão de SSO pode entrar direto, sem pedir
> nada. Logout federado não existe nesta versão.

---

## O que é conferido na volta

Quando o provedor devolve o navegador, o painel recusa na primeira coisa que não fecha, e toda
recusa produz **a mesma mensagem na tela**. Dizer se foi o estado ou a lista de autorizados também
diria ao atacante até onde ele chegou; o motivo real vai para o log.

| Conferido | Por que está na lista |
|---|---|
| Teto de tentativas por endereço | as duas rotas novas são públicas, então levam o mesmo teto do formulário |
| Cookie de estado presente e com assinatura íntegra | sem a assinatura, quem escreve os dois lados casa sempre |
| Estado da URL igual ao do cookie | é o que impede o CSRF de login — ser autenticado na conta do atacante |
| Estado ainda não usado | além do cookie, o servidor lembra o que consumiu: uso único de verdade |
| Sem erro, e com código presente | |
| Código trocado com prova de posse da chave | o código passa pela barra de endereços e fica no histórico; a chave nunca sai do cookie |
| `iss` igual ao issuer configurado | um provedor por vez, então há uma única resposta certa |
| `aud` contendo o ID do cliente | um token legítimo emitido para OUTRO serviço não pode valer aqui |
| Não vencido, e emitido há menos de cinco minutos | tolerância generosa transforma prazo em decoração |
| Nonce igual ao do cookie | amarra o token àquela ida específica |
| As informações do usuário e o token descrevem o mesmo sujeito | prova que a troca aconteceu de verdade |
| E-mail confirmado pelo provedor | senão um endereço não verificado basta para entrar |
| E-mail ou domínio na lista de autorizados | |

Só então a sessão é emitida — **o mesmo cookie assinado** que o formulário de senha emite, com a
mesma validade de oito horas e as mesmas propriedades. Não existe um segundo tipo de sessão.

A página de pouso é uma página de verdade com um redirecionamento dentro, e não um redirecionamento
HTTP: navegadores não carregam um cookie estrito de mesmo site por uma cadeia de redirecionamento
que começou em outro site, e você aterrissaria de volta no formulário com uma sessão perfeitamente
boa no bolso. O destino é sempre a raiz do painel — nenhum parâmetro da URL vira destino.

Cada uma dessas recusas tem um teste que a exercita com entrada ruim, em
`tests/test_sso_oidc.py`.

---

## SAML 2.0 não está nesta versão

A aba existe, os campos existem, e estão desabilitados. É um limite técnico, não uma tarde de
trabalho que faltou.

O SAML assina sobre *Exclusive XML Canonicalization 1.0*. A biblioteca padrão do Python também
canonicaliza XML — mas em C14N 2.0, outro algoritmo, o que faz toda assinatura válida parecer
inválida. A biblioteca padrão também não tem verificação RSA nenhuma, e o analisador de XML dela não
se defende de *signature wrapping*, o ataque específico deste protocolo, em que a asserção assinada
é movida para outro ponto do documento e uma falsa ocupa o lugar lido.

Escrever isso à mão produz uma validação que parece funcionar e aceita asserção forjada em silêncio.
Então o suporte a SAML espera uma biblioteca: `python3-saml` está declarado no `pyproject.toml` como
extra opcional `saml`, e a imagem precisará de `libxml2`, `libxslt` e `xmlsec` antes que ele possa
ser ligado. Até lá o painel recusa ligá-lo, e diz isso.

Também fora de escopo nesta versão, por decisão: logout federado e asserção SAML criptografada.

---

## Onde cada coisa fica guardada

| O quê | Onde | Por que ali |
|---|---|---|
| Issuer, ID do cliente, endereço público, listas de autorizados | banco de preferências do painel, chaves com prefixo `sso.` | nada disso é segredo; é a mesma tabela do idioma |
| Segredo do cliente | arquivo de permissão `0600` no diretório de dados | segredo não mora em banco que a tela lê |
| Qual provedor está ativo | preferências, um por vez | dois provedores ao mesmo tempo é como a resposta de um passa por outro |
| O estado da ida | cookie assinado de vida curta, mais uma lista de estados consumidos em memória | morre em dez minutos e sozinho não dá acesso a nada |

O diretório de dados é o mesmo que o resto do painel já usa — veja `DATA_DIR` em
[Configuration](Configuration).
