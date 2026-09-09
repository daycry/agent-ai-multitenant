---
adr_id: "0081"
title: "Gates de seguridad y materialización del install de marketplace: diferidos a Fase B/C"
status: accepted
date: 2026-06-24
authors: [claude-opus]
plan_referenced: auditoria-2026-06-memoria-tools-marketplace
docs_language: es
related: ["0019", "0049", "0052"]
supersedes: []
---

# ADR 0081 — Gates de seguridad y materialización del install de marketplace: diferidos a Fase B/C

> **Estado: `accepted` (2026-06-24)** — el operador eligió **diferir con honestidad**:
> corregir el copy engañoso y documentar el gap aquí, en lugar de cablear los gates de
> forma que regrese el feature. La implementación real queda planificada (abajo) pero NO
> se hace hasta tener la infraestructura.

## Contexto

La auditoría 2026-06 (`docs/roadmap/auditoria-2026-06-memoria-tools-marketplace.md`)
encontró dos huecos en el flujo de instalación del marketplace:

- **H4** — `POST /marketplace/installations` (install **fresco**) NO ejecuta los gates de
  seguridad (firma → análisis estático → sandbox). El `InstallOrchestrator.install()`
  SÍ los corre (`_run_security_gates`), pero solo está cableado al path de **update**
  (`perform_installation_update`). Un install fresco se persiste sin verificar firma ni
  escanear código; solo se aplica el gate de **consent** (un listing no-verified aterriza
  `DISABLED` sin permisos).
- **M1** — instalar un listing **no materializa** nada en el catálogo nativo del tenant
  (`tools` / `skills` / `agents`): solo crea una fila `marketplace_installations` + auditoría.
  Ningún path de runtime consume la instalación, así que `enabled` no produce una capacidad
  utilizable por los agentes.

## Decisión

**No cablear `InstallOrchestrator.install()` en el endpoint ahora.** Hacerlo de forma
naïve sería una **regresión**, no un fix:

1. **El gate de sandbox necesita Docker que el api-server no tiene (Principio 2).**
   `_gate_sandbox` construye `MarketplaceSandbox()` (corre un contenedor de prueba) y
   **falla cerrado** si no puede ejecutarlo. El api-server **no tiene socket Docker por
   diseño** (aislamiento por contenedor, Principio 2 / ADR 0019). Los listings
   `community`/`experimental` exigen sandbox → **todo install → 422**.
2. **`verified` exige clave de firma + artefacto en disco.** Sin
   `MARKETPLACE_SIGNING_PUBLIC_KEY` configurado y sin el artefacto firmado en el root de
   `LocalArtifactFetcher`, el gate de firma falla cerrado → **422**.
3. **No hay registro de artefactos vivo.** El `LocalArtifactFetcher` lee de un root en
   disco que hoy no se puebla (no existe el "registry runtime"); el gate de fetch (gate 1,
   siempre corre) aborta → **422**.

Es decir: cablear los gates sin la infraestructura **deshabilita la instalación entera**.
Por eso H4 y M1 son la **Fase B/C** que el diseño difirió a propósito (los comentarios del
código ya lo marcaban como "Phase B/C" / "live path pending the registry runtime").

### Acción inmediata (esta entrega)

- **Copy honesto:** `InstallationStatus.ENABLED` ya no afirma "usable by the tenant's
  agents"; ahora dice "ALLOWED to be used" + nota de que NO es una capacidad viva hasta
  Fase B/C. El docstring y el marcador del endpoint apuntan a este ADR (no a un `TODO`
  suelto que parezca un descuido).
- El gate de **consent** se mantiene (un listing no-verified instala `DISABLED`).
- **No** se cambia comportamiento ejecutable: cero regresión.

## Plan de Fase B/C (cuando se aborde)

Requisitos para que el install fresco sea seguro **y** produzca capacidad:

1. **Runner de sandbox fuera del api-server.** El smoke-probe debe ejecutarse donde SÍ hay
   capacidad de lanzar contenedores efímeros — la misma infraestructura que `agent-runtime`
   / los workers `test`/`review` (cap-drop, sin egress, seccomp). El api-server encola el
   probe y lee el verdicto; nunca toca Docker. (Reutiliza el patrón de runtime templates.)
2. **Registro de artefactos vivo** que pueble el root del `LocalArtifactFetcher` (o un
   fetcher remoto) con el manifest + firma de cada versión publicada.
3. **Gestión de la clave de firma de la plataforma** (`MARKETPLACE_SIGNING_PUBLIC_KEY` vía
   Vault) para que el gate de firma de `verified` tenga con qué verificar.
4. **Paso de materialización transaccional (M1):** al pasar a `ENABLED`, crear/upsert la
   fila en `tools`/`skills` del tenant instalador a partir del manifest, con `category`
   válida del catálogo cerrado (ADR 0049), nombre no colisionante, y **provenance**
   (`source_listing_id` / `source_installation_id`) — reutilizando el idioma `forked_from_*`
   ya presente en `Agent`/`Team`. `uninstall`/`revoke` debe desmontar (soft-delete) esa fila
   en la misma transacción, con test de no-orfandad.
5. Cablear `InstallOrchestrator.install()` en `POST /installations` (como ya está `update`),
   y unificar la lógica duplicada de consent/persistencia entre router y orquestador.

