---
adr_id: "0165"
title: "Allowlist de hosts MCP remotos en el egress-proxy: quién manda, quién aplica y qué NO controla"
status: proposed
date: 2026-09-03
authors: [claude-opus-5, operador]
plan_referenced: remediacion-marketplace-mcp-2026-09-02
docs_language: es
---

# ADR 0165 — Allowlist de hosts MCP remotos en el egress-proxy

> **Estado: `proposed`.** Lo firma el operador. Lo pide **`task_mk_0a`** del plan
> [`remediacion-marketplace-mcp-2026-09-02`](../roadmap/remediacion-marketplace-mcp-2026-09-02.md)
> (hallazgos MK-03, MK-13, MK-14, MK-15), y quien lo **consume** es `task_mk_02`,
> cuya implementación el criterio de cierre nº5 del plan condiciona a que este ADR
> esté `accepted` **antes**. La distinción no es burocrática: `task_mk_0a` es la
> casilla que se cierra escribiendo esto, y `task_mk_02` la que se cierra
> implementándolo. Si se acepta, tres frases del plan hay que corregirlas en el
> mismo commit: ver §«Qué le pasa a `task_mk_0a` y a `task_mk_02`».

## Contexto

Un servidor MCP **remoto** (Atlassian, GitHub, cualquier SaaS con endpoint
`streamable_http` o `sse`) no llega hoy desde una ejecución. La cadena está
medida, eslabón a eslabón:

- El sandbox recibe `HTTP_PROXY`/`HTTPS_PROXY` apuntando al egress-proxy
  (`workers/container.py:329-332`). No es opcional: la red `agentic-agents` es
  `internal: true` (`docker-compose.yml`, §`networks`), así que el proxy es la
  **única** salida (ADR 0019, Principio Rector 2).
- El worker exime por `NO_PROXY` **sólo los hosts sin punto**
  (`workers/execution.py:223-233` + `:311-322`): un nombre de servicio del
  compose. La justificación está escrita en el propio código y es correcta —«la
  declaración del server en el proyecto ES la autorización»— pero por
  construcción **un FQDN sale obligatoriamente por el proxy**.
- tinyproxy corre con `FilterDefaultDeny Yes` (`tinyproxy.conf:29`). El filtro
  trae hoy **nueve** entradas —contadas, no estimadas:
  `grep -cvE '^\s*(#|$)' docker/egress-proxy/filter.txt` → `9`—, repartidas en
  dos bloques. **Siete** en el de proveedores LLM del catálogo cerrado (ADR
  0021): `^api\.anthropic\.com$`, los tres de Copilot (`api.githubcopilot.com`,
  `api.github.com`, `copilot-proxy.githubusercontent.com`), el patrón de APIM
  `^[a-z0-9-]+\.azure-api\.net$`, `^ollama\.com$` y el `^ollama(:[0-9]+)?$`
  in-stack. **Dos** en el de la web del córtex (ADR 0067):
  `^api\.search\.brave\.com$` y `^searxng(:[0-9]+)?$`. Cuatro proveedores, siete
  líneas — la aritmética importa porque de esas nueve, **dos son hosts sin
  punto** (`ollama`, `searxng`), que están ahí precisamente porque el
  agent-runtime enruta por el proxy también sus llamadas internas.
  **Ninguna es Atlassian.** `mcp.atlassian.com` muere con `403 Filtered`;
  `api.githubcopilot.com` sí está, pero por ser proveedor LLM, no por ser MCP —
  es una coincidencia, no un diseño.

Hasta aquí es el hallazgo MK-03 tal cual. Lo que lo convierte en una decisión de
arquitectura y no en «añadir una línea al fichero» son cuatro cosas que aparecen
al tirar del hilo. **No son hallazgos de este ADR**: son MK-13, MK-14 y MK-15 del
plan (`:88-99`), vueltos a medir aquí eslabón a eslabón porque la decisión sale
de su detalle, no de su titular.

**1. Cambiar la allowlist no es editar un fichero: es reconstruir una imagen
(MK-13).** El `Dockerfile` hace `COPY filter.txt /etc/tinyproxy/filter`, y **ni
el compose canónico (`docker-compose.yml:405-412`, `build: ./egress-proxy` sin
`volumes:`) ni el que genera el instalador** montan ese fichero como bind.
Aplicar un cambio exige `docker compose build egress-proxy && up -d
--force-recreate egress-proxy`, procedimiento ya documentado en
[`docs/03-guides/gotchas/agent-runtime-egress-blocks-in-stack-llm.md`](../03-guides/gotchas/agent-runtime-egress-blocks-in-stack-llm.md).
El coste real es bajo —el `COPY` es la última capa, el `apk add` está cacheado—
pero **no es un cambio en caliente**, y cualquier diseño que finja lo contrario
miente.

**2. Probar y ejecutar recorren caminos distintos (MK-15). Ésta es la asimetría
clave.** `POST /projects/{id}/mcp/test-connection` llama a `discover_tools`
(`routers/mcp.py:246`) **desde el api-server**, que vive en `agentic-net` +
`agentic-agents` y **no tiene `HTTP_PROXY`**. La prueba sale directa a Internet.
O sea: **«Probar conexión» puede salir en verde mientras el run muere con
`403 Filtered`**, y la UI pinta el error crudo
(`mcp-connection-test-section.tsx:135-141`). Un botón de prueba que prueba otro
camino que el de producción es peor que no tenerlo: produce confianza
injustificada, que es el modo de fallo nº1 de
[`verificar-antes-de-implementar.md`](../03-guides/verificar-antes-de-implementar.md).
Y no es un único call site: `import-tools` hace lo mismo (`routers/mcp.py:362`),
y `task_mk_01` va a abrir un tercero (descubrir al guardar).

**3. El api-server no puede aplicar nada, y no por falta de código.** No está en
`agentic-docker` (`compose_generator.py:1226` le da `["agentic-net",
"agentic-agents"]`; sólo los workers llevan las tres,
`compose_generator.py:1282`) y no tiene `DOCKER_HOST`. Darle acceso al daemon
para que reconstruya un proxy **contradice el ADR 0060 de frente**. Así que «el
panel aplica la allowlist» no es implementable sin abrir el agujero que el 0060
cerró.

**4. El instalador no ve la base de datos (MK-14).** `installer_backend` copia
`docker/egress-proxy/*` **byte a byte** desde su paquete
(`installer_backend.stack_assets`, guardado por
`tests/unit/test_installer_ships_stack_assets.py` — hoy las dos copias del filtro
son idénticas) y los escribe en `real_step_executor._generate_config:682-689`. El
paquete no importa `api_server`, y en `GENERATE_CONFIG` **todavía no hay base de
datos**: migraciones y siembra van después. La frase que MK-14 cita del enunciado
**anterior** de `task_mk_02` —«el instalador vuelca el platform setting al
filtro»— **no es implementable tal cual**; el plan ya la retiró del texto de la
tarea el 2026-09-03, y lo que queda abierto es la pregunta que `task_mk_0a`
plantea así: «qué fuente usa el instalador». La responde D8.

Y dos cosas que el diseño tiene que respetar sí o sí:

- **La copia doble.** El filtro vive en `docker/egress-proxy/filter.txt` **y** en
  `apps/installer/backend/src/installer_backend/stack_assets/egress-proxy/filter.txt`.
  Tocar una sola pone en rojo `test_installer_ships_stack_assets.py`, y con
  razón: la imagen que escanea Trivy en CI sale de `docker/`, la que corre una
  instalación sale de `stack_assets`.
