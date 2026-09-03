---
adr_id: "0166"
title: "Las tools de un servidor MCP llegan al catálogo sin un paso manual (enmienda de los ADR 0052 y 0100)"
status: proposed
date: 2026-09-03
authors: [claude-opus-5, operador]
plan_referenced: remediacion-marketplace-mcp-2026-09-02
amends: ["0052", "0100"]
related: ["0019", "0048", "0049", "0101", "0127", "0128", "0131", "0142"]
docs_language: es
---

# ADR 0166 — Las tools de un servidor MCP llegan al catálogo sin un paso manual

> **Estado: `proposed`.** Lo acepta el operador, no el redactor.
>
> **Este ADR contradice DOS decisiones `accepted`** y lo dice de frente:
>
> - El [ADR 0052](0052-import-mcp-tools-catalogo.md) eligió el 2026-06-03 la
>   **importación manual (P-A)** y **rechazó expresamente el auto-import (P-B)
>   «por meter ruido/superficie no querida»**. Aquí se enmienda esa alternativa
>   rechazada. **No lo supersede**: del 0052 sobrevive todo lo que era su
>   aportación real —el namespacing `<server>.<tool>`, la fila `Tool` como ancla
>   única de gobernanza, `security_level='sandboxed'` editable, la faceta
>   Origen=MCP y el threading al runtime— y se deroga **sólo** el requisito de
>   que un humano elija las tools una a una antes de que existan.
> - El [ADR 0100](0100-materializacion-marketplace.md) manda **expresamente** lo
>   que §D6 cambia: su pieza 2 ordena materializar «`kind=tool`/**`mcp_server`**
>   cuyo `implementation_type` resuelto sea `mcp_tool`… → upsert de la fila
>   `Tool`». D6 retira esa rama para `kind=mcp_server`. No es un detalle de
>   implementación que se pueda cambiar en silencio: es una decisión aceptada
>   que hay que enmendar por escrito.
>
> Qué párrafos concretos caen y qué hay que escribir en cada uno de los dos ADR
> en el MISMO commit que acepte éste: §«Qué queda derogado del ADR 0052» y
> §«Qué queda derogado del ADR 0100».

## Contexto

### El dead end, medido

Hoy un servidor MCP declarado cuyas tools nadie importó es un **callejón sin
salida silencioso**. Nada falla; simplemente no ocurre nada.

Lo que hace que el modelo vea una tool MCP es exclusivamente
`_project_mcp_tool_rows` (`apps/api-server/src/api_server/agent_tools_enforcement.py:250-289`):
filas `Tool` vivas **del tenant**, con `implementation_type='mcp_tool'`, cuyo
prefijo antes del primer punto coincide con un `name` declarado en
`project.mcp_servers`, filtradas después por `mcp_tool_roles`
(`filter_mcp_tools_by_role_policy`, `agent_tools_enforcement.py:351`). De esa
misma función derivan **las dos** mitades: lo que el run puede llamar
(`resolve_project_mcp_tool_names`) y lo que al modelo se le cuenta
(`serialize_project_mcp_tool_specs`, `:312`). **Sin fila `Tool` no hay ni
permiso ni anuncio.**

Al otro lado, el runtime sí conecta el servidor y registra sus tools
(`agent_runtime/mcp_tools.py`). O sea: la sesión MCP se abre, la tool existe en
el proceso, y el modelo nunca se entera de que existe. El operador ve «servidor
guardado, conexión OK, 12 tools» y el agente contesta que no tiene esa
herramienta. Es el hallazgo **MK-02 (ALTO)** de la auditoría del 2026-09-02
([plan de remediación](../roadmap/remediacion-marketplace-mcp-2026-09-02.md)).

Y el paso que lo cerraría está escondido detrás de tres puertas:

1. **No existe endpoint de «guardar servidor MCP».** Los servidores se
   persisten en `projects.mcp_servers` (JSONB) vía `POST /projects`
   (`routers/projects.py:395,452`) y `PUT /projects/{id}`
   (`routers/projects.py:553`, por `apply_partial_update`), que es lo que llama
   el panel (`mcp-servers/page.tsx:110-114`). El guardado de un servidor MCP es,
   literalmente, un update genérico de proyecto: ese dato es el que decide §D1.
2. **El botón «Importar» sólo vive dentro del diálogo de edición.**
   `McpConnectionTestSection` se monta únicamente en `mcp-server-dialog.tsx:326`.
   La tarjeta del servidor (`mcp-server-card.tsx`) no dice cuántas tools hay
   importadas, ni si hay cero. El estado invisible es el que nadie arregla.
3. **Son dos viajes, y en un servidor nuevo el segundo da 404.**
   `ImportMcpToolsRequest.tool_names` exige `min_length=1`
   (`routers/mcp.py:298`), así que hay que probar conexión primero para tener
   nombres que enviar; y `import_mcp_tools` exige que el servidor esté **ya
   declarado** en `project.mcp_servers` (404 si no, `routers/mcp.py:309-350`).
   En el diálogo de alta —donde el botón está— el servidor todavía no está
   guardado.

A eso se suma un segundo escritor que **no tiene dedo con el que pulsar**: el
despliegue del marketplace ([ADR 0142](0142-marketplace-despliegue-tres-capas.md))
escribe la entrada en `projects.mcp_servers` y la política `mcp_tool_roles`
(`marketplace/deploy.py:379-460`) y, al no encontrar filas `<server>.*`
(`_namespaced_tool_names`, `deploy.py:480`), **se rinde con un aviso**: «tras
importarlas, vuelve a desplegar para aplicar el role_map» (`deploy.py:418-424`).
Una instalación de un clic que termina en «ahora vete a otra pestaña, pulsa dos
botones y vuelve a desplegar» es exactamente el «comprar sin recibir» que el
0142 existe para terminar.

### Qué ha cambiado desde el 2026-06-03

El 0052 no se equivocó: describía bien el mundo de junio. Han cambiado cuatro
cosas, y las cuatro erosionan la premisa del clic.

1. **La fila `Tool` dejó de ser la elección del operador y pasó a ser el
   cableado.** En junio, una fila `Tool` significaba «el operador asignó esta
   tool a este agente» (el gate por-agente de `agents.py:770-787`). El
   [ADR 0128](0128-tools-mcp-aportadas-por-proyecto-runtime.md) **fase 3**
   (2026-07-23) retiró ese gate: la allowlist MCP de un run **la aporta el
   proyecto**, y una fila de un servidor declarado queda disponible para
   cualquier agente que corra ahí, modulada sólo por `mcp_tool_roles`. El clic
   ya no expresa «qué puede usar este agente»: expresa «qué tools existen». La
   decisión de supply chain **subió de nivel**: hoy es la decisión de declarar
   el servidor.
2. **El [ADR 0101](0101-discovery-mcp-runtime.md) ya anticipó este documento.**
   Evaluó como opción **B** el «discovery automático opt-in por servidor» y lo
   calificó de «**aplazable como capa futura sobre A**», con una condición
   escrita: hace falta **lógica de reconciliación** (qué pasa cuando el servidor
   añade o quita tools). Este ADR es esa capa, y su parte nueva de verdad son
   precisamente las reglas de reconciliación que el 0101 echaba en falta.
3. **Llegó OAuth.** Los ADR [0127](0127-conector-oauth-generico-mcp-remotos.md) y
   [0131](0131-credenciales-oauth-mcp-en-el-sandbox.md) hicieron viables los MCP
   remotos, y el caso estrella (Atlassian) es justo el que **no se puede
   descubrir desde el api-server**: `discover_tools` no acepta credencial OAuth
   (§Obstáculo 1). Para ese servidor el flujo manual del 0052 no es fricción:
   es imposible.
4. **La auditoría midió el coste.** MK-02 ALTO, con su gemelo de UI (UI-03) y su
   consecuencia en el marketplace (el «vuelve a desplegar»).

### Los dos obstáculos duros

**Obstáculo 1 — un MCP con OAuth no se puede descubrir hoy desde el api-server.**
`MCPClient.connect` sí acepta un `auth: httpx.Auth | None`
(`packages/shared-mcp/src/shared_mcp/client.py:130-135`, ADR 0127), pero
`discover_tools` **no lo propaga** (`shared_mcp/discovery.py:68-102`: sólo
`vault_resolver`), y `_to_runtime_config` (`routers/mcp.py:463`) no lleva
`oauth_ref` — de hecho `MCPServerConfigModel` es `extra="forbid"`
(`api_server/mcp/config.py:45`), así que ni siquiera admitiría el campo. El
`oauth_ref` lo monta el dispatch para el run (`mcp_oauth_flow.serialise_servers_for_run`)
y el token lo resuelve el runtime contra `/internal/agent/mcp-oauth-token`
(`routers/internal_agent.py:222-280`).

Pero —y esto es lo que cambia la conclusión— **el api-server ya tiene toda la
mitad privilegiada**: `VaultTokenStorage`, `oauth_vault_path`,
`build_oauth_provider` (`shared_mcp/oauth.py:172-211`) e `issue_access_token`
(`mcp_oauth_flow.py:305`). Lo que falta no es arquitectura: es **una costura de
tres líneas** en `discover_tools`.

**Obstáculo 2 — la transacción del request.** `get_tenant_session` mantiene
abierta la transacción durante todo el handler. Meter una llamada de red de hasta
`timeout_s` (default 30 s, tope 300 s) **por servidor** dentro de
`update_project` reproduce literalmente el hallazgo **perf-2/db-2** que cerró
prod-13: retener una conexión del pool durante una espera de red agota el pool y
**toda** la API empieza a dar `TimeoutError`. Hay guarda viva que lo vigila para
el asistente (`tests/integration/test_assistant_no_tx_during_llm.py`), y su
docstring explica por qué la aserción va sobre `engine.pool.checkedout()` y no
sobre la firma del endpoint.

Un tercer dato, contraintuitivo, que decide una de las opciones: **el
egress-proxy gobierna el sandbox, no el api-server**. `HTTP_PROXY` se inyecta en
el contenedor de `agent-runtime` que lanza el worker (`workers/container.py:331`)
y en el sandbox del marketplace (`marketplace/sandbox.py:312`); ni el servicio
`api-server` ni el proceso `workers` lo llevan (grep = 0), y
`tests/integration/test_worker_mcp_noproxy.py` documenta la asimetría medida en
vivo: un MCP externo desde el run exige su host en la allowlist estática
(`docker/egress-proxy/filter.txt`, nueve hosts, ninguno Atlassian → MK-03, que
resuelve el ADR 0165, en redacción en paralelo). Es decir: **descubrir desde el
runtime está MÁS bloqueado que descubrir desde el api-server**, no menos.

Ese mismo dato tiene una segunda lectura, incómoda y necesaria, que este ADR no
puede escamotear: que el api-server salga **directo** a Internet no es una
virtud del diseño, es una asimetría que el plan clasifica como **defecto**
(MK-15) y que el propio repo tiene escrita como política en contra
(`docker/egress-proxy/filter.txt:31-33`). Se trata en §D10.

### La pregunta

**¿Cómo dejan de existir servidores MCP declarados cuyas tools el modelo nunca
ve, sin romper el control de supply chain que motivó P-A en el 0052?**

## Opciones consideradas

| Opción                                                                                                                                         | A favor                                                                                                                                                                                                      | En contra                                                                                                                                                                                                                                                                                                                                                                                                |
| ---------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **(a) Auto-import SÍNCRONO al guardar el proyecto** (la lectura literal de «sin pulsar nada»)                                                  | Cero fricción; el modelo mental «declaro el servidor → sus tools están» se cumple sin excepción.                                                                                                             | **Reproduce perf-2/db-2**: N servidores × hasta 300 s de red dentro de la transacción del request. `PUT /projects` es un update genérico: renombrar el proyecto redescubriría todo. Y un servidor caído impediría **guardar el proyecto**. Con OAuth, además, no funciona (obstáculo 1).                                                                                                                 |
| **(b) Auto-import ASÍNCRONO** (el guardado encola; el panel muestra «descubriendo…»)                                                           | Saca la red de la transacción; hay patrón y lane ya construidos (`marketplace/async_gates.py:93`, `_workers_marketplace_service` del instalador, `--concurrency=1`).                                         | Compra una máquina de estados y un poll en la UI. Y hereda la lección del `analyzing` **permanente**: un estado transitorio persistido cuyo único consumidor es una cola que quizá nadie drena (el stack de dev no levanta esa lane) se queda atascado para siempre. Sola, no basta.                                                                                                                     |
| **(c) Semi-automático de un clic**: el guardado no descubre, pero la tarjeta enseña el hueco y un botón hace discovery+import en un solo viaje | Conserva íntegro el control del 0052; cero riesgo de transacción; **elimina el dead end silencioso**, que es el defecto real; barato. Convierte «dos viajes y un 404» en «un botón donde se ve el problema». | Sigue habiendo un acto humano. No sirve para el despliegue del marketplace, donde no hay nadie mirando la pestaña MCP.                                                                                                                                                                                                                                                                                   |
| **(d) Descubrir desde el RUNTIME/worker** y persistir por el API interno                                                                       | Es el único sitio que hoy resuelve OAuth de facto; schema siempre fresco.                                                                                                                                    | El [ADR 0101 opción C](0101-discovery-mcp-runtime.md) ya rechazó la visibilidad runtime-side. Y el dato nuevo la remata: el contenedor de runtime está **detrás del egress-proxy**, así que para Atlassian estaría bloqueado hasta el ADR 0165, mientras que el api-server no lo está. Además el anuncio al modelo se construye worker-side **antes** de conectar. Coste alto para quedar más bloqueado. |
| **(e) Flag `auto_import` por servidor** (opción B del ADR 0101, literal)                                                                       | Explícito; el operador elige por servidor.                                                                                                                                                                   | Un booleano tiene que tener default, y con cualquiera de los dos la mitad de los operadores recibe lo que no quería **más** un knob que aprender. `MCPServerConfigModel` es `extra="forbid"`: añadir el campo es cambio de esquema y migración de intención. Configuración nueva para un problema que se resuelve con visibilidad.                                                                       |
| **(f) Statu quo**                                                                                                                              | Cero trabajo.                                                                                                                                                                                                | Deja MK-02 abierto y el marketplace vendiendo servidores MCP inertes. Inaceptable.                                                                                                                                                                                                                                                                                                                       |

## Decisión

**(c) como mecanismo base y suficiente, con (b) acotada a los llamantes que no
tienen humano delante, y con el estado siempre DERIVADO.** En una frase: **el
import deja de ser un paso que hay que descubrir; no deja de ser un acto.**

### D1 — No hay auto-import al guardar el proyecto

Se **rechaza (a)**. `POST /projects` y `PUT /projects/{id}` no abren sesiones MCP.
La razón no es doctrinal: es que la transacción del request está abierta y el
antipatrón está medido y vigilado (prod-13, perf-2/db-2). Guardar un proyecto no
puede depender de que un tercero conteste.

### D2 — Un solo viaje, y ofrecido donde se ve el hueco

- `POST /projects/{id}/mcp/servers/{name}/import-tools` pasa a aceptar
  `tool_names` **opcional**. Ausente = «todas las que el servidor anuncie
  ahora» (el discovery server-side ya ocurre dentro del endpoint,
  `routers/mcp.py:322-350`). Con lista = la multiselección del 0052, intacta.
- La **tarjeta** del servidor (`mcp-server-card.tsx`, fuera del diálogo) muestra
  «N tools importadas» o el badge «sin importar», con **Probar conexión** e
  **Importar** como acciones directas. El estado vacío de
  `mcp-tool-roles-section.tsx:197` enlaza a la causa en vez de decir sólo
  «pulsa Probar».
- La lógica de importar **se extrae** de `import_mcp_tools`
  (`routers/mcp.py:309-457`, hoy toda en línea) a una función reutilizable
  —`import_server_tools(session, project, server_name, …)`— porque a partir de
  aquí tiene tres llamantes. Un tercer copiado de esa lógica es la vía por la
  que el fail-closed del 0101 se pierde en uno de los caminos.

### D3 — Sí hay import automático, atado a un acto sobre ESE servidor

Se adopta **(b)**, acotada a los dos disparadores donde el operador ya decidió
algo sobre ese servidor concreto y no hay diálogo abierto:

1. **Despliegue desde el marketplace.** `_materialize_mcp_server` deja de
   rendirse con «vuelve a desplegar»: encola el import tras el commit
   (`schedule_after_commit` publicando en la lane `marketplace`, el mismo camino
   que `queue_install_gates`), y el `role_map` se aplica cuando las filas
   existen. **No** se hace en línea: el handler del deploy tiene la transacción
   abierta y `schedule_after_commit` drena en `close()`, o sea todavía dentro del
   request (`db/after_commit.py:139-155`) — sirve para publicar un mensaje de
   milisegundos, no para una red de 300 s.
2. **«Conectar» de OAuth completado** (§D5).

**El proceso donde corre, dicho con nombre.** La task va a **`apps/workers`**,
sobre la lane `marketplace` —la misma que drena `workers.marketplace_gates`, con
`--concurrency=1`— y hay que **añadirla al `include` de
`apps/workers/src/workers/celery_app.py`** (:140-162): sin ese import el worker
arranca sin registrar la task y los mensajes de la cola mueren con
`NotRegistered` mientras el productor devuelve 202, que es la lección escrita en
el comentario de esa misma lista. Tres dependencias que ese proceso cumple, y
que se dejan por escrito porque es exactamente donde §D5 se rompería si no
fuesen ciertas:

- **Vault.** La imagen de workers se construye SOBRE la de api-server
  (`ARG BASE_IMAGE`, ADR 0141) y `workers/marketplace_gates.py` ya importa
  `api_server`, así que `VaultTokenStorage`, `build_oauth_provider` y
  `oauth_vault_path` son importables tal cual. `oauth_vault_path` es
  **keyword-only** (`shared_mcp/oauth.py:61`): se llama
  `oauth_vault_path(tenant_id=…, project_id=…, server_name=…)`, y la ruta se
  deriva de esos tres, nunca se acepta montada.
- **RLS.** El worker corre con rol **BYPASSRLS**: no hay sesión de tenant que
  inyecte nada, así que **cada query lleva su `tenant_id` explícito** y ese
  `tenant_id` viaja en el mensaje, igual que en `marketplace_gates`. Una
  instalación de otro tenant no se encuentra.
- **Egress.** El worker **tampoco** tiene `HTTP_PROXY`: `container.py:331` lo
  inyecta en el contenedor de runtime que el worker lanza, no en el worker. Su
  postura de red es la del api-server, ni mejor ni peor — y eso es justo lo que
  obliga a escribir §D10.

Si alguna de esas tres dejara de ser cierta, la alternativa es mover el
disparador a una **background task del api-server** con la misma función
`import_server_tools` y una sesión propia fuera de la transacción del request.
Se prefiere la lane porque ya existe, tiene reintento y aísla la capacidad; una
background task muere con el proceso que la creó.

### D4 — El estado es derivado, nunca una columna

**No se añade ningún campo de estado** (`importing`, `discovery_status`,
`last_imported_at`…). «N tools importadas» es un `COUNT` de filas `Tool` vivas
con prefijo `<server>.`, el mismo criterio que `_project_mcp_tool_rows` y
`_namespaced_tool_names` ya usan.

Esta es la propiedad que neutraliza por construcción la lección del `analyzing`
permanente: **no hay estado transitorio que se pueda quedar atascado**. Si la
cola no se drena —el stack de dev no levanta la lane `marketplace`—, la tarjeta
sigue diciendo «sin importar» y el botón sigue funcionando. Regla general que
este ADR fija:

> **Toda ruta automática de import es un atajo sobre una ruta manual que
> permanece disponible y visible. Ninguna ruta automática puede ser la única
> forma de llegar al estado final.**

Y como cierre honesto del hueco, el **preámbulo del run** (donde ya se reporta
el fallo MCP, `tests/unit/test_mcp_failure_in_preamble.py`) dice cuando un
servidor declarado tiene **cero** tools importadas. Ahí sobrevive lo único
defendible de la opción (d): **el runtime informa del hueco; no lo cierra.**

### D5 — Los MCP con OAuth: el disparador es «Conectar», no el guardado

Se **propaga la capacidad, no el `oauth_ref`**:

- `discover_tools` gana un parámetro `auth: httpx.Auth | None` que **pasa tal
  cual** a `MCPClient.connect` (que ya lo acepta desde el ADR 0127). Es la
  costura mínima; ni un cambio de comportamiento para los llamantes actuales.
- El llamante privilegiado, cuando `uses_oauth(url)`, monta el proveedor con
  `build_oauth_provider` + `VaultTokenStorage(oauth_vault_path(tenant_id=…,
project_id=…, server_name=…))`. La ruta de Vault **se deriva en servidor** a
  partir de (tenant, proyecto, nombre), nunca se acepta montada — el mismo
  argumento de frontera de tenant que ya está escrito en
  `routers/internal_agent.py:238`.
- Si aún no hay token guardado (el operador no completó «Conectar»), el
  discovery falla **fail-closed con error tipado y accionable** («este servidor
  usa OAuth y no está conectado: pulsa Conectar»), no con un `AUTH_ERROR` crudo.
- **El import de un servidor OAuth se dispara al completar «Conectar»**, que es
  el momento en que ese servidor pasa a ser alcanzable. Guardar la entrada nunca
  lo fue.

**Respuesta explícita a la pregunta 1:** no se acepta que los servidores OAuth
exijan el clic, ni se descubren desde el contenedor de runtime. Se descubren
desde un proceso de plataforma que resuelve Vault —el api-server, o el worker de
la lane `marketplace` cuando el disparador es un despliegue (§D3)— en cuanto la
conexión existe. Lo que esa salida de red significa para la postura de
seguridad, y qué la condiciona, está en §D10.

### D6 — La fila `Tool` no namespaceada del marketplace se **retira**

`marketplace/materialize.py:205-256` crea hoy, por cada instalación de un
listing `mcp_server`, **una** fila `Tool` con `implementation_type='mcp_tool'` y
nombre **no** namespaceado (el del listing). Esa fila:

- es **invisible al runtime**, porque `_project_mcp_tool_rows` exige el prefijo
  `<server>.` (`agent_tools_enforcement.py:282-284`);
- representa un **servidor**, no una tool, así que ni siquiera es un objeto del
  tipo que dice ser;
- y tras el auto-import **convivirá** con las filas `<server>.*` en `/tools`,
  duplicando a ojos del operador lo que no duplica en el sistema.

Se elimina la rama que la crea. En su lugar, las filas namespaceadas que produce
un import disparado por un despliegue **estampan la procedencia** en las columnas
que ya existen (`source_listing_id`, `source_installation_id`, `source_version`),
con lo que `dematerialize_installation` retira exactamente lo que creó y se
cumple su contrato («la capacidad no puede sobrevivir a su permiso»). Sirve
además a la procedencia que pide UI-04/`task_mk_13`.

Esto **deroga una decisión `accepted`** del ADR 0100 y no un detalle de código:
lo que hay que escribir allí, en el mismo commit, está en §«Qué queda derogado
del ADR 0100».

> **Aviso de implementación, no cosmético:** `materialize.py:213` busca la fila
> previa con `select(...).where(Tool.source_installation_id == installation.id)`
> y `scalar_one_or_none()`. Con varias filas por instalación eso lanza
> `MultipleResultsFound`. Retirar la rama y estampar la procedencia son **el
> mismo cambio**, no dos. `_materialized_catalog_row` (`deploy.py:506-531`) tiene
> la misma forma; hoy **no** se llama para `kind=mcp_server` (`deploy.py:772-787`
> enruta ese kind a `_materialize_mcp_server`), así que no rompe — pero no puede
> reutilizarse para este kind sin la misma corrección.

### D7 — Idempotencia y borrado (la reconciliación que el ADR 0101 pedía)

- **R1 · Alta.** Tool anunciada y sin fila viva → upsert (el existente, por
  `(tenant, name)`; el índice único parcial `uq_tools_tenant_name` es el
  backstop).
- **R2 · Refresco.** Tool anunciada y con fila viva → se refrescan
  `input_schema` y `description` (ADR 0101). **El import automático NO toca
  `security_level`.** Hoy el endpoint lo sobreescribe siempre con el default del
  payload (`routers/mcp.py:440`), de modo que un re-import degradaría en
  silencio la elección de un operador que lo hubiera subido a `privileged` o
  bajado a `safe`. En el camino automático no hay elección humana que aplicar:
  se respeta la que hay, y sólo el import manual —donde el operador está mirando
  el selector— la fija.
- **R3 · Tool que el servidor deja de anunciar.** Se **soft-borra**, con dos
  condiciones acumulativas:
  1. **Sólo si `tool_names` está ausente.** La reconciliación es la contrapartida
     de «todas las que el servidor anuncie ahora»: cuando el operador manda una
     selección explícita —3 de 12, deliberadamente— lo no seleccionado **nunca**
     se retira. Un import parcial es una elección, no un inventario.
  2. **Sólo si el discovery terminó con éxito.** Nunca por uno fallido o
     parcial: así es como una caída transitoria de un tercero vaciaría un
     catálogo.

  Se justifica porque una tool que el servidor ya no anuncia es inejecutable por
  definición (el runtime no la registra → `unknown tool`); mantenerla es
  mantener una mentira que además se le cuenta al modelo en cada turno. Es
  reversible: el índice único es parcial sobre filas vivas, así que un re-import
  la resucita.

- **R4 · Servidor retirado del proyecto.** Se soft-borran sus filas `<server>.*`
  y se limpian sus claves de `mcp_tool_roles` — **salvo que otro proyecto vivo
  del mismo tenant declare un servidor con ese nombre.** Las filas `Tool` son
  **de tenant** y `mcp_servers` es **de proyecto**: sin esa comprobación, quitar
  un servidor `github` del proyecto A dejaría ciego al proyecto B.
- **R5 · Encolado.** El mensaje encolado en §D3 es idempotente por construcción,
  porque su efecto es el upsert de R1/R2 por `(tenant, name)`: una redelivery de
  Celery no duplica nada. El caso de carrera real es otro y hay que decirlo:
  **dos despliegues del mismo listing en dos proyectos del mismo tenant con el
  mismo `name` de servidor** producen dos tasks que compiten por las mismas
  filas, y hoy esa colisión sale por el `IntegrityError → 409` de
  `routers/mcp.py:448-452`. En la ruta asíncrona **no hay nadie a quien
  devolverle un 409**, así que la task lo **absorbe con reintento** (rollback y
  reencolado con backoff acotado): al segundo intento las filas ya existen y el
  upsert converge. Escribirlo en una auditoría que nadie lee, y dejar el
  despliegue a medias, sería el mismo dead end silencioso con otra cara. Que las
  dos tasks converjan a la **misma** fila es correcto por idempotencia y a la vez
  el síntoma del problema de fondo: §Decisión aplazada.
- Toda retirada por R3/R4 deja **entrada de auditoría** con la lista de nombres.

### D8 — Fail-open al guardar, fail-closed al importar

- **Guardar el servidor nunca depende de un tercero.** La entrada se escribe en
  `projects.mcp_servers` aunque el servidor esté caído, sin credenciales o sin
  egress abierto. Es la condición para que el operador pueda **registrarlo y
  luego pedir la apertura**.
- **El import es fail-closed** (ADR 0101, intacto): discovery caído → **cero
  filas** y error tipado (`401 AUTH_ERROR` / `502 TRANSPORT_ERROR`).
- **Precisión sobre el `input_schema` vacío, que hoy sí se produce y a
  propósito.** `routers/mcp.py:411-420` degrada al comportamiento histórico
  —`input_schema={}` más una descripción placeholder— cuando un nombre **pedido
  explícitamente** no está entre los anunciados, en vez de abortar el lote
  entero. Este ADR **no cambia esa degradación** del camino manual: es el
  contrato del 0052 para una selección explícita, y cambiarlo a omisión sería
  una decisión aparte con su propio efecto en la UI. Lo que sí es cierto, y es
  lo único que hacía falta afirmar, es que **el camino automático no puede
  producir esa fila**, porque su conjunto de nombres **es** el conjunto anunciado
  y `spec` nunca falta.
- **La frontera con el ADR 0165** (allowlist de hosts MCP remotos, en redacción
  para `task_mk_0a`): la distinción no es «bloquear o no», es **de quién
  depende la comprobación**. Una política **local y determinista** de la
  plataforma (¿está el host en la allowlist?) puede rechazar al guardar, porque
  la respuesta no la da un tercero y el mensaje puede ser accionable. Una
  dependencia de **un tercero** (¿contesta el servidor?) no puede bloquear
  nunca. Con una condición que el 0165 debe respetar y que aquí se deja escrita:
  **si el 0165 rechaza con 422 el guardado de un host no permitido, la vía para
  pedir la apertura no puede exigir que el servidor esté ya guardado.** Los dos
  ADR tienen que ser coherentes en el commit que los acepte.

### D9 — Límites

- **L1 · Número de tools por servidor: 200** (el `max_length=200` que ya tiene
  `tool_names`, `routers/mcp.py:298`, aplicado también al camino automático).
  Superarlo **no trunca**: el import automático **se abstiene** y pide selección
  manual con el número dentro del mensaje («el servidor anuncia 431 tools, por
  encima del límite de 200: elige cuáles»). Es la propiedad más limpia de este
  diseño: **el automatismo se apaga solo justo donde la objeción de «ruido y
  superficie» del 0052 empieza a ser cierta.**
- **L2 · `input_schema`: 32 KiB por tool** (JSON serializado). El schema viaja al
  anuncio del modelo (`workers/agent_tool_schemas.py`) y **se paga en tokens en
  cada turno**; es la misma familia de defensa que el `max_output_bytes` (tope
  1 MiB) que `MCPServerConfigModel` ya impone. La tool que se pasa **se omite**
  con aviso: importarla con `{}` recrearía el bug del 0101.
- **L3 · `Tool.name` es `String(120)` con único parcial por `(tenant, name)`,** y
  el nombre del servidor admite hasta 64 (`MCPServerConfigModel.name`). Hoy nadie
  valida la suma y un nombre largo revienta en la BD con `DataError` (500). El
  namespaced se valida a ≤ 120 y la tool que no cabe **se omite** con aviso.
  **No se trunca**: dos nombres largos del mismo servidor truncados colisionan en
  el índice único y el segundo se lleva un 409. Y **no se reutiliza
  `_dedupe_name`** (`materialize.py:101-121`), que resuelve esta misma clase de
  colisión en el marketplace con un sufijo determinista `-mkt-XXXXXX`: ese sufijo
  produciría un nombre que ya **no** es `<server>.<tool>`, y
  `_project_mcp_tool_rows` parsea exactamente ese formato
  (`agent_tools_enforcement.py:282-284`). Una fila renombrada así sería
  invisible al runtime — es decir, D6 otra vez, reintroducido por la puerta de
  atrás. Para las tools MCP el nombre **no es negociable**: o cabe, o se omite
  con aviso.
- **L4 · Servidores por proyecto:** hoy `validate_mcp_servers_payload` no pone
  tope, así que el peor caso del anuncio es N × 200. Este ADR **no inventa** ese
  número; deja anotado que, si hace falta, su sitio es
  `validate_mcp_servers_payload`, no el import.

### D10 — Lo que este ADR NO decide sobre el egress, y la condición que le pone a la ruta automática

§D5 hace que un proceso de plataforma abra una conexión HTTP **a una URL que
escribe el tenant**. Hay que decir lo que eso es, porque el repo tiene política
escrita en contra y el borrador de este documento la vendía como virtud:

- `docker/egress-proxy/filter.txt:31-33` dice, de las host tools del córtex, que
  «salen SIEMPRE por este proxy (**nunca conexión directa desde el api-server**),
  además del anti-SSRF de `api_server.cortex.web_safety`». El discovery MCP hace
  hoy exactamente lo contrario: sale directo, y `MCPServerConfigModel.url`
  (`api_server/mcp/config.py:63`) sólo valida longitud y coherencia con el
  transporte — **no pasa por `assert_safe_url`** (`cortex/web_safety.py`), que es
  la guarda que rechaza `169.254.169.254`, hosts internos e IPs literales para
  las tools web.
- El plan trata esa asimetría como **defecto** (**MK-15 · MEDIO**), no como
  característica, y **`task_mk_0a` la reserva expresamente al ADR 0165** («y si
  "Probar conexión" debe salir por el proxy (MK-15)»).

Por tanto:

1. **Este ADR no decide MK-15.** Que el discovery salga directo o por el proxy,
   y con qué validación de host, lo decide el **ADR 0165**. Aquí sólo se afirma
   lo que era verdad y relevante para descartar (d): que hoy el api-server **no**
   está detrás del proxy, y que por eso descubrir desde el contenedor de runtime
   estaría _más_ bloqueado, no menos. Eso es un dato del estado actual, no una
   preferencia arquitectónica.
2. **La ruta automática sin humano queda condicionada.** El disparador de §D3.1
   —despliegue del marketplace, donde **nadie mira la pantalla**— no se activa
   hasta que el **ADR 0165 esté `accepted`** y su validación de host se aplique
   también al camino de discovery (endpoint manual, task de la lane y flujo
   OAuth: los tres pasan por `import_server_tools`, que es la razón de extraerla
   en §D2). Mientras tanto, §D2 —el botón, con un humano que eligió ese servidor
   y ve el error— sigue siendo suficiente para cerrar MK-02, que es el hallazgo
   ALTO. El orden de la ola 0 del plan ya pone los dos ADR por delante de las dos
   implementaciones, así que la condición no añade espera.
3. **La regla de §D4 lo hace seguro de aplazar**: sin la ruta automática, la
   tarjeta sigue diciendo «sin importar» y el botón sigue funcionando. No hay
   estado a medias.

### Qué sustituye al control de supply chain del 0052

La pregunta 2, respondida en dos mitades. Lo que el clic **creía** proteger ya
lo protege otra cosa; lo que protegía **de verdad** se sustituye por tres
mecanismos.

**Lo que el clic no protegía (y quién sí lo hace):**

- Que una tool de terceros corra con privilegio → `security_level='sandboxed'`
  por defecto (ADR 0052, intacto) + aislamiento por contenedor.
- Que un agente cualquiera la llame → `mcp_tool_roles` (ADR 0128 fase 2, con
  editor en la fase 4) + intersección de allowlist
  ([ADR 0048](0048-fuente-unica-nombres-tool.md)).
- Que se ejecute sin supervisión → política de aprobación humana por categoría +
  guardrails `pre_tool`/`post_tool`.
- Que un servidor no autorizado sea alcanzable → `require_tenant_admin` para
  declararlo + la allowlist de egress que decida el ADR 0165 para los remotos.
- Que la salida reviente el contexto → `max_output_bytes` por servidor.

| Lo que el clic sí protegía           | Qué lo sustituye                                                                                                                                                                          |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Que no entren 400 tools de golpe     | **L1**: tope de 200 y **abstención** (no truncado) por encima → vuelve la selección manual                                                                                                |
| Que el catálogo no se llene de ruido | **D4/D6**: recuento visible por servidor, procedencia estampada, faceta Origen=MCP ([ADR 0049](0049-taxonomia-y-disponibilidad-de-tools.md)), y **R3/R4** que retiran lo que sobra        |
| Que el operador pueda decir que no   | **D2**: la multiselección sigue existiendo, y ahora además se puede **borrar** lo importado y **restringir por rol**; lo irreversible sería importar sin poder retirar, y R3/R4 lo cubren |

**Y el punto que zanja la pregunta:** la fila `Tool` sigue siendo el ancla única
de toda la gobernanza —`security_level`, categoría para el gate humano,
guardrails, allowlist, procedencia—. Eso es lo que el ADR 0101 defendió al
rechazar su opción C, y **este ADR no lo toca**. Lo único que cambia es **quién
pulsa**, no **qué controla la plataforma**.

### Qué queda derogado del ADR 0052

Al aceptar este ADR hay que editar el [0052](0052-import-mcp-tools-catalogo.md)
**en el mismo commit** (regla fina de `CLAUDE.md` §«Qué manda cuando dos
documentos se contradicen»):

1. **§Opciones consideradas, viñeta P-B** («Auto-import al guardar el server
   (todas las tools descubiertas). ✅ Cero fricción. ❌ Mete en el catálogo tools
   que el operador quizá no quiere; ruido y superficie no deseada.»): queda
   **enmendada**. La objeción sigue siendo válida como riesgo, y la respuesta es
   L1 + D4, no la prohibición.
2. **§Alternativas rechazadas, primera frase** («P-B (auto-import) por meter
   ruido/superficie no querida»): **derogada**. P-C (no persistir) sigue
   rechazada, y `safe` por defecto también.
3. **§Decisión, punto 1**: «ofrece "Importar N tools al catálogo" con
   multiselección» deja de ser el **único** camino; la multiselección se
   conserva como uno de tres.
4. Se añade al principio del 0052 una nota `> **Enmendado por el ADR 0166
(2026-09-03)**: …` para que quien lo abra no lea una decisión que ya no rige.

**La edición tiene guarda propia, y es estrecha.**
`tests/docs/test_adrs_tools.py` comprueba sobre el 0052 exactamente las dos
secciones que se tocan: frontmatter con **`status: accepted`** y
**`plan_referenced: 06.18-tools-overhaul`**, las cuatro secciones canónicas
(Contexto / Opciones / Decisión / Consecuencias), **≥ 2 opciones enumeradas** en
§Opciones y un enlace al plan 06.18 en el cuerpo. De ahí tres cosas que la
enmienda **no** puede hacer: bajar el `status` del 0052 (sigue `accepted`; lo que
cae es una alternativa, no el ADR), borrar la viñeta P-B (se **enmienda** en su
sitio; borrarla podría dejar el recuento de opciones por debajo del mínimo y,
peor, borraría la traza de qué se rechazó y por qué) y tocar `plan_referenced`,
que sigue apuntando al plan que lo originó.

**Todo lo demás del 0052 sigue `accepted` y vigente**: namespacing
`<server>.<tool>`, `category='mcp'`, `implementation_ref`,
`security_level='sandboxed'` editable, render con badge de origen, threading de
`project.mcp_servers` al runtime.

### Qué queda derogado del ADR 0100

El [ADR 0100](0100-materializacion-marketplace.md) está `accepted` y **manda
expresamente lo que §D6 cambia**. No basta con no citarlo: hay que enmendarlo,
en el mismo commit y por la misma regla.

1. **§Decisión (pieza 2), «Materializa ahora»** (líneas 86-87): «listings
   `kind=skill`; y `kind=tool`/**`mcp_server`** cuyo `implementation_type`
   resuelto sea `mcp_tool` o `http_endpoint` … Upsert de la fila `Tool`/`Skill`».
   Queda **enmendada para `kind=mcp_server`**: la instalación de un servidor MCP
   deja de producir una fila `Tool` con el nombre del listing. `kind=skill` y
   `kind=tool` —incluida una tool suelta con `implementation_type='mcp_tool'`,
   que sí **es** una tool— siguen exactamente igual.
2. **§Criterio de aceptación, punto 2** (línea 170): «un install de un tool
   `mcp_tool`/`http_endpoint` crea una fila `Tool` **invocable** (aparece en el
   allowlist del dispatch y el runtime la ejecuta por la vía MCP/HTTP
   existente)». Para `kind=mcp_server` esa frase **es falsa hoy** —la fila no
   lleva el prefijo `<server>.` y `_project_mcp_tool_rows` no la ve
   (`agent_tools_enforcement.py:282-284`)—, así que el criterio se reescribe: en
   un `mcp_server`, las filas invocables son las `<server>.*` que produce el
   import del despliegue, y la no-orfandad se comprueba sobre ellas.
3. **§Estado de implementación (2026-07-13)** (línea 186), donde está descrito el
   comportamiento vivo («tool/mcp_server con implementation_type de RED … ->
   Tool»): gana una nota fechada que diga que la rama `mcp_server` se retiró por
   este ADR y por qué.

**Qué retira `dematerialize_installation` a partir de ahora.** Su contrato —«la
capacidad no puede sobrevivir a su permiso»— **no cambia**, y su query tampoco:
sigue soft-borrando por `source_installation_id` (`materialize.py:259-305`). Lo
que cambia es **qué encuentra**: en vez de una fila con el nombre del listing,
las N filas `<server>.*` que estampó el import del despliegue. De ahí que retirar
la rama y estampar la procedencia sean el mismo cambio y no dos: sin el
estampado, `uninstall`/`revoke` dejaría **vivas todas las tools** de un servidor
cuyo permiso se acaba de revocar. Eso no sería una regresión menor: es
exactamente el fallo que el 0100 existe para impedir, agravado por el número de
filas.

**Lo que del 0100 sigue intacto**: el corte por `implementation_type` (los
`python_function`/`docker_command` siguen diferidos, ADR 0081 B/C), la migración
de provenance (0111) y sus tres columnas —que este ADR **usa más**, no menos—,
los gates sin sandbox y la des-materialización transaccional en
`uninstall`/`revoke`.

**`CLAUDE.md` no cambia.** Se ha comprobado: ninguno de sus principios habla del
import de tools MCP, y los que rozan el asunto —2 (aislamiento por contenedor),
9 (catálogo cerrado de providers LLM) y 11 (validación humana por categoría)—
quedan intactos. Que la comprobación se haya hecho es parte de la decisión.

## Consecuencias

**Mejora.** Desaparece la clase entera de fallo «servidor declarado, modelo
ciego, cero señales»: o hay filas, o la tarjeta dice que no las hay. El
despliegue del marketplace deja de terminar en «vuelve a desplegar» y cumple lo
que el ADR 0142 prometió. Un MCP con OAuth pasa de indescubrible a descubrible
en cuanto se conecta. El re-import deja de pisar la elección de
`security_level` del operador. Y `/tools` deja de tener una fila fantasma por
cada `mcp_server` instalado, con la ganancia menos visible y más importante:
`dematerialize_installation` pasa a retirar las filas que de verdad son la
capacidad.

**Complejidad.** Una función extraída con tres llamantes; un parámetro nuevo en
`discover_tools`; una task Celery más en una lane que ya existe (con su entrada
en el `include` de `celery_app.py`, sin la cual no se registra); reglas de
retirada (R3/R4) y de reintento (R5) que hoy no existen; y tres límites que hoy
tampoco, dos de los cuales (L2, L3) cierran fallos latentes que ya podían dar un 500. Más dos ADR aceptados que hay que editar en el mismo commit.

**Trade-offs.** Se acepta que el catálogo de un tenant crezca sin que un humano
apruebe tool a tool, a cambio de que ninguna capacidad declarada quede muerta en
silencio. Se acepta que el import automático dependa de una cola cuyo consumidor
puede no estar levantado, a cambio de que esa dependencia **no pueda atascar
nada** (D4). Se acepta borrar filas por reconciliación (R3/R4), a cambio de que
el borrado sea suave, auditado, reversible, condicionado a un discovery exitoso y
**nunca aplicable a un import con selección explícita**. Y se acepta que la ruta
sin humano llegue **después** del ADR 0165 (D10), a cambio de no ampliar la
superficie de salida directa a Internet antes de que alguien haya decidido cómo
se gobierna.

## Riesgos

| Riesgo                                                                                                                                                                       | Prob. | Impacto  | Mitigación                                                                                                                                                                                                                                                                                              |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **El discovery sale directo desde un proceso de plataforma hacia una URL que escribe el tenant** (SSRF: `169.254.169.254`, `postgres:5432`, hosts internos de `agentic-net`) | Media | **Alto** | **D10**: este ADR no decide MK-15; la ruta automática **no se activa** hasta que el ADR 0165 esté `accepted` y su validación de host cubra `import_server_tools`. Hoy `MCPServerConfigModel.url` no pasa por `assert_safe_url` y declarar un servidor es `require_tenant_admin` — contención, no cierre |
| Un servidor charlatán inunda el catálogo y el anuncio del modelo                                                                                                             | Media | Medio    | L1 (tope 200 con abstención, no truncado) + L2 (32 KiB por schema) + recuento visible por servidor (D4)                                                                                                                                                                                                 |
| R3 retira filas por un discovery «exitoso pero parcial» de un servidor con bug                                                                                               | Baja  | Alto     | Sólo se retira tras éxito completo **y** sólo cuando `tool_names` está ausente; soft-delete reversible; auditoría con la lista; un re-import las resucita (índice único parcial)                                                                                                                        |
| La lane `marketplace` no está levantada y el import automático nunca ocurre                                                                                                  | Media | Bajo     | D4: estado derivado, sin `importing` que se atasque; la tarjeta sigue diciendo «sin importar» y el botón manual sigue siendo suficiente                                                                                                                                                                 |
| La task nueva no se añade al `include` de `celery_app.py` → `NotRegistered` y mensajes muertos                                                                               | Media | Medio    | D3 lo deja escrito como parte del cambio; es la misma lección que el comentario de `celery_app.py:155-161` ya documenta para `workers.marketplace_gates`                                                                                                                                                |
| Dos despliegues del mismo listing en dos proyectos con el mismo `name` chocan en `(tenant, name)`                                                                            | Baja  | Medio    | R5: la task absorbe el `IntegrityError` con reintento acotado (en la ruta asíncrona no hay a quién devolver el 409 de `routers/mcp.py:448-452`); el upsert converge                                                                                                                                     |
| Dos proyectos del mismo tenant declaran un servidor con el MISMO nombre y distinta `url`                                                                                     | Baja  | Alto     | R4 no retira si otro proyecto vivo lo declara. **Pero el fondo no se cierra aquí**: las filas son de tenant y el proyecto B recibe descripción y schema importados por A — vector de inyección de prompt intra-tenant. Ver §Decisión aplazada                                                           |
| El import automático pisa el `security_level` que el operador subió                                                                                                          | Media | Alto     | R2: el camino automático **no** toca `security_level`; sólo el manual, y sólo porque ahí hay elección explícita                                                                                                                                                                                         |
| Un `<server>.<tool>` largo revienta con `DataError` (500)                                                                                                                    | Media | Bajo     | L3: validación a ≤120 con omisión y aviso; nunca truncado ni sufijado (lo primero colisiona en el índice único, lo segundo rompe el parseo de `<server>.<tool>`)                                                                                                                                        |
| D6 retira la fila del marketplace y rompe una asignación existente que apuntaba a ella                                                                                       | Baja  | Bajo     | Esa fila nunca fue ejecutable (invisible a `_project_mcp_tool_rows`): retirarla convierte un `unknown tool` en una ausencia honesta. Migración con soft-delete y changelog                                                                                                                              |
| El operador cree que «declarado» = «disponible» y no repara en los avisos de import parcial (L1/L2/L3)                                                                       | Media | Medio    | Los avisos se propagan a la puerta de despliegue (UI-02) y a la tarjeta; el preámbulo del run reporta cero tools importadas                                                                                                                                                                             |

## Alternativas rechazadas

- **(a) Auto-import síncrono al guardar el proyecto** — reproduce perf-2/db-2 con
  una llamada de red dentro de la transacción del request, convierte un servidor
  caído en «no se puede guardar el proyecto», y con OAuth ni siquiera funciona.
- **(b) pura, como único mecanismo** — obliga a un estado transitorio persistido
  cuyo único consumidor es una cola; la lección del `analyzing` permanente dice
  que eso se atasca. Se adopta sólo como atajo sobre (c), nunca como única vía.
- **(d) Descubrir desde el runtime/worker** — el ADR 0101 (opción C) ya lo
  rechazó por romper el catálogo cerrado, y el dato nuevo lo remata: el
  contenedor de runtime está detrás del egress-proxy y el api-server no
  (`tests/integration/test_worker_mcp_noproxy.py`), así que descubrir desde allí
  estaría **más** bloqueado. Sobrevive únicamente como **informe** en el
  preámbulo del run. (Que el proceso `workers` **sí** pueda alojar la task
  asíncrona no contradice esto: el worker no es el contenedor de runtime, no
  lleva `HTTP_PROXY` y no construye el anuncio del modelo — §D3.)
- **(e) Flag `auto_import` por servidor** (opción B del ADR 0101) — configuración
  nueva, con default equivocado para la mitad de los casos, y cambio de esquema
  en un modelo `extra="forbid"`, para un problema que se resuelve haciendo
  visible el hueco.
- **Superseder el ADR 0052 entero** — se rechaza. El 0052 aportó el namespacing,
  el ancla de gobernanza en la fila `Tool` y el `sandboxed` por defecto, y las
  tres cosas siguen siendo correctas y en uso. Marcarlo `superseded` haría creer
  que también caen. Se enmienda la alternativa rechazada, y punto. Lo mismo con
  el 0100: se enmienda **una rama** de su pieza 2, no su decisión.
- **Reutilizar `_dedupe_name` para las colisiones de nombre del import** — el
  sufijo `-mkt-XXXXXX` deja de cumplir `<server>.<tool>` y la fila se vuelve
  invisible al runtime: es el defecto de D6 reintroducido. Se omite con aviso
  (L3).
- **Truncar en silencio** cuando un servidor supera los límites — importar «las
  200 primeras» o recortar un nombre a 120 produce un catálogo que miente sobre
  lo que hay. La abstención con mensaje es más larga de leer y más corta de
  depurar.

## Decisión aplazada (sin `reopen_when:` mecanizado, a propósito)

**Las filas `Tool` de MCP son de tenant; los servidores, de proyecto.** Dos
proyectos del mismo tenant que declaren un servidor homónimo comparten las filas
`<server>.*`: la **ejecución** va a cada servidor real (el runtime usa la config
de su propio proyecto), pero el **anuncio** —descripción y `input_schema`— es el
que importó el otro. Es un vector de inyección de prompt intra-tenant, contenido
por ser admin-only, y con el auto-import pasa de rareza a rutina (es el mismo
caso que R5 absorbe con reintento: converger a la misma fila es correcto por
idempotencia y sintomático por diseño). El arreglo de fondo (namespacear por
`(proyecto, servidor)` o alcanzar las filas al proyecto) es cambio de esquema y
de `_project_mcp_tool_rows`, fuera del alcance de este ADR.

**No se declara `reopen_when:` en el frontmatter** porque
`tests/docs/test_adr_deferrals.py` exige que cada id apuntado **exista** en
`docs/roadmap/` y **cite de vuelta** al ADR; hoy no hay casilla para esto y un
`reopen_when:` apuntando al vacío rompe la suite. Si el operador quiere el
disparador mecanizado, el orden correcto es: abrir la casilla en el plan de
remediación citando a este ADR, y **entonces** añadir el campo.

**Tampoco se usa `rejects:`**, y por la misma clase de razón: ninguna casilla del
plan queda **sin objeto** por esta decisión. `task_mk_01` ya está escrita como
«implementar **lo que decida el ADR 0166**», así que no cambia de objeto sino de
contenido, y
`test_adr_precedence.py::test_a_rejected_task_is_closed_not_open` exigiría
cerrarla `[x]` con nota en negativo, que sería falso.

## Qué cambia en el plan de remediación

Este ADR **cierra `task_mk_0b`**, que es la casilla que lo encarga (redactar el
0166, enmendar el 0052 en el mismo commit y responder a MK-09…MK-12). Al
aceptarse, esa casilla se marca `[x]` con su nota de cierre.

Sobre `task_mk_01`, la única edición que este ADR obliga es en su cláusula de
**Test**: donde hoy dice «guardar/desplegar → filas `Tool` namespaceadas sin
pulsar nada», el **«guardar»** cae —§D1 lo rechaza por perf-2/db-2— y queda
«desplegar → filas sin pulsar nada» más «guardar → la tarjeta dice "sin
importar" y el botón importa en un viaje». La propia casilla ya preveía esto al
escribir «o el contrato que fije el ADR». Su enunciado y su lista de anclas
(extraer `import_server_tools`, la tarjeta, el estado vacío de
`mcp-tool-roles-section.tsx:197`) **no cambian**: son exactamente §D2.

Y hereda una dependencia nueva: por §D10, la mitad automática de `task_mk_01`
(el disparador del despliegue) no se activa hasta que `task_mk_0a`/ADR 0165 esté
`accepted`, que es el orden que la ola 0 del plan ya fija.

## Trazabilidad

- **Plan**: [`remediacion-marketplace-mcp-2026-09-02`](../roadmap/remediacion-marketplace-mcp-2026-09-02.md)
  — hallazgos MK-02 (ALTO), MK-09…MK-12, MK-15 y UI-03. **Cierra `task_mk_0b`**;
  lo implementa `task_mk_01` (con la edición de su cláusula de Test descrita
  arriba); coordina con `task_mk_0a`/`task_mk_02`.
- **ADR enmendados**: [0052](0052-import-mcp-tools-catalogo.md) (P-B, §Opciones y
  §Alternativas rechazadas) y [0100](0100-materializacion-marketplace.md)
  (pieza 2 «Materializa ahora» y criterio de aceptación 2, para `kind=mcp_server`).
  Los dos se editan en el mismo commit que acepte éste.
- **Ratificados y no tocados**: [0101](0101-discovery-mcp-runtime.md)
  (fail-closed del discovery y persistencia del `input_schema`; este ADR es su
  «capa futura sobre A»), [0048](0048-fuente-unica-nombres-tool.md),
  [0049](0049-taxonomia-y-disponibilidad-de-tools.md),
  [0128](0128-tools-mcp-aportadas-por-proyecto-runtime.md) (fases 2-4),
  [0127](0127-conector-oauth-generico-mcp-remotos.md),
  [0131](0131-credenciales-oauth-mcp-en-el-sandbox.md),
  [0142](0142-marketplace-despliegue-tres-capas.md),
  [0019](0019-egress-red-sandbox-agent-runtime.md).
- **Coordinar con el ADR 0165** (allowlist de hosts MCP remotos, en redacción
  para `task_mk_0a`; **todavía no existe** el fichero — hoy el ADR más alto del
  directorio es el 0164, por eso no se enlaza ni figura en `related`): §D8 fija
  la frontera entre política local (puede bloquear) y dependencia de terceros
  (no), y §D10 condiciona a su aceptación la ruta automática. Nota de método:
  `amends:` no lo lee ninguna guarda; es prosa. Lo que sí obliga es la regla de
  precedencia de `CLAUDE.md`, y por eso los párrafos derogados están enumerados
  arriba uno a uno.
- **Backend**: `apps/api-server/src/api_server/routers/mcp.py:285-457`
  (extraer `import_server_tools`, `tool_names` opcional, `auth` de OAuth,
  degradación a `{}` en :411-420, 409 en :448-452),
  `agent_tools_enforcement.py:250-289,312,351` (sin cambios: es el criterio que
  todo lo demás copia), `marketplace/deploy.py:379-460,480` (encolar en vez de
  rendirse) y `:506-531` (el `scalar_one_or_none` que no debe reutilizarse para
  `mcp_server`), `marketplace/materialize.py:205-256` (retirar la rama
  `mcp_server`, estampar procedencia) y `:259-305`
  (`dematerialize_installation`, sin cambio de query), `marketplace/async_gates.py:93`
  y `db/after_commit.py:101-155` (patrón de encolado),
  `mcp_oauth_flow.py:305,398-426`, `routers/internal_agent.py:222-280`,
  `cortex/web_safety.py` (el anti-SSRF que el discovery MCP hoy no usa, §D10).
- **Workers**: `apps/workers/src/workers/celery_app.py:140-162` (registrar la
  task nueva de la lane `marketplace`), `workers/marketplace_gates.py`
  (precedente de importar `api_server` desde el worker y de la disciplina
  BYPASSRLS), `workers/container.py:331` (dónde se inyecta `HTTP_PROXY`, y dónde
  no), `apps/installer/backend/src/installer_backend/compose_generator.py:1518`
  (`_workers_marketplace_service`, `--concurrency=1`).
- **Paquete compartido**: `packages/shared-mcp/src/shared_mcp/discovery.py:68`
  (parámetro `auth`), `client.py:130-135` (ya lo acepta),
  `oauth.py:61` (`oauth_vault_path`, keyword-only), `oauth.py:172-211`,
  `api_server/mcp/config.py:36-117` (límites de nombre y `url` sin anti-SSRF).
- **Frontend**: `app/admin/projects/[id]/mcp-servers/mcp-server-card.tsx`
  (recuento y acciones fuera del diálogo),
  `mcp-connection-test-section.tsx:85-106`, `mcp-server-dialog.tsx:326`,
  `mcp-tool-roles-section.tsx:197` (estado vacío que enlaza a la causa),
  `components/marketplace/available-capabilities-section.tsx:114-131`
  (propagar `warnings`, UI-02).
- **Infra**: `docker/egress-proxy/filter.txt:31-33` (la política escrita contra
  la conexión directa del api-server, §D10).
- **Tests que gobiernan este cambio**:
  `tests/integration/test_mcp_tool_import_and_threading.py` (extender:
  despliegue → filas sin pulsar nada; segundo import no duplica; discovery caído
  → cero filas y aviso; re-import no pisa `security_level`; R3 sólo tras éxito y
  sólo sin `tool_names`; R4 sólo si ningún otro proyecto declara el nombre),
  `tests/integration/test_assistant_no_tx_during_llm.py` (la guarda de
  transacción que justifica D1 y D3),
  `tests/integration/test_worker_mcp_noproxy.py` (la asimetría de egress que
  descarta (d)), `tests/unit/test_mcp_oauth_reaches_runtime.py`,
  `tests/unit/test_mcp_failure_in_preamble.py` (el informe del preámbulo, D4),
  `tests/docs/test_adrs_tools.py` (la guarda estrecha del 0052: `accepted`,
  `plan_referenced`, ≥2 opciones, secciones canónicas),
  `tests/docs/test_adr_precedence.py` y `tests/docs/test_adr_deferrals.py`
  (por qué este ADR no lleva `rejects:` ni `reopen_when:`).
