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