- **El filtro es una regex ERE por línea** (`FilterExtended Yes`), evaluada
  contra el destino del CONNECT / el `Host:` (`FilterURLs Off`), con semántica
  invertida. Un host **sin escapar** convierte cada punto en comodín
  (`mcp.atlassian.com` casa `mcpXatlassianYcom`); un host **malicioso o
  descuidado** (`.*`) abre el proxy entero. Y los puertos de CONNECT están
  acotados por **dos directivas separadas**, `ConnectPort 443` y `ConnectPort
8443` (`tinyproxy.conf:35-36`) —no una directiva con lista—, que son
  **globales**: un host permitido en otro puerto sigue muerto, y ninguna línea
  del filtro puede cambiar eso ni para bien ni para mal (D3).

**La pregunta que este ADR responde**, y que `task_mk_0a` deja abierta: ¿quién
**manda** sobre la allowlist? ¿El platform setting es la fuente de verdad y
`filter.txt` su render, o el fichero sigue mandando y el setting es un espejo que
valida y da el mensaje accionable?

## Opciones consideradas

### (a) Platform setting como fuente de verdad + render de `filter.txt` + paso de aplicación explícito

El System Admin edita una lista de hosts en `platform_settings`. Un renderizador
la convierte en líneas ERE ancladas dentro de un bloque delimitado del
`filter.txt`, y un paso de aplicación explícito (rebuild + recreate) la pone en
vigor.

- ✅ Un sitio donde pedirlo, con RBAC, con validación y con mensaje accionable.
- ✅ El error del `403 Filtered` deja de ser un jeroglífico.
- ❌ Nace una **deriva estructural**: entre «guardado» y «aplicado» el setting
  dice una cosa y el proxy hace otra. Si el panel presenta el guardado como
  «permitido», mentirá en verde con toda la buena fe del mundo.
- ❌ El paso de aplicación **no lo puede disparar el panel** (ADR 0060). Tiene
  que salir de la máquina, por CLI o runbook.
- ❌ El instalador no puede consumirlo (§Contexto 4): al instalar, el setting
  todavía no existe.

### (b) `filter.txt` sigue mandando; el setting no existe; el api-server valida contra el fichero

Cero superficie nueva. El operador edita las dos copias del filtro en el repo, lo
despliega, y lo único que se añade es que el api-server lea ese fichero y dé un
error accionable en vez del `403` crudo.

- ✅ **Cero deriva posible**: hay un solo documento.
- ✅ La auditoría es la que ya existe y es la mejor que hay: el historial de git
  del fichero, con su PR y su revisor.
- ❌ **El api-server no tiene ese fichero.** Su imagen no lleva `docker/`, y en
  una instalación el filtro vive en `{compose_dir}/stack/egress-proxy/` **en el
  host**. Hacerlo legible exige un bind read-only nuevo hacia el api-server: o
  sea, el coste de la opción (c) pagado en otro contenedor y sin su beneficio.
- ❌ Abrir un host exige un ciclo de repo: rama, PR, merge, despliegue. Para un
  tenant que quiere Atlassian el martes, eso es el equivalente operativo de «no».
- ❌ No hay dónde registrar _quién lo pidió_ — sólo quién lo mergeó.

### (c) Bind mount del filtro (`./stack/egress-proxy/filter`, read-only)

El fichero deja de ser entrada de build y pasa a ser dato. Aplicar = `docker
compose restart egress-proxy` (~1 s) o un `SIGHUP`, no un rebuild.

- ✅ Es el **final de trayecto correcto**: una allowlist específica del sitio no
  pertenece a una imagen. Y si algún día se publican estas imágenes (pregunta 6
  del ADR 0161), tenerla horneada dentro es directamente inviable.
- ✅ Hace implementable la validación de (b) sin inventar nada: el mismo bind,
  read-only, hacia el api-server.
- ❌ **Cambia el contrato de build del servicio.** Si el `Dockerfile` deja de
  hacer `COPY`, cambia lo que CI construye y escanea; si lo mantiene, hay dos
  verdades (la horneada y la montada, ganando la montada en silencio).
- ❌ **Prejuzga la pregunta 6 del ADR 0161**, que está explícitamente sin firmar
  y que arrastra dos guardas de `tests/unit/test_infra_images_are_scanned.py`
  (`:232-242` exige que el generador los emita con `build:`; `:264-282` que un
  servicio construido localmente no se haga `pull`). El propio 0161 avisa de que
  moverlas «no es un cambio de una línea».
- ❌ Un bind de **fichero suelto** se rompe si el escritor sustituye el inodo en
  vez de reescribir en sitio — trampa clásica, y aquí el escritor sería un script
  nuevo.

### (d) Derivar el filtro en caliente de `projects.mcp_servers`

Cualquier proyecto que declare un MCP remoto abre su host automáticamente.

- ✅ Cero fricción; probar y ejecutar coinciden por construcción.
- ❌ **Escalada de privilegios entre fronteras**: `projects.mcp_servers` lo
  escribe un `tenant_admin`. Convertiría un control **de plataforma** en uno **de
  tenant**: el tenant A abre egress hacia un host que también alcanzan los
  sandboxes del tenant B. Choca de frente con el Principio Rector 1.
- ❌ «En caliente» no existe: el proxy es estático (§Contexto 1). Haría falta (c)
  **más** un escritor con acceso al daemon, o sea el agujero del ADR 0060 **más**
  el de arriba.
- ❌ Un MCP remoto no es sólo un host permitido: es un canal de salida
  completamente escribible (§Riesgos).

### (e) Un segundo proxy sólo para MCP, con su propia allowlist

Simétrico al `registry-proxy` del ADR 0094 (un tinyproxy disjunto para los
registries de paquetes).

- ✅ Separa el radio de daño: abrir Atlassian no toca la salida LLM.
- ❌ El sandbox tiene **una** variable `HTTPS_PROXY`, no una por destino. Habría
  que enrutar por cliente dentro del runtime, o encadenar proxies. Es un rediseño
  del transporte del sandbox por un beneficio que la allowlist única ya da
  (`FilterDefaultDeny` no distingue quién pregunta, pero el daño de «Atlassian
  alcanzable desde el sandbox» es el mismo con uno o con dos proxies).
- Se archiva: reevaluable si algún día el runtime distingue clientes HTTP.

## Decisión

**Recomendación: (a), acotada — el platform setting es la fuente de verdad de la
INTENCIÓN; `filter.txt` sigue siendo la AUTORIDAD de lo que se aplica; y ninguna
de las dos habla en nombre de la otra. Quien responde «¿está permitido?» no es el
setting ni el fichero: es el propio proxy, al que se le pregunta.**

El argumento decisivo: (b) es la única opción sin deriva, pero su validación **no
es implementable sin un bind nuevo** (el api-server no tiene el fichero), y ese
bind es (c) — que este ADR no puede firmar sin responder por el 0161. Y (a) sin
más presenta una deriva que se puede _ocultar_ muy fácilmente. La salida no es
elegir cuál de las dos mentiras se prefiere, sino **quitarle a las dos la
autoridad de responder**: si «Probar conexión» sale por el proxy (D9), el veredicto
lo emite el mismo componente que lo va a emitir en el run, y el espejo deja de
ser un oráculo para ser lo que es — una lista de intenciones auditada.

### D1 — Escapado y anclado

El setting guarda **hostnames en claro, nunca regexes**. El renderizador emite
`^` + host escapado + `$`, una línea por entrada, con el grupo de puerto que
decida la medición de D10 (y sólo si la medición dice que hace falta para casar —
nunca para restringir: eso no lo puede hacer el filtro, D3).

**No se usa `re.escape` a pelo, y esto no es purismo:** `re.escape` emite `\-`
(medido: `re.escape("a-b.example.com") == "a\\-b\\.example\\.com"`), y en POSIX
ERE una barra invertida delante de un carácter ordinario es **comportamiento
indefinido**. glibc y musl lo toleran hoy; el contrato no lo garantiza. Como el
validador (D2) ya reduce el alfabeto a `[a-z0-9.-]`, el **único** metacarácter
ERE que puede aparecer es el punto: se escapa el punto y nada más. El anclado
`^…$` es obligatorio y no negociable — sin él, `mcp.atlassian.com` casa
`evil-mcp.atlassian.com.attacker.tld`.

