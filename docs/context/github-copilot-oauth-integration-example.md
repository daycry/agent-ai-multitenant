# GitHub Copilot OAuth Device Flow — ejemplo de integración (referencia para task_02_32)

> **Qué es esto.** Ejemplo de integración con GitHub Copilot vía OAuth Device
> Flow que el operador (System Admin) aportó como referencia para
> `task_02_32` de la Fase G (ADR 0017). Procede de **otro proyecto**
> ("Agent AI", en `c:\laragon\python\agent-ai\`) — sus rutas
> (`app/core/...`), endpoints `/api/providers/...` y nombres NO son de este
> repo. Es una **idea**, no una receta a copiar literal.

> **Nota (2026-05-25, ADR 0021).** Este documento es **referencia
> histórica**. La integración Copilot actual vive en
> `packages/shared-llm/src/shared_llm/providers/copilot.py` —
> `CopilotProvider` con `start_device_flow()` / `poll_device_flow()` /
> `authenticate_interactive()` + mint del JWT con margen de 60s. El
> agent-runtime usa el adaptador `CopilotModelClient` que envuelve ese
> provider. Las menciones de LiteLLM como "implementación hermana"
> ya no aplican: el catálogo cerrado de ADR 0021 sustituye al gateway
> abstracto.

## Claves a retener para nuestra implementación

- **Device Flow sin secret de app**: se usa el `client_id` público de GitHub
  CLI (`01ab8ac9400c4e429b23`). Flujo: `POST github.com/login/device/code` →
  `user_code` + `verification_uri`; el operador autoriza en el navegador; se
  hace _poll_ a `POST github.com/login/oauth/access_token` con
  `grant_type=urn:ietf:params:oauth:grant-type:device_code` hasta `success`.
- **El token OAuth NO se usa directo**: se intercambia por un **JWT efímero
  de Copilot** (~30 min TTL) vía `GET api.github.com/copilot_internal/v2/token`.
  El JWT se cachea en memoria y se re-intercambia cuando quedan <60 s.
- **Las llamadas al modelo** van a `api.githubcopilot.com/chat/completions`
  (API **OpenAI-compatible**) con headers que imitan a VS Code Copilot Chat
  (`User-Agent: GitHubCopilotChat/...`, `Editor-Version`,
  `Copilot-Integration-Id: vscode-chat`, …). Sin esos headers GitHub rechaza.
- **3 fuentes de token** (primera gana): env `GITHUB_TOKEN`, persistido en BD
  (cifrado), y el `hosts.json` de VS Code Copilot Chat.
- Cuota: Free/Individual tienen cuota mensual de tokens premium; conviene un
  fallback de modelo ante el 429.

## Cómo encaja en la Fase G

A diferencia del **Claude Agent SDK** (que es su propio runtime agéntico — ver
`claude-agent-sdk-integration-example.md`), **GitHub Copilot es un endpoint
`/chat/completions` request/response normal y corriente**. Eso encaja
**limpiamente** en nuestro protocolo `ModelClient.decide()` (una petición →
una respuesta), igual que el gateway LiteLLM.

Reparto esperado en `task_02_32`:

- **LiteLLM** y **GitHub Copilot** → implementaciones directas de `ModelClient`
  (petición → decisión). De hecho LiteLLM podría cubrir Copilot como backend;
  decisión de detalle de la Fase G (cliente directo vs. vía LiteLLM).
- **Claude Agent SDK** → el caso especial (su propio loop) — es lo que de
  verdad necesita pensarse / un ADR.

La parte no trivial de Copilot es el **flujo de auth** (Device Flow + exchange
a JWT + caché), no el cliente de chat. Ese flujo de auth probablemente quiera
una pantalla en el admin-panel (botón "Sign in with GitHub", `user_code`,
polling) y un sitio donde persistir el token cifrado (similar a Vault o a la
tabla `platform_settings`).

---

## Documento original aportado por el operador

# Integración con GitHub Copilot (OAuth Device Flow)

> Agent AI usa el **endpoint interno `copilot_internal/v2/token`** de GitHub
> para intercambiar un token OAuth de usuario por un **JWT efímero de Copilot**
> (TTL ~30 min, auto-renovado). El JWT se envía a `https://api.githubcopilot.com/`
> con headers que imitan a VS Code Copilot Chat, dándote acceso a los modelos
> incluidos en la suscripción (GPT-4o, GPT-4.1, Claude Sonnet via Copilot,
> o3-mini, etc.) sin necesidad de API keys propias.
>
> El token OAuth se obtiene vía **GitHub Device Flow** (sin secret de app —
> usamos el client_id público de GitHub CLI). Persiste en `.env` + DB; al
> reiniciar el servidor se reusa automáticamente.

## Prerrequisitos

1. **Cuenta GitHub con licencia Copilot activa** — Free, Individual, Pro, Pro+, Business o Enterprise.
2. **Browser disponible** para autorizar el dispositivo (cualquier dispositivo, incluso uno distinto al server).

No necesitas: crear una OAuth App propia, API key de OpenAI/Anthropic, ni tener VS Code (pero si lo tienes, su token se reutiliza automáticamente).

## Flujos de autenticación — 3 fuentes de token OAuth

| Fuente                                            | Cuándo                                                 |
| ------------------------------------------------- | ------------------------------------------------------ |
| `os.environ["GITHUB_TOKEN"]`                      | Set manualmente en shell o `.env`                      |
| DB `ProviderConfig.GITHUB_TOKEN` (cifrado Fernet) | Persistido tras device flow o UI                       |
| `hosts.json` de VS Code Copilot Chat              | VS Code instalado y logged in (auto-detección on boot) |

## Device Flow — paso a paso

### Paso 1 — Start

```bash
curl -X POST https://github.com/login/device/code \
  -H "Accept: application/json" \
  -d 'client_id=01ab8ac9400c4e429b23&scope=user:email'
```

Respuesta: `{user_code, verification_uri, expires_in, interval}`. El `client_id`
es público (GitHub CLI) — sin client_secret.

### Paso 2 — El operador autoriza

Abrir `https://github.com/login/device`, pegar el `user_code`, "Authorize".

### Paso 3 — Poll

```bash
curl -X POST https://github.com/login/oauth/access_token \
  -H "Accept: application/json" \
  -d 'client_id=01ab8ac9400c4e429b23&device_code=<from-step-1>&grant_type=urn:ietf:params:oauth:grant-type:device_code'
```

Estados: `pending` → seguir; `success` → llega `{access_token: "gho_..."}`;
`expired` / `denied` / `error` → abortar. Respeta el `interval` (GitHub puede
pedir slow-down).

Al recibir el `access_token`: persistirlo (memoria + .env + BD cifrada) e
invalidar el caché del JWT.

## Cómo funciona el JWT internamente

El token OAuth (`ghu_*`/`gho_*`/`ghp_*`) **NO** se usa directo en cada llamada.
Se intercambia por un JWT efímero (~30 min):

```python
resp = await client.get(
    "https://api.github.com/copilot_internal/v2/token",
    headers={"Authorization": f"token {oauth_token}", "Accept": "application/json", **_request_headers()},
)
# resp.json(): {token, expires_at, refresh_in, endpoints, sku, chat_enabled}
```

El JWT se cachea en memoria; cuando queda <60 s para caducar se re-intercambia.
Cada llamada al modelo:

```python
jwt = await get_copilot_jwt(github_token)            # cache hit típico
resp = await client.post(
    "https://api.githubcopilot.com/chat/completions",
    headers={"Authorization": f"Bearer {jwt}", **COPILOT_HEADERS, "X-Request-Id": str(uuid.uuid4())},
    json={"model": model, "messages": msgs, "stream": True, ...},
)
```

## Headers que imitan VS Code

Sin estos headers GitHub rechaza la request:

```python
COPILOT_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.24.0",
    "Editor-Version": "vscode/1.96.2",
    "Editor-Plugin-Version": "copilot-chat/0.24.0",
    "Copilot-Integration-Id": "vscode-chat",
    "Openai-Organization": "github-copilot",
    "Openai-Intent": "conversation-panel",
    "X-Request-Id": "<uuid-per-request>",
}
```

Es una superficie pseudo-VS-Code; si GitHub bumpea el contrato (~cada 6-12
meses) hay que actualizar los headers.

## Modelos (varía según plan)

`gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `o3-mini`, `claude-sonnet-4-6` (vía
Copilot, solo Pro+/Business/Enterprise), `claude-opus-4-7`, `gemini-2.5-pro`.

## Cuotas

Free/Individual tienen cuota mensual de tokens premium. Al agotarse, la
respuesta trae `limited_user_quotas` + `limited_user_reset_date`; conviene un
fallback automático a un modelo no limitado (`gpt-4o-mini`) — sin él, los runs
fallan con 429.

## Troubleshooting (resumen)

- **`No GitHub token found`** → device flow, o `GITHUB_TOKEN` en `.env`, o
  `hosts.json` de VS Code.
- **`403 — Copilot Chat is not enabled`** → el plan no incluye Chat
  (Code-only). Upgrade o usar otro provider.
- **`429 Rate Limit`** → cuota agotada; fallback a modelo no limitado.
- **`401` tras funcionar** → JWT caducado + OAuth token mal; limpiar caché /
  re-device-flow.
- **`Editor-Version` rechazado** → GitHub cambió el contrato; bumpear
  `COPILOT_HEADERS` a la versión actual de VS Code Copilot Chat.

## Endpoints (del proyecto de origen — referencia de superficie)

| Endpoint                       | Descripción                                |
| ------------------------------ | ------------------------------------------ |
| `POST /auth/device-flow/start` | Inicia device flow, devuelve `user_code`   |
| `POST /auth/device-flow/poll`  | Poll: pending / success / denied / expired |
| `POST /auth/logout`            | Borra el token (env + BD + .env)           |
| `GET /auth-status`             | Estado actual + info de plan/cuota         |
| `GET /models?provider=copilot` | Modelos disponibles                        |
| `GET /health/copilot`          | Healthcheck (latencia + estado)            |

---

_Documento aportado por el operador el 2026-05-22 como referencia para la Fase G del Plan 02._