## Consecuencias

- **Positivas:** el copy deja de mentir; el gap queda registrado y planificado; no se
  introduce regresión; el orquestador + sus gates ya existen y se prueban (`test_install_flow`),
  listos para reutilizar cuando llegue la infra.
- **Negativas / deuda:** un install fresco sigue sin escanear código (mitigado por el gate de
  consent: un listing no-verified no se habilita sin consentimiento por permiso). El feature
  de marketplace no produce capacidad viva hasta completar la Fase B/C.

## Alternativas descartadas

- **Cablear los gates ya (naïve):** regresa el feature (todo install → 422). Descartado.
- **Saltar los gates cuando falta infra (fail-open):** degradación de seguridad silenciosa,
  contradice el diseño "fail-closed" del orquestador. Descartado.

## Reapertura de la Fase B/C (2026-09-08, `task_mk_10` del plan `remediacion-marketplace-mcp-2026-09-02`)

La auditoría del 2026-09-02 (hallazgo **MK-01 · ALTO**) midió lo que este ADR
dejó abierto: `python_function` y `docker_command` se instalan `enabled` **sin
fila** (`materialize.py`, «diferido honesto»), mientras el runtime **sí** los
ejecutaría si la fila existiera (`tool_wiring.py`). Se vende una capacidad que
no llega. Y una segunda cosa que este ADR no vio: el formato privado de tools
(`tool.yaml`, Plan 09) no lleva `implementation_type` sino
`implementation.runtime` (python, node…), así que un tenant que publica una tool
propia pasa la publicación, la revisión y el consentimiento, y muere al
**habilitar** con `enable cannot materialise its capability` — dos pantallas
después de donde se decidió.

### Las dos opciones

| Opción                                                                                                                                                                                                      | A favor                                                                                                                                                                                                          | En contra                                                                                                                                                                                                                                                                                                                                                       |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **(a) Materializar** `python_function`/`docker_command` con el gate de revisión que YA existe (`workers/marketplace_gates.py` en la lane `marketplace`, cola de revisión de `routers/marketplace/admin.py`) | El runtime ya los ejecuta; el sandbox del ADR 0081 §1 existe hoy como task fuera del api-server (prod-13); la fila `Tool` sería el ancla de gobernanza de siempre (ADR 0052/0101).                               | **Reabre la superficie de ejecución de código arbitrario del marketplace** (riesgo escrito en el plan): hay que MEDIR que el gate se aplica a TODO listing antes de la fila, no sólo al `async_gates` opt-in; el registro de artefactos (§2) y la clave de firma (§3) siguen sin cablear. Es una **decisión de producto y de seguridad**, no de implementación. |
| **(b) Rechazar** esos manifiestos en la publicación privada hasta que exista (a), y que «Instaladas» no muestre `enabled` a un ítem sin fila                                                                | Honesto y barato: el fallo aparece donde se decide, con motivo y ADR; cero superficie nueva; el catálogo oficial ya no publica nada de estos tipos salvo Playwright, que va por su propio despliegue (ADR 0142). | No da capacidad: un tenant que quiera una tool Python propia sigue sin poder. Es exactamente el estado de hoy, dicho en voz alta.                                                                                                                                                                                                                               |

### Lo que se aplica ahora: (b)

La casilla `task_mk_10` lo ordena así («mientras no se decida, aplicar (b)») y la
orden permanente del operador reserva las decisiones de producto: **(a) queda
propuesta al operador**, no tomada. Lo implementado el 2026-09-08:

1. `marketplace/capability.py::capability_of` clasifica por dónde llega la
   capacidad de un listing: `catalog_row` (skills, tools de red), `on_deploy`
   (servidores MCP — ADR 0166 D6 — y listings con validador tipado en su
   `config_schema`, como Playwright) o `deferred` (código arbitrario sin
   sandbox). Resuelve el formato privado (`implementation.runtime` ⇒ código).
2. `GET /marketplace/installations` devuelve `capability` y `capability_reason`
   (LEFT JOIN al listing), y la pestaña «Instaladas» pinta «Autorizada, sin
   capacidad» en vez de «Habilitada» cuando `enabled` + `deferred`.
3. `POST /marketplace/private/listings` rechaza con **422 y motivo** un manifiesto
   de `kind: tool` con `implementation.runtime`; un `mcp_server` privado sigue
   pasando (su capacidad la crea el import del despliegue).

`materialize_installation` **no cambia**: el «diferido honesto» sigue ahí para
las filas que ya existen en el catálogo oficial o en instalaciones anteriores.

### Qué necesita (a) para dejar de ser propuesta

Los cinco puntos de §«Plan de Fase B/C» siguen vigentes, y el 1 está parcialmente
hecho (la lane `marketplace` ejecuta el sandbox fuera del api-server desde
prod-13). Lo que falta medir antes de firmar (a): que **ningún** camino de
instalación de un listing con código llegue a `ENABLED` sin haber pasado el
gate de sandbox —hoy `async_gates` es opt-in (`schemas/marketplace.py:143`)— y
que el `security_level` de la fila resultante nazca `sandboxed` y el runtime lo
respete. Con eso medido, (a) es una casilla de implementación; sin eso, es un
agujero con un nombre bonito.