**Verificación empírica obligatoria, no razonada:** que la línea emitida
_realmente casa_ se comprueba contra tinyproxy, no contra `re`. El precedente
está en el repo: `^ollama(:[0-9]+)?$` lleva grupo de puerto y
`^api\.anthropic\.com$` no. La hipótesis razonable es que la diferencia sea el
método —el Ollama in-stack se habla en HTTP plano contra un puerto no estándar,
así que su `Host:` arrastra el `:11434`, mientras que un CONNECT a 443 llega con
el host pelado— pero **hipótesis razonable no es medición**: eso se mide (D10), no
se deduce.

### D2 — Validador: lo que se rechaza, y por qué

Un host es válido si y sólo si:

- casa `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$`
  (etiquetas de 1-63, sin guion inicial ni final, ≤253 en total) — se normaliza a
  minúsculas y se aplica IDNA/punycode antes de validar, para que un homógrafo
  unicode no entre por la puerta de atrás;
- **tiene al menos un punto**. Los hosts sin punto son nombres de servicio del
  compose (`vault`, `postgres`, `api-server`, `ollama`) y ya tienen su camino:
  `NO_PROXY` (`workers/execution.py:223-233`). Permitir uno aquí sería abrir el
  proxy —que vive en `agentic-net`— hacia el interior del stack;
- **no es una IP literal** (v4 ni v6, ni en forma decimal/octal/hexadecimal
  comprimida): una IP no se puede auditar por reputación ni revocar por DNS;
- **no es** `169.254.169.254` ni nada bajo `169.254.0.0/16`, `metadata`,
  `metadata.google.internal`, `localhost`, ni con sufijo `.internal`, `.local`,
  `.localdomain`, `.cluster.local`. **No se reescribe esta lista**: se reutiliza
  `api_server.cortex.web_safety` (`_BLOCKED_HOST_EXACT:52-54`,
  `_BLOCKED_HOST_SUFFIXES:46-51`, `_is_public_ip:79`), que ya existe para el mismo
  problema en el córtex (ADR 0067). Dos copias de una lista de bloqueo se
  bifurcan;
- **resuelve a IP pública** en el momento de guardar (`assert_safe_url:106` sin
  `allow_internal`). Con la honestidad de decir qué es y qué no: es una
  comprobación de **usabilidad y de intención**, no un control. El DNS puede
  cambiar después (rebinding). El control de verdad contra ese caso son las dos
  directivas `ConnectPort` (443 y 8443): los servicios internos del stack escuchan
  en 5432, 6379, 8200, 11434 — fuera de esos dos puertos, un CONNECT no llega. El
  riesgo residual está en la tabla.

El rechazo es **422 con el motivo concreto** («`10.0.0.5` es una IP literal»,
«`vault` no tiene punto: un MCP interno no necesita allowlist, se exime por
NO_PROXY»), nunca un booleano.

