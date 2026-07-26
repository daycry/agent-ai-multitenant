---
title: "ADR 0131: Cómo llega la credencial OAuth de un MCP remoto al sandbox"
status: proposed
date: 2026-07-26
deciders: [operador]
relates_to: [0012, 0052, 0093, 0127, 0128]
---

# ADR 0131: Cómo llega la credencial OAuth de un MCP remoto al sandbox

## Contexto

El ADR 0127 entregó el conector OAuth 2.1 genérico para MCP remotos: descubrimiento,
registro dinámico de cliente, PKCE, y el token guardado en Vault bajo
`secret/data/mcp-oauth/{tenant}/{proyecto}/{servidor}`. El operador pulsa «Conectar» en
el panel, completa el consentimiento en el navegador y el servidor queda conectado.

`task_wf_12` (remediación 2026-07-25) cerró el tramo que faltaba **por el lado de la
plataforma**: el dispatch —el único que conoce tenant y proyecto— serializa un
`oauth_ref` con la ruta de Vault dentro de la petición del run
([dispatch.py:792-796](apps/orchestrator/src/orchestrator/dispatch.py#L792-L796)), el
runtime lo mapea a `MCPServerConfig.oauth_ref` y `build_oauth_auth` construye el
`httpx.Auth` que la sesión MCP necesita.

**Pero el último salto no existe.** Para desreferenciar ese puntero el runtime necesita
un resolver de Vault, y lo construye leyendo `AGENT_VAULT_TOKEN`
([`__main__.py:310`](docker/agent-runtimes/agent-runtime/agent_runtime/__main__.py#L310)).
Esa variable **no se fija en ningún punto del repositorio**: solo se lee. Sin ella,
`build_oauth_auth` lanza `MCPAuthError`
([mcp_tools.py:104-108](docker/agent-runtimes/agent-runtime/agent_runtime/mcp_tools.py#L104-L108)).

O sea: un servidor MCP remoto con OAuth se conecta desde la UI y **no funciona dentro de
un run**, que es justamente el caso para el que se diseñó.

### Por qué no basta con fijar la variable

Porque un token de Vault no es un secreto: es **la llave del almacén de secretos**. Con
él, código no controlado dentro del sandbox puede leer todo lo que la política del token
permita —potencialmente secretos de otros proyectos o de otros tenants— y no caduca con
el run. El principio 2 de `CLAUDE.md` («los workers no ejecutan código del usuario;
lanzan contenedores efímeros… sin socket Docker, sin credenciales») existe exactamente
para esto.

Conviene separar dos cosas que se confunden con facilidad:

|                        | Qué es                      | Alcance                  | Vida             |
| ---------------------- | --------------------------- | ------------------------ | ---------------- |
| **Token de Vault**     | credencial _del almacén_    | lo que diga su política  | larga, renovable |
| **Access token OAuth** | credencial _de un servidor_ | un servidor, un proyecto | corta, caduca    |

Inyectar lo segundo en el sandbox **no** contradice el principio 2, y de hecho es lo que
la plataforma ya hace. Inyectar lo primero sí.

### El precedente ya está en el código

Este problema está resuelto dos veces, y las dos con la misma forma: **el privilegio se
queda en el worker**.

- **Git**: el worker comitea y empuja; el sandbox no tiene credenciales de git —
  «_The WORKER does this — the sandbox has no git credentials (principle 2)_»
  ([execution.py:687](apps/workers/src/workers/execution.py#L687)).
- **Credencial del LLM**: el worker la resuelve contra Vault **con su propio token**
  (`WORKERS_VAULT_TOKEN`, [execution.py:290-311](apps/workers/src/workers/execution.py#L290-L311))
  y al contenedor le llega el spec ya resuelto, no un puntero.
- **`stack_exec`** (ADR 0093): cuando el agente necesita algo que el sandbox no puede
  hacer, se lo **pide al worker**.

La vía MCP es la única del sistema que espera que el sandbox tenga una llave del almacén
y desreferencie el puntero él mismo. Esa asimetría es el fallo de diseño, no la variable
sin fijar.

### La complicación propia de OAuth

Una clave de API estática se inyecta y ya está. Un token OAuth **caduca y se refresca**, y
el refresco produce un token nuevo que hay que **volver a guardar**. Por eso el diseño
original usa un `VaultTokenStorage` de lectura _y escritura_: el proveedor OAuth necesita
leer el access token, detectar el 401/expiración, canjear el refresh token y persistir el
resultado. Cualquier opción que ignore esto rompe los runs largos.

### Qué pasa hoy, mientras tanto

El fallo está **contenido y es visible**: `_wire_mcp_servers` captura por servidor, así
que el resto de servidores del proyecto siguen conectando, y desde `task_wf_14` el fallo
llega al preámbulo del agente además del log. No es una rotura silenciosa; es una función
que no entrega. Eso permite decidir esto con calma, pero no lo convierte en aceptable.

## Opciones

### A — El worker resuelve e inyecta el access token

El worker lee el estado OAuth de Vault antes de lanzar el contenedor y pasa el access
token ya resuelto en el env del run, igual que la credencial del LLM.

- ✅ Idéntico al precedente; cambio pequeño; el sandbox nunca ve Vault.
- ❌ **No resuelve el refresco.** Un run que empieza con un token a 10 minutos de caducar
  se queda a medias, y el token refrescado nunca se persiste. Mitigable refrescando antes
  de lanzar, pero un run puede durar horas y el problema reaparece.
- ❌ Si además se inyectara el refresh token para que el runtime pudiera refrescar, el
  sandbox pasaría a tener la credencial de larga duración: peor que el access token.

### B — Un token de Vault hijo, con política restringida y TTL del run

El worker emite un token hijo limitado a `secret/data/mcp-oauth/{tenant}/{proyecto}/*`
con TTL igual al presupuesto del run, y lo inyecta como `AGENT_VAULT_TOKEN`.

- ✅ Funciona sin tocar nada del diseño actual: lectura, refresco y escritura siguen igual.
- ✅ El radio de daño se acota al proyecto y al run.
- ❌ Sigue metiendo **una credencial del almacén** en el contenedor no confiable. Acota el
  principio 2 en lugar de respetarlo, y crea el precedente de «un Vault pequeñito es
  aceptable» que la próxima feature ampliará.
- ❌ Añade gestión de tokens hijos (emisión, revocación al terminar, política por proyecto).

### C — La credencial se media por el worker, como `stack_exec` _(recomendada)_

El runtime no habla con Vault. Pide el access token vigente al API interno que **ya tiene
cableado** (`agent_internal_api_url`, `_build_internal_api()`), y el lado plataforma hace
lo privilegiado: leer Vault, refrescar si toca y persistir el token nuevo. En el runtime,
`VaultTokenStorage` se sustituye por un storage HTTP de solo lectura que siempre devuelve
un token vigente.

- ✅ El sandbox no tiene ni token de Vault ni refresh token: **solo un access token de
  vida corta**, que es el mínimo necesario. Estrictamente mejor que A y que B.
- ✅ Resuelve el refresco de verdad, y en el único sitio que puede persistirlo.
- ✅ Misma forma que ADR 0093 y que el resto del sistema: el privilegio se queda fuera.
- ✅ Deja registro por-servidor de cada entrega de credencial, auditable.
- ❌ Más trabajo: un endpoint interno nuevo (autenticado por la identidad del run, con
  `oauth_ref` validado contra el tenant/proyecto de ESE run — si no, es una vía para leer
  credenciales ajenas) y una implementación de storage en el runtime.
- ❌ El refresco pasa a depender del API interno; si no responde, la sesión MCP cae. Es el
  mismo acoplamiento que ya tienen el resto de tools mediadas.

## Decisión

**Pendiente.** Recomendación: **C**.

Es la única que respeta el principio 2 en su letra y a la vez resuelve el refresco, que es
el problema real. A es más barata pero deja el bug a medio arreglar y empuja hacia meter
el refresh token en el sandbox. B funciona hoy y erosiona la frontera que sostiene todo el
aislamiento.

Si el coste de C no cabe ahora, **A es un paso intermedio honesto** siempre que se
documente que los servidores OAuth no sobreviven a runs largos y que **no** se inyecte el
refresh token. Lo que no debería hacerse es B: es la que parece un atajo pequeño y es la
que mueve la frontera.

## Consecuencias

- Mientras no se decida, un servidor MCP remoto con OAuth **queda sin conectar dentro de
  los runs**, con motivo explícito en el log y en el preámbulo del agente. El resto de
  servidores del proyecto no se ven afectados.
- Con C, `AGENT_VAULT_TOKEN` y `_build_mcp_vault_resolver` desaparecen del runtime, y con
  ellos la única expectativa de que el sandbox tenga credenciales de plataforma. El
  `auth_ref` de ADR 0052 (claves estáticas) debería seguir el mismo camino.
- El test humano del handshake OAuth real sigue necesitando navegador: es el riesgo
  residual (c) que el propio ADR 0127 declaró.
