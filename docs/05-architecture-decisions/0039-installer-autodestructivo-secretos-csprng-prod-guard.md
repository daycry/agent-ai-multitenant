---
adr: "0039"
title: Instalador en contenedor autodestructivo, credenciales mostradas una sola vez y secretos CSPRNG generados para pasar el guard de secretos-dev en producción
status: accepted
date: 2026-05-31
deciders: System Architect, Security, DevOps
phase: 15-instalador-produccion
---

# ADR 0039 — Instalador autodestructivo, credenciales una sola vez y secretos CSPRNG que pasan el guard de producción

> **Estado: `accepted`.** Recoge tres decisiones tomadas durante el Plan 15
> (Fase A — wizard del instalador; Fase B — generadores de config) que no
> estaban registradas en un ADR previo: el **contenedor installer temporal que
> se autodestruye** tras revelar las credenciales, la **revelación de
> credenciales + unseal keys EXACTAMENTE una vez sin recuperación**, y la
> **generación de secretos de alta entropía (CSPRNG) diseñada para pasar el
> guard de secretos-dev en producción** del Plan 06.14. El guard de
> secretos-dev arranca del hardening del Plan 06.14; el almacenamiento de
> secretos en Vault es la **ADR 0003**.

## Contexto

El Plan 15 hace el sistema **instalable por terceros sin asistencia**. El
instalador es la primera y única pieza que un operador ejecuta antes de que el
stack exista, y por tanto la que **fabrica todo el material secreto inicial**
(contraseña del admin del tenant inicial, secretos de PostgreSQL / MinIO / JWT /
SSO / notificaciones / webhooks, y las unseal keys de Vault). Tres cuestiones de
diseño no quedaban cerradas por ADRs previos:

1. **¿Qué pasa con la UI temporal del instalador y con la copia en memoria de
   los secretos una vez terminada la instalación?** Dejar un servidor web con
   acceso al árbol `/data` y conocimiento de las credenciales corriendo
   indefinidamente es una superficie de ataque permanente.
2. **¿Cómo se entregan al operador las credenciales y las unseal keys?** El
   modelo de seguridad de Vault (ADR 0003) exige que las unseal keys no se
   persistan en claro; si el instalador las guardara en disco anularía la
   garantía.
3. **¿Cómo se garantiza que un `.env` de producción generado por el instalador
   NO dispare el guard de secretos-dev** que el Plan 06.14 introdujo para
   rechazar arranques de `staging`/`prod` con marcadores `changeme` /
   `dev-only` / `minioadmin`?

## Decisión

### 1. Contenedor installer temporal y autodestructivo

El instalador vive en un **contenedor separado** (`apps/installer`, Next.js +
FastAPI mínimo, `docker-compose.installer.yml`) que sirve la UI del wizard sobre
loopback. NO forma parte del stack runtime. Tras la revelación de credenciales
del paso 9 (`finalize.py`), el servicio **se autodestruye**: la lógica de
self-destruct vive detrás del seam `InstallerLifecycle` (mockeado en tests; el
binding real señala al compose bootstrap para parar + eliminar el contenedor
installer). Así la UI temporal y la copia en memoria de los secretos
desaparecen en cuanto el operador los ha visto.

### 2. Credenciales y unseal keys reveladas EXACTAMENTE una vez, sin recuperación

`FinalizeService` es una máquina de estados de un solo disparo:

```
not-installed ──arm()──▶ armed ──reveal()──▶ revealed (+ self-destruct)
```

- `arm(credentials)` lo invoca la orquestación de instalación en el momento en
  que el pipeline alcanza su estado terminal de éxito. Antes de eso el servicio
  está _not-installed_ y `reveal()` se niega (`InstallNotCompleteError`) — una
  instalación incompleta NUNCA revela credenciales ni se autodestruye.
- `reveal()` devuelve el payload la **primera** vez y acto seguido invalida la
  copia en memoria e invoca el self-destruct. Una segunda llamada recibe
  `CredentialsAlreadyRevealedError` — el payload de un solo uso ya no existe.
- `InstallCredentials` vive **solo en memoria**, nunca se escribe a `/data` ni a
  ningún fichero, y nunca se loguea: su `__repr__`/`__str__` están redactados
  para que un `log.info(creds)` accidental o un frame de traceback no filtre los
  valores. Los secretos reales los persiste el bootstrap de Vault (Fase B), no
  este módulo. **El operador es responsable de guardarlos: no hay recuperación.**

### 3. Secretos CSPRNG diseñados para pasar el guard de producción

Cada secreto generado se extrae de un CSPRNG (`secrets.token_urlsafe`, ≥ 32
bytes ⇒ ≥ 256 bits de entropía), es **único por instalación** (un
`GeneratedSecrets` fresco por llamada a `generate_secrets`) y URL-safe (limpio
para `.env`: sin padding, sin comillas, sin substrings de marcador posibles).
El generador conoce los marcadores que el guard de secretos-dev del Plan 06.14
rechaza (`changeme` / `dev-only` / `minioadmin`) y garantiza que un `.env` de
producción no contiene ninguno. Además mapea el `Environment` del instalador
(`production`/`staging`/`development`) al valor `ENVIRONMENT` que esperan los
servicios (`prod`/`staging`/`dev`), de modo que una instalación de producción
emite `ENVIRONMENT=prod` y el guard se arma. La escritura del `.env` va por el
seam `EnvFileWriter` (mockeado en tests; el binding real escribe a disco solo en
tiempo de instalación, NUNCA se commitea ni se loguea en claro).

## Alternativas consideradas

- **Instalador persistente integrado en el panel admin.** Rechazada: deja una
  superficie de configuración privilegiada corriendo siempre y mezcla el ciclo
  de bootstrap con el runtime.
- **Persistir las unseal keys cifradas para "recuperación".** Rechazada:
  contradice el modelo de Vault (ADR 0003) — quien tenga el material de cifrado
  más las keys cifradas tiene Vault. El operador guarda las keys fuera del
  sistema.
- **Validar el `.env` post-hoc con el guard en vez de generarlo limpio.**
  Rechazada: prevenir en origen es más barato que detectar; el generador es la
  única fuente del `.env` de producción.

## Consecuencias

- **Positivas.** Cero superficie de instalador residual. Las credenciales nunca
  tocan disco ni logs. Un `.env` de producción generado arranca sin tropezar con
  el guard de secretos-dev. La lógica vive en Python unit-testeable detrás de
  seams (la verificación real de self-destruct / escritura a disco es un test
  humano del plan).
- **Negativas / asunciones.** El operador DEBE capturar las credenciales y
  unseal keys en el único momento en que se muestran; perderlas obliga a un
  `vault operator rekey` (runbook `05-key-rotation.md`) o a una reinstalación.
  La autodestrucción real y la escritura a disco solo se ejercitan en un stack
  vivo (Tests Humanos `human_15_01` / `human_15_02`).

## Verificación

- `tests/integration/test_installer_finalize.py` — máquina de estados del
  reveal (no revela antes de completar; revela una vez; segundo intento falla;
  self-destruct se invoca; repr redactado).
- `tests/unit/test_config_generators.py` — secretos únicos de alta entropía sin
  marcadores de dev; mapeo de environment; estructura del `.env` / `global.yaml`.
- `tests/security/test_pentest_findings.py` — invariante: los perfiles de
  instalación de producción y la config generada no llevan marcadores de
  secreto-dev.