**Los comodines quedan FUERA del bloque generado, y es deliberado.** El filtro
vivo ya tiene un patrón —`^[a-z0-9-]+\.azure-api\.net$`, el APIM por convención
del ADR 0021— y este validador nunca lo habría aceptado: el alfabeto `[a-z0-9.-]`
rechaza `+`, `[`, `*` y `\`. No es una contradicción, es la frontera. Un patrón
abre una **familia** de hosts y no se puede juzgar de un vistazo en un formulario;
por eso vive donde hay revisor y diff: en el repo, fuera de los centinelas de D7,
con su PR. El panel **no puede expresar comodines y no debe intentarlo**, y el
texto de la tarjeta lo dice para que nadie lo descubra en caliente. La
consecuencia práctica, que hay que escribir donde se lea porque el propio
`filter.txt` la anticipa en su comentario: el operador con un APIM en dominio
propio (`apim.miempresa.com`) o con Azure OpenAI directo
(`<resource>.openai.azure.com`) tiene **dos caminos, y el bueno es el primero**:
si le basta un host concreto —y casi siempre le basta— es un FQDN y entra por el
setting sin tocar el repo; sólo si de verdad necesita la familia entera pasa por
rama, PR y despliegue.

### D3 — Puertos: el setting guarda SÓLO hostname

**El puerto por entrada es ficción y el enunciado de `task_mk_0a` lo escribe mal.**
Dos hechos medidos en `tinyproxy.conf:35-36`:

1. No hay ninguna directiva `ConnectPort 443 8443`. Hay **dos directivas
   separadas**, `ConnectPort 443` y `ConnectPort 8443`. Escribirlo con lista
   —como hacen el enunciado de la tarea y varios borradores de este documento—
   sugiere un parámetro con forma de conjunto que no existe.
2. Y sobre todo: `ConnectPort` es **global al proxy**, no por host. tinyproxy no
   tiene ninguna forma de decir «este host sólo por 8443». El filtro y los
   puertos son dos mecanismos que no se hablan.

De ahí que una entrada `host:puerto` mentiría, y en los dos sentidos según cómo
se renderice:

- Si la línea emitida es `^host(:8443)?$`, el grupo es **opcional**, así que casa
  igualmente el host pelado: no impide el 443. Prometería una restricción y no
  restringiría nada.
- Si la línea fuese `^host:8443$` y lo que tinyproxy evalúa en un CONNECT es el
  host pelado (D1/D10), la entrada no casaría **nada** y el host quedaría muerto
  en todos los puertos. Prometería una apertura y cerraría del todo.

**Decisión: el setting guarda hostname y nada más.** No se acepta `host:puerto` en
la entrada, no aparece un puerto en el mensaje de 422, y el panel no hace ninguna
promesa por host. Lo que sí hace el panel es decir **una vez**, en la tarjeta del
ajuste, el hecho global del stack: _el proxy sólo deja salir CONNECT por 443 y
8443, para todos los hosts permitidos_. Un host que sólo escuche en 9443 no se
puede habilitar desde aquí; hacerlo exige tocar `tinyproxy.conf` en el repo, y
entonces vale para todos.

Si el grupo de puerto acaba haciendo falta en la línea emitida, será **para casar**
(porque el `Host:` de un HTTP plano lo arrastra), nunca **para restringir**. Eso lo
decide la medición de D10, no esta sección.

`http` en claro se rechaza: el token de un MCP remoto viaja en cabecera, y además
`ConnectPort` sólo gobierna el túnel TLS.

### D4 — `Allow` por IP cliente de tinyproxy: no se toca, pero se comprueba

Con D9 el api-server pasa a ser cliente del proxy. Sus IPs son las de los bridges
`agentic-net` / `agentic-agents`, que caen dentro de `Allow 10.0.0.0/8`,
`172.16.0.0/12`, `192.168.0.0/16` (`tinyproxy.conf:21-23`). **No hace falta
cambiar nada**, y conviene notar que esa ACL de cliente ya era permisiva: la
seguridad de este proxy vive en el filtro de destino, no en quién pregunta.

Lo que sí se añade es **no darlo por supuesto**: si el operador configura
`default-address-pools` fuera de RFC1918, o habilita IPv6 en esos bridges (la
lista `Allow` no tiene entradas v6), el api-server se comería un rechazo del
proxy y el síntoma sería idéntico al de un host no permitido. Se distingue en el
mapeo de errores (D9) y se anota en el runbook (D10).

### D5 — Quién aprueba, y dónde queda escrito

**Sólo un System Admin.** El camino ya está: `PUT /admin/platform-settings/{key}`
va con `require_system_admin`, y `db/platform_settings.set_platform_setting`
vuelve a rechazar a cualquier otro actor —incluido un Tenant Admin— en la capa de
datos (`db/platform_settings.py:199-202`). Es un setting **de plataforma pedido
por tenants**: el tenant pide, el System Admin abre. No se delega.

**La auditoría necesita dos registros, porque son dos preguntas distintas:**

1. _¿Quién pidió/autorizó abrir este host?_ → una fila en `audit_log`
   (`tenant_id = NULL`, `action = "egress.mcp_allowlist.changed"`,
   `resource_type = "platform_setting"`, `changes = {"added": [...], "removed":
[...], "requested_for_tenant": <uuid|null>}`). Hace falta porque
   `platform_settings` sólo guarda `updated_by`/`updated_at` del **último**
   escritor: sobrescribe el historial que es justo lo que se quiere auditar.
2. _¿Qué está corriendo de verdad?_ → el historial de git del `filter.txt`
   aplicado, con su PR. Este segundo registro es el que sobrevive a una BD
   restaurada de un backup viejo.

**Fuera de alcance, y dicho a propósito:** no se construye un flujo de
_solicitud_ (cola de peticiones de tenant, aprobación, notificación). El canal
por ahora es el mensaje accionable (D9, D11) y el runbook. Un workflow de
aprobación es una feature de producto, no un control de seguridad, y este repo no
necesita una cuarta cola a medio construir.

### D6 — El campo, y los cinco sitios que hay que tocar

`platform_settings`, clave `egress.mcp_allowed_hosts`, categoría nueva
«Egress / Red» en `PLATFORM_KNOWN_SETTINGS`. Requiere un **tipo nuevo
`string_list`** en el registro, que hoy no existe. Los sitios son **cinco**, no
cuatro, y el que se olvida es el de los tests:

1. **El `Literal` de `PlatformSettingType`**
   (`api_server/platform_settings_registry.py:46`) — hoy
   `"bool" | "int" | "decimal" | "model_config" | "guardrails_config"`.
2. **La rama de `validate_platform_setting_value`** (`:385`), que termina en
   `raise ValueError(f"unknown setting type …")`: un tipo declarado sin rama
   revienta en runtime, no al importar.
3. **La entrada del registro**, y aquí está la mitad que el borrador de esta
   decisión se dejaba. `PlatformSettingDef` exige **cuatro textos**, no uno:
   `label_es`, `label_en`, `description_es`, `description_en`
   (`:53-70`). `PlatformCategoryDef` exige además un **`icon`** —nombre de
   componente lucide-react, resuelto en el frontend— y sus dos descripciones
   (`:73-93`). Y no es una convención que un test cace después:
   `require_language_pair` (`api_server/settings_registry.py:35-56`) valida **al
   construir, o sea al importar el módulo**, así que una entrada a medias **no
   deja arrancar el proceso**. El `icon` se elige de los que el panel sabe
   resolver: el mapa `ICONS` de `app/admin/settings/page.tsx:45-53` degrada a un
   engranaje genérico con `ICONS[def.icon] ?? SettingsIcon` (`:126`), o sea que un
   nombre inventado no rompe nada — falla en silencio, que es peor.
4. **El frontend**: la unión `SettingType`
   (`app/admin/settings/platform-defaults/page.tsx:46`, espejo declarado del
   backend) **y** la cadena de ternarios que elige el control
   (`:230-278`), que hoy cubre `bool`, `int`, `decimal`, `model_config` y
   `guardrails_config` y cae en `null` —un ajuste sin control, invisible— para
   cualquier otro.
5. **Los fixtures de i18n**: `app/admin/settings/i18n.test.tsx:54-120` espeja
   `platform_registry_to_dict()` **con las dos caras de cada texto**, a propósito:
   si la pantalla se quedara pintando `label_es` sin mirar el idioma, esos casos
   lo cazan. Un tipo nuevo cuyo control no se ejercite ahí entra sin cobertura
   bilingüe, que es exactamente lo que `prod-16` vino a cerrar.

`platform_registry_to_dict` (`:468`) **no necesita cambio**: serializa `sdef.type`
tal cual. Sólo se toca si el panel necesita metadatos por clave —el tope de 100,
por ejemplo— y entonces se inlinean como ya se hace con `provider_kinds` para
`model_config` (`:491-495`).

El tipo se introduce **genérico** (lista de strings con validador inyectable por
clave), no como `mcp_hosts`: la siguiente lista de la plataforma no debería
repetir esto.

Tope duro: 100 entradas. No por rendimiento —tinyproxy compila las regex una
vez— sino porque una allowlist que nadie puede leer de un vistazo ha dejado de
ser una allowlist.

### D7 — La deriva: se mide y se enseña; no se declara inexistente

Tres reglas, y la tercera es la que impide que «guardar en verde» mienta:

1. **Bloque delimitado, un solo escritor por bloque.** El `filter.txt` gana
   marcadores centinela:

   ```
   # >>> BEGIN generated: egress.mcp_allowed_hosts — NO EDITAR A MANO
   # <<< END generated: egress.mcp_allowed_hosts
   ```

   Lo de dentro lo escribe **sólo** el renderizador; lo de fuera (proveedores
   LLM, córtex, y los patrones con comodín de D2) lo escribe **sólo** una persona
   en el repo. Ninguno pisa al otro, y el instalador nunca escribe ninguno de los
   dos (D8).

2. **El panel jamás dice «permitido».** Dice **«guardado — pendiente de aplicar
   al proxy»**, con el comando exacto al lado. El estado «aplicado» no se deduce
   de haber guardado: se **comprueba**.

3. **La comprobación es empírica.** Un endpoint de estado abre un CONNECT de
   prueba **a través del proxy** por cada host del setting y reporta
   `permitido` / `bloqueado`. Es la única afirmación verdadera por construcción,
   y detecta los dos sentidos de la deriva que importan: un host guardado y no
   aplicado, y un host aplicado que el setting ya no lista.

   **Límite que hay que escribir donde se lea:** sondear **no enumera**. Una
   allowlist de regex no se puede recorrer desde fuera, así que la comparación es
   de _subconjunto_, no de igualdad: se puede afirmar «todo lo que el setting
   pide está permitido», nunca «el proxy no permite nada más». Quien quiera la
   respuesta exacta lee el fichero en el host.

   **Y tiene un coste de seguridad que se paga con los ojos abiertos:** ese
   sondeo convierte al api-server en un cliente que abre conexiones a hasta 100
   destinos externos elegidos por el operador. Está en la tabla de riesgos, y sus
   dos frenos son de diseño: sale **por el proxy** (así que sólo alcanza lo que el
   propio filtro ya permite) y **no se dispara solo** — nunca en un barrido
   periódico, sólo bajo petición explícita de un System Admin.

### D8 — El instalador: ni consulta la BD ni la va a consultar

**El instalador sigue copiando `stack_assets` byte a byte, y el bloque generado
sale vacío.** Es lo correcto y no un apaño: en `GENERATE_CONFIG` no hay base de
datos (las migraciones y la siembra van después), el paquete no importa
`api_server`, y `test_installer_ships_stack_assets.py` exige que la copia sea
idéntica a la de `docker/`. La frase que MK-14 cita del enunciado anterior de
`task_mk_02` —«el instalador vuelca el platform setting al filtro»— describe algo
que el orden de arranque hace imposible; ésta es la respuesta a la pregunta que
`task_mk_0a` dejó en su lugar.

**Cómo se evita la doble verdad:** hay exactamente un escritor por bloque y un
momento para cada uno.

| Momento                         | Quién escribe el filtro                     | Qué contiene el bloque generado |
| ------------------------------- | ------------------------------------------- | ------------------------------- |
| Instalación (`GENERATE_CONFIG`) | El instalador, copiando `stack_assets`      | Vacío (sólo los centinelas)     |
| Día 2, al abrir un host         | El renderizador, en el host, con el setting | Los hosts del setting           |

La siembra posterior a las migraciones deja el setting en `[]`, que es
exactamente lo que el fichero recién instalado dice. Consistentes por
construcción, sin que ninguno consulte al otro.

**Y el que aplica no es el panel.** El api-server no está en `agentic-docker` ni
tiene `DOCKER_HOST`: dárselo contradiría el ADR 0060. El paso de aplicación es un
comando **en el host** —a crear, `scripts/egress/render-mcp-allowlist.py`— que (i)
lee el setting por la API de admin con un token de System Admin, (ii) reescribe
**en sitio** el bloque generado de **las dos copias** del filtro, (iii) hace
`build` + `up -d --force-recreate egress-proxy`, y (iv) vuelve a sondear (D7.3)
para confirmar. Reescribe en sitio y no sustituye el inodo, para no dejar el
camino minado el día que se adopte (c).

### D9 — «Probar conexión» sale POR el proxy. Sí, con su coste

**Sí, y es la mitad del valor de este ADR.** Un botón que prueba un camino
distinto del de producción fabrica confianza injustificada; con la allowlist
recién instalada, además, fabricaría _exactamente el falso verde_ que MK-15
describe.

El enganche existe y está verificado: `streamablehttp_client` y `sse_client` del
SDK MCP aceptan `httpx_client_factory` (firma comprobada en el entorno:
`(headers, timeout, auth) -> httpx.AsyncClient`), y son los dos transportes que
`shared_mcp.client` ya usa (`client.py:257,268`). `shared_mcp.client` pasa una
factoría que construye `httpx.AsyncClient(proxy=…)`, igual que ya hace el córtex
en `cortex/web.py:57-74` (`_build_proxied_client`). **No se ponen
`HTTP_PROXY`/`HTTPS_PROXY` en el entorno del api-server**: `httpx` lleva
`trust_env=True` y proxificaría también el tráfico interno (searxng, tts, la API
interna), rompiendo cosas que hoy funcionan.

Se aplica a **los tres call sites**, no sólo al botón: `test-connection`
(`routers/mcp.py:246`), `import-tools` (`:362`) y el descubrimiento automático al
guardar que trae `task_mk_01`. Si uno queda directo, la asimetría vuelve por esa
puerta.

Con dos reglas de simetría:

- **Un MCP interno (host sin punto) NO va por el proxy**, replicando
  `_internal_mcp_hosts` (`workers/execution.py:223-233`). La regla de exención
  tiene que ser **la misma función compartida**, no dos copias: si probar y
  ejecutar discrepan en qué es interno, se reintroduce la asimetría en su forma
  más difícil de ver.
- **Un MCP `stdio` no tiene host**: fuera de alcance, ni validación ni proxy.

**Lo que D9 NO arregla, y hay que decirlo antes de que alguien lo descubra en
rojo: la autenticación.** Proxificar cierra la asimetría de **transporte**. La de
**auth** sigue abierta, y para el caso que motiva el plan —Atlassian— es la que
manda: `_to_runtime_config` (`routers/mcp.py:463-479`) **no propaga `oauth_ref`**
al `MCPServerConfig` que consume el SDK, y `shared_mcp` tampoco lo consume (el
campo existe declarado en `shared_mcp/types.py:75` y nadie lo lee); el OAuth lo
resuelve el **runtime** contra `internal_agent` (`routers/internal_agent.py:238`).
Es MK-10 del plan, y quien lo decide es el **ADR 0166** (`task_mk_0b`), no éste.
Consecuencia concreta y esperable: aceptado este ADR e implementada
`task_mk_02`, «Probar conexión» contra `mcp.atlassian.com` dejará de morir con
`403 Filtered` **y morirá con `AUTH_ERROR`** (`routers/mcp.py:246-251`). Eso es
progreso —el fallo pasa a ser el verdadero, emitido en el sitio correcto— pero
**este ADR no entrega simetría end-to-end**, y prometerla sería repetir en el
documento el mismo falso verde que viene a cerrar en el producto. El orden de
lectura es: 0165 arregla el camino, 0166 arregla la credencial.

**El coste, sin adornos:**

1. Cambia el significado de un fallo. Si el proxy está caído, la prueba pasa a
   fallar donde antes pasaba. **Es la mejora**: el run también habría fallado.
2. El mapeo de errores tiene que distinguir tres cosas que hoy se ven iguales, y
   el discriminante es sólido: un `httpx.ProxyError` en el CONNECT ⇒ _el host no
   está en la allowlist_ (mensaje accionable: «añade `mcp.atlassian.com` a la
   allowlist de MCP remotos (Sistema → Egress)»); un `403` **de la respuesta HTTP
   de origen** ⇒ _credencial rechazada por el servidor MCP_; un rechazo del
   `Allow` de cliente ⇒ _el api-server no puede usar el proxy_ (D4). Confundir el
   primero con el segundo manda al operador a rotar un token que está bien.
3. Latencia: un salto más. Irrelevante frente al handshake MCP.
4. `API_SERVER_CORTEX_EGRESS_PROXY_URL` (`config.py:610`) deja de ser «del
   córtex». Se renombra a una variable de egress del api-server con alias
   retrocompatible, o se documenta que el nombre miente. No se deja a medias.

### D10 — Verificación, y qué no se declara sin medir

- **Unit** — el validador: acepta `mcp.atlassian.com`; rechaza IP literal (las
  cuatro notaciones), `169.254.169.254`, `localhost`, `vault` (sin punto),
  `foo.internal`, `.*`, `^[a-z0-9-]+\.azure-api\.net$` (un patrón del propio
  filtro: el panel no expresa comodines, D2), `mcp.atlassian.com:8443` (D3: la
  entrada es sólo hostname), `mcp.atlassian.com.` con punto final, un homógrafo
  unicode, y >253 caracteres.
- **Unit** — el renderizador: la línea emitida está anclada, **no** contiene
  `\-`, y `re.fullmatch` sobre la ERE emitida acepta el host y rechaza
  `evil-mcp.atlassian.com.attacker.tld` y `mcpXatlassianYcom`.
- **Unit** — el instalador: el `filter.txt` que escribe `_generate_config`
  contiene los centinelas y el bloque **vacío**; y sigue siendo byte a byte igual
  al de `docker/` (el test existente no se relaja).
- **Integración** — `POST .../mcp/test-connection` con host no permitido: 422 con
  el mensaje accionable, no un 502 crudo; y `discover_tools` se invoca con un
  cliente proxificado (assert sobre la factoría).
- **Integración** — guardar un servidor con `url` externa fuera de la allowlist
  **devuelve 200 con el aviso tipado** (D11), no un 4xx; y el mismo guardado con
  una `url` de forma prohibida (IP literal, `169.254.169.254`) devuelve 422.
- **Integración** — el `PUT` del setting escribe la fila de `audit_log` con
  `added`/`removed`.
- **vitest** — la tarjeta muestra «guardado — pendiente de aplicar», el aviso de
  D11 en el diálogo del servidor, y el mapeo de los tres errores de D9.
- **Medido a mano, no deducido, y anotado en el runbook nuevo**
  (`docs/06-runbooks/egress-mcp-allowlist.md`, a crear): (i) **contra qué cadena
  casa tinyproxy en un CONNECT** —host pelado o `host:puerto`—, probando a 443 y a
  8443, que es lo que decide si la línea emitida lleva grupo de puerto; el
  precedente de `^ollama(:[0-9]+)?$` vs `^api\.anthropic\.com$` dice que aquí la
  intuición falla; (ii) el tiempo real del rebuild+recreate, que es el número que
  decide si (c) urge o no.

### D11 — Qué pasa al GUARDAR un servidor MCP con `url` externa

`task_mk_02` pide que el api-server valide «al guardar un servidor con `url`
externa» (plan `:183-186`), y hasta aquí este ADR sólo había decidido la
validación **del setting** (D2) y la de la prueba (D9). Falta el caso del día 1, y
tiene trampa: es donde fail-open y fail-closed son ambos defendibles y ambos
rompen algo.

**Decisión: el guardado es fail-OPEN respecto a la allowlist y fail-CLOSED
respecto a la forma.** Son dos comprobaciones distintas y se resuelven distinto a
propósito.

**Fail-open respecto a la allowlist**, con aviso tipado y no bloqueante. Guardar
un servidor cuyo host no está (todavía) permitido devuelve **200** con un
`warning` estructurado en el cuerpo, que la tarjeta del servidor pinta. Tres
razones, y la primera basta:

1. **Fail-closed encierra al `tenant_admin` en un bucle sin salida.** El servidor
   declarado _es_ el artefacto que justifica pedirle la apertura al System Admin
   (D5). Si no lo puede guardar, no puede pedir nada: sólo un System Admin podría
   desatascarlo, y para eso tendría que adivinar el host.
2. **Haría fallar una escritura de tenant por un estado de plataforma que el
   tenant no controla**, y encima retroactivamente: el día que un System Admin
   retirase un host, cualquier edición futura de un servidor ya guardado —cambiar
   su nombre, su timeout— empezaría a devolver 422 por un motivo que no tiene
   nada que ver con lo que se está editando.
3. La declaración por sí sola **no abre nada**. El egress sigue deny-by-default;
   un servidor guardado sin su host permitido simplemente no conecta. Guardar no
   es alcanzar.

**Y el aviso usa las mismas palabras que D7.2, ni una más:** «guardado — este host
no está hoy en la allowlist de egress de la plataforma; pídele su apertura a un
System Admin (Sistema → Egress). Hasta entonces, las ejecuciones y las pruebas
con este servidor fallarán.» No dice «permitido» ni «pendiente de aplicar»: no lo
sabe. **El único que puede decir «permitido» sigue siendo el sondeo de D7.3**, y
por eso el aviso del guardado no contradice D7.2 — afirma menos, no distinto.

**Fail-closed respecto a la forma.** Lo que sí rechaza el guardado con 422 es una
`url` cuyo host no pase el validador de D2 en su mitad _estructural_: alfabeto,
FQDN bien formado, ni IP literal, ni `169.254.169.254`, ni `localhost`, ni sufijo
`.internal`/`.local`. Esto no es la allowlist: es no almacenar una URL que D9 va a
marcar después desde el api-server. La resolución DNS a IP pública **no** se
exige aquí (es lenta, y `get_tenant_session` mantiene la transacción del request
abierta durante todo el handler — MK-11): se exige al abrir el host en el setting,
que es donde hay tiempo y donde el actor es un System Admin.

**El límite que esta decisión no cierra, y hay que nombrarlo:** un host **sin
punto** sigue siendo válido al guardar, porque es la forma legítima de declarar un
MCP interno del compose (`_internal_mcp_hosts`), y desde D9 está exento del proxy.
Eso significa que un `tenant_admin` puede apuntar el api-server a un servicio
in-stack. **No es un agujero que abra este ADR** —hoy el `test-connection` sale
directo desde el api-server hacia _cualquier_ destino, con o sin punto, así que la
superficie actual es estrictamente mayor— pero D9 la reduce sin cerrarla, y
cerrarla exigiría distinguir «servicio MCP que el tenant desplegó» de «servicio de
la plataforma», cosa que el hostname no dice. Queda en la tabla de riesgos, no en
una nota al pie.

### Qué le pasa a `task_mk_0a` y a `task_mk_02` (y por qué este ADR **no** lleva `rejects:`)

Ninguna de las dos casillas **queda invalidada**: `task_mk_0a` se cierra
escribiendo este documento, y `task_mk_02` queda **especificada** y se implementa.
Por eso el frontmatter no lleva `rejects:` — el campo exige que la casilla
apuntada quede **cerrada `[x]`** con nota en negativo
(`tests/docs/test_adr_precedence.py::test_a_rejected_task_is_closed_not_open`), y
marcar `[x]` una tarea que hay que hacer sería exactamente la mentira que ese
test existe para impedir.

Lo que sí obliga la cadena de precedencia de `CLAUDE.md`: **en el mismo commit
que acepte este ADR** hay que corregir tres frases del plan, porque describen
cosas que este documento contradice o que ya no son ciertas:

- **`task_mk_0a`, «si la allowlist es de host o de `host:puerto` (hoy
  `ConnectPort 443 8443`)»** → la respuesta es _de host_ (D3), y el paréntesis
  escribe con lista una directiva que no la tiene: son `ConnectPort 443` y
  `ConnectPort 8443`, dos directivas globales
  (`tinyproxy.conf:35-36`). La pregunta queda respondida y la cita, corregida.
- **Criterio de cierre nº5, «el nuevo ADR de la allowlist de MCP remotos
  (`task_mk_02`)»** → lo escribe `task_mk_0a`; `task_mk_02` es la casilla que no
  puede empezar hasta que esté `accepted`. Es una palabra, y evita que alguien
  busque el ADR en la casilla equivocada.
- **`task_mk_02` gana dos puntos de alcance que el ADR le añade**: el **sondeo
  empírico** (D7.3), sin el cual el resto es un espejo sin oráculo, y la
  **semántica del guardado** (D11: fail-open con aviso, fail-closed de forma), que
  el enunciado pedía sin decidir. El paso de aplicación, además, **no lo dispara
  el panel** (ADR 0060): es un comando en el host (D8).

**Este ADR no contradice `CLAUDE.md`** y por tanto no lo modifica: es una
aplicación del Principio Rector 2 (el egress sigue siendo deny-by-default) y del
1 (la allowlist es de plataforma justamente para que un tenant no pueda abrir
egress a otro). La frontera de §«Dónde vive un secreto» tampoco se mueve: un
hostname no es un secreto.

## Consecuencias

**Mejora.** Un MCP remoto pasa de imposible-sin-tocar-el-repo a una operación de
System Admin con RBAC, validación y auditoría. El `403 Filtered` crudo se
convierte en una instrucción. Y —lo que más importa— **«Probar conexión» deja de
poder salir en verde sobre un run que va a morir por transporte**: probar y
ejecutar recorren el mismo camino, incluidos los tres call sites de
descubrimiento. Con el matiz que D9 escribe en voz alta: para un MCP con OAuth el
fallo cambia de sitio (`403 Filtered` → `AUTH_ERROR`), no desaparece; eso lo cierra
el ADR 0166.

**Complejidad.** Un tipo nuevo en el registro de platform settings (cinco sitios,
D6), un validador, un renderizador con centinelas, un script de aplicación en el
host, un endpoint de sondeo, una factoría httpx en `shared_mcp`, el aviso del
guardado (D11) y un runbook. Estimación coherente con los 2 d de `task_mk_02`
sólo si el sondeo y el script se mantienen minúsculos; si crecen, se parte la
tarea antes que recortarlos.

**Trade-offs asumidos, en voz alta.**

- **La deriva no se elimina, se hace visible.** Entre guardar y aplicar hay una
  ventana. Se paga a cambio de no tener que responder hoy la pregunta 6 del ADR 0161.
- **La ventana es asimétrica, y en el sentido malo.** Añadir un host no surte
  efecto hasta el paso manual (lo cuenta el panel). **Quitarlo tampoco**: el host
  sigue alcanzable desde todos los sandboxes hasta que alguien ejecuta el script.
  Revocar, que es lo urgente, tarda lo mismo que abrir, que es lo que puede
  esperar. Tiene su fila en la tabla de riesgos y su procedimiento en el runbook.
- **El paso de aplicación es manual y sale de la máquina.** Es el precio del ADR
  0060, y es el precio correcto.
- **Cada host abierto lo está para TODOS los sandboxes**, de todos los tenants.
  El filtro no distingue quién pregunta. La segmentación por tenant exigiría (e)
  y un rediseño del transporte del sandbox.

**Deuda con disparador, al estilo del ADR 0158.** La opción (c) —bind mount— es
el final de trayecto correcto y queda **aplazada, no descartada**. Se reabre este
ADR cuando ocurra cualquiera de estas dos cosas: (i) el ADR 0161 responda que sí
a su pregunta 6 (una imagen publicada no puede llevar horneada la allowlist de un
sitio concreto: el bind deja de ser una mejora y pasa a ser un requisito); o (ii)
el rebuild+recreate medido en D10 resulte lo bastante caro como para que alguien
prefiera editar el fichero a mano — el día que eso pase, la fuente de verdad se
habrá mudado sola.

**Y por qué este aplazamiento NO lleva `reopen_when:` en el frontmatter.** El
mecanismo del [ADR 0158](./0158-skillopt-aplazado-con-disparador.md) es real y
está guardado (`tests/docs/test_adr_deferrals.py`), pero su campo toma
**casillas de roadmap o `plan_id`**: exige que cada id exista en `docs/roadmap/`
(`test_reopen_when_points_at_something_that_exists`) y que el documento apuntado
**cite de vuelta al ADR** (`test_each_condition_cites_the_adr_back`), porque quien
cierra la casilla abre el plan, no el corpus de ADR. Ninguno de los dos
disparadores de arriba es una casilla: el (i) es una **pregunta abierta dentro de
otro ADR ya `accepted`** —la 6 del 0161, que su propia §«Qué se hace cuando esto
se acepte» deja marcada como «que esta firma no responde»— y el (ii) es un
**umbral sobre una medición**, o sea un juicio. Apuntar `reopen_when:` a un id
inventado rompería la suite, y apuntarlo a uno aproximado sería peor: convertiría
en mecánico un disparo que no lo es. **Lo que sí queda escrito, y es la parte
accionable:** el día que el plan 15 abra una casilla para publicar las imágenes
de los dos tinyproxy, este ADR gana su `reopen_when:` apuntándola en ese mismo
commit, y esa casilla cita de vuelta al 0165 — que es el momento exacto en que el
disparador (i) pasa de prosa a comprobable.

## Riesgos

| Riesgo                                                                                                                                            | Prob.    | Impacto     | Mitigación                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Host sin escapar/anclar: el punto actúa de comodín y `mcp.atlassian.com` casa `mcpXatlassianYcom`                                                 | Media    | **Alto**    | D1 (escape sólo del punto + `^…$`) + unit con los dos negativos exactos                                                                                                                                                                                        |
| El operador mete una regex (`.*`) y abre el proxy entero                                                                                          | Baja     | **Crítico** | D2: el alfabeto `[a-z0-9.-]` rechaza `*`, `\`, `(`, `                                                                                                                                                                                                          | ` en el guardado; el setting nunca almacena regex, y los patrones legítimos viven fuera del bloque generado, con PR y revisor |
| Host permitido que resuelve (o pasa a resolver) a una IP interna                                                                                  | Baja     | Alto        | `assert_safe_url` al guardar el setting + las dos directivas `ConnectPort` (443/8443; los servicios internos están en 5432/6379/8200/11434). Residual: un servicio interno HTTP en 443/8443 sería alcanzable — se acepta y se anota en el runbook              |
| **Guardar en verde y no aplicar**: el panel dice permitido, el run muere                                                                          | **Alta** | Medio       | D7: «pendiente de aplicar» + sondeo empírico a través del proxy; ni el panel ni el guardado de D11 afirman «permitido» sin haberlo probado                                                                                                                     |
| **Revocación asimétrica**: quitar un host del setting NO cierra el egress hasta el paso manual, y el sondeo lo reporta como `permitido` con razón | Media    | **Alto**    | D8: el script es el mismo para abrir y para cerrar, y el runbook lo escribe **primero** en el procedimiento de revocación, con su tiempo medido (D10). El sondeo de D7.3 detecta el sentido «aplicado pero ya no listado», que es justo éste                   |
| **El api-server abre CONNECT a hasta 100 destinos externos** (sondeo de D7.3): superficie tipo SSRF y baliza «existimos» hacia terceros           | Media    | Medio       | D7.3: el sondeo sale **por el proxy** (no alcanza más que lo que el filtro ya permite), sólo bajo petición explícita de un System Admin, nunca en barrido periódico, y los destinos son los que ese mismo actor acaba de autorizar                             |
| Un `tenant_admin` declara un MCP con host **sin punto** y el api-server lo alcanza saltándose el proxy (exención `_internal_mcp_hosts`)           | Media    | Medio       | D11: preexistente y **estrictamente menor** que hoy (el `test-connection` actual sale directo a todo destino); D9 lo reduce a los hosts sin punto; distinguir «MCP del tenant» de «servicio de plataforma» por hostname no es posible y queda fuera de alcance |
| Se toca una sola copia del filtro (`docker/` o `stack_assets`)                                                                                    | Media    | Medio       | `test_installer_ships_stack_assets.py` ya lo rompe; el script de D8 escribe **las dos**                                                                                                                                                                        |
| La línea emitida no casa de verdad en tinyproxy (grupo de puerto)                                                                                 | Media    | Medio       | D10: se mide contra el proxy, no contra `re`. Precedente: `^ollama(:[0-9]+)?$` vs `^api\.anthropic\.com$`                                                                                                                                                      |
| `ProxyError` confundido con el `403` del servidor MCP → el operador rota un token sano                                                            | Media    | Bajo        | D9.2: discriminante explícito (fallo en el CONNECT vs. respuesta de origen) y tres mensajes distintos                                                                                                                                                          |
| Con D9 el api-server no puede usar el proxy (bridge fuera de RFC1918, o IPv6)                                                                     | Baja     | Medio       | D4: mensaje de error propio + comprobación en el runbook; la ACL de cliente no se relaja a ciegas                                                                                                                                                              |
| Se lee este ADR como si dejara Atlassian funcionando, y el `AUTH_ERROR` se interpreta como que la allowlist no se aplicó                          | **Alta** | Bajo        | D9 lo dice por escrito y el mapeo de errores separa los tres casos; el ADR 0166 (`task_mk_0b`) es quien cierra el OAuth (MK-10)                                                                                                                                |
| La allowlist se lee como control de exfiltración                                                                                                  | Media    | **Alto**    | El párrafo de abajo lo niega por escrito, y el texto del panel lo repite donde se decide                                                                                                                                                                       |
| El setting crece a 80 hosts y nadie lo revisa                                                                                                     | Media    | Medio       | Tope de 100 + fila de `audit_log` por cambio + revisión en el runbook de upgrade                                                                                                                                                                               |

**Y lo que hay que decir dos veces porque es lo que más se malinterpreta: esto NO
es control de exfiltración.** tinyproxy filtra el destino del CONNECT / el
`Host:`; el cuerpo de una sesión TLS es opaco para él. Abrir `mcp.atlassian.com`
significa que **cualquier** sandbox de **cualquier** tenant puede enviar
**cualquier cosa** a Atlassian mientras dure la ejecución. Es un control de
**alcanzabilidad**, no de contenido. Lo que sí acota el contenido vive en otro
sitio y hay que apoyarse en ello: el `security_level='sandboxed'` de las tools
MCP importadas (ADR 0052), los guardrails `pre_tool`/`post_tool` (Principio
Rector 10), las políticas de aprobación por categoría (Principio Rector 11) y que
las credenciales del server salgan de Vault por servidor. **Cada línea añadida a
esta allowlist es un canal de salida completamente escribible**, y así hay que
juzgarla al aprobarla.

## Alternativas rechazadas

- **(d) Derivar el filtro de `projects.mcp_servers`** — rechazada por escalada de
  privilegios: la escribe un `tenant_admin` y el efecto es de plataforma
  (Principio Rector 1). Además «en caliente» no existe con un proxy estático:
  exigiría (c) más un escritor con acceso al daemon, o sea el ADR 0060 abierto.
  Y confunde «declarar un MCP» con «autorizar un canal de salida».
- **(b) Sólo fichero, con el api-server validando contra él** — es la opción sin
  deriva y por eso costó descartarla. Se descarta porque **el api-server no tiene
  ese fichero**: hacerlo legible exige el bind de (c) pagado en otro contenedor,
  y porque abrir un host quedaría atado a un ciclo de PR y despliegue sin lugar
  donde registrar quién lo pidió. Su mejor idea —que la autoridad es el fichero—
  se conserva íntegra en la decisión.
- **(c) Bind mount ahora** — no rechazada: **aplazada con disparador**
  (§Consecuencias). Prejuzgaría la pregunta 6 del ADR 0161 y movería dos guardas
  de `test_infra_images_are_scanned.py` que el propio 0161 avisa de que no son un
  cambio de una línea.
- **(e) Segundo proxy sólo para MCP** — archivada: el sandbox tiene una sola
  variable de proxy, así que exigiría enrutado por cliente dentro del runtime,
  por un beneficio que la allowlist única ya da.
- **Auto-aplicar desde el panel** — rechazada sin matices: contradice el ADR 0060
  (el api-server no habla con el daemon Docker, ni por el socket-proxy).
- **Poner `HTTP_PROXY` en el entorno del api-server** para que D9 salga gratis —
  rechazada: `httpx` con `trust_env=True` proxificaría también el tráfico interno
  (searxng, tts, API interna) y rompería lo que hoy funciona. La factoría
  explícita es más código y menos daño.
- **Guardar un servidor MCP con `url` no permitida como 422 (fail-closed)** —
  rechazada en D11: encierra al `tenant_admin` en un bucle sin salida (no puede
  declarar el servidor que justifica la petición) y haría fallar ediciones
  posteriores por un estado de plataforma que él no controla. El aviso tipado
  informa sin bloquear, y guardar nunca fue alcanzar.
- **Puerto por entrada (`host:puerto`) en el setting** — rechazada en D3: no es
  restrictiva (`^host(:8443)?$` casa igual el host pelado) o es letal
  (`^host:8443$` no casaría nada si el CONNECT llega pelado), y en ningún caso
  `ConnectPort` —global, dos directivas— puede acotarse por host.

## Trazabilidad

- **Roadmap**: `docs/roadmap/remediacion-marketplace-mcp-2026-09-02.md`
  (`task_mk_0a:152-164`, que pide este ADR; `task_mk_02:181-193`, que lo
  implementa; hallazgos MK-03, MK-13, MK-14, MK-15 en `:88-99` y UI-03; y
  `task_mk_01`, que abre el tercer call site de descubrimiento).
- **Proxy**: `docker/egress-proxy/{Dockerfile,filter.txt,tinyproxy.conf}` —
  `tinyproxy.conf:21-23` (`Allow` de cliente), `:28-31` (filtro ERE,
  `FilterDefaultDeny`), `:35-36` (**las dos** directivas `ConnectPort`);
  `filter.txt` con sus 9 entradas; y su copia en
  `apps/installer/backend/src/installer_backend/stack_assets/egress-proxy/`;
  `docker/docker-compose.yml:405-412`.
- **Backend**: `apps/api-server/src/api_server/routers/mcp.py:246,362`
  (call sites de `discover_tools`), `:463-479` (`_to_runtime_config`, **sin**
  `oauth_ref`); `api_server/platform_settings_registry.py:46,53-70,73-93,385,468,491-495`;
  `api_server/settings_registry.py:35-56` (`require_language_pair`);
  `routers/platform_settings.py` (PUT con `require_system_admin`);
  `db/platform_settings.py:199-202`; `cortex/web_safety.py:40-56,79,106`;
  `cortex/web.py:57-74` (patrón de cliente proxificado); `config.py:610`
  (`API_SERVER_CORTEX_EGRESS_PROXY_URL`);
  `packages/shared-mcp/src/shared_mcp/client.py:245-277` (los dos transportes
  HTTP que aceptan `httpx_client_factory`); `shared_mcp/types.py:75`
  (`oauth_ref`, declarado y sin consumidor).
- **Workers**: `workers/container.py:329-332` (inyección del proxy);
  `workers/execution.py:223-233,311-322` (exención `NO_PROXY` de hosts internos).
- **Instalador**: `real_step_executor._generate_config:682-689`;
  `compose_generator.py:1226,1282` (redes de api-server y workers);
  `tests/unit/test_installer_ships_stack_assets.py`;
  `tests/unit/test_infra_images_are_scanned.py:232-242,264-282`.
- **Frontend**: `app/admin/settings/platform-defaults/page.tsx:46,230-278`;
  `app/admin/settings/page.tsx:45-53,126` (mapa `ICONS` y su fallback);
  `app/admin/settings/i18n.test.tsx:54-120` (fixtures espejo del registry);
  `app/admin/projects/[id]/mcp-servers/mcp-connection-test-section.tsx:135-141`.
- **Guardas de gobierno**: `tests/docs/test_adr_precedence.py` (por qué no hay
  `rejects:`); `tests/docs/test_adr_deferrals.py` (por qué no hay `reopen_when:`).
- **Docs a crear/actualizar**: `docs/06-runbooks/egress-mcp-allowlist.md` (nuevo);
  `docs/03-guides/gotchas/agent-runtime-egress-blocks-in-stack-llm.md` (enlazar
  el runbook); `docs/04-reference/` de platform settings.
- **ADR relacionados**:
  [0019](./0019-egress-red-sandbox-agent-runtime.md) (egress del sandbox — este
  ADR extiende su allowlist, no la sustituye),
  [0021](./0021-shared-llm-layer-catalogo-cerrado.md) (catálogo cerrado de LLM:
  las siete líneas LLM del filtro quedan fuera del bloque generado),
  [0052](./0052-import-mcp-tools-catalogo.md) (importación de tools MCP y
  `security_level`),
  [0060](./0060-acceso-daemon-docker-y-ruta-api-interna-sandbox.md) (acceso al
  daemon Docker: **por qué el panel no aplica**),
  [0067](./0067-tools-web-search-y-fetch-con-egress-guardrails.md) (web del
  córtex por el mismo proxy: patrón y anti-SSRF reutilizados),
  [0094](./0094-egress-runtime-templates-registries-via-proxy-allowlist.md)
  (segundo tinyproxy para registries: precedente de proxies disjuntos),
  [0098](./0098-politica-push-pr-resync-ciclo-plan.md) (fetch periódico de
  remotos git: otro egress, gobernado aparte, no se toca),
  [0129](./0129-servicios-e-imagen-runtime-por-proyecto.md) (servicios e imagen
  de runtime por proyecto),
  [0158](./0158-skillopt-aplazado-con-disparador.md) (precedente de aplazamiento
  con disparador, y el motivo de que aquí no sea mecanizable),
  [0161](./0161-distribucion-e-instalacion-de-la-plataforma.md) (**pregunta 6
  abierta**: si estas imágenes se publican — es el disparador de (c)), y el
  **0166** (`task_mk_0b`, aún por escribir: el OAuth de los MCP remotos, MK-10,
  que es lo que D9 explícitamente **no** arregla).
