---
adr: "0041"
title: Rotación automática de credenciales con Vault dynamic secrets, detrás de un seam, schedule en config y fail-safe (el motor nunca tira el sistema)
status: accepted
date: 2026-05-31
deciders: System Architect, Security, DevOps
phase: 15-instalador-produccion
---

# ADR 0041 — Rotación automática de credenciales con Vault dynamic secrets

> **Estado: `accepted`.** Recoge la decisión tomada durante el Plan 15 (Fase C —
> endurecimiento de seguridad, `task_15_17`) de **rotar credenciales
> automáticamente** usando el **database secrets engine de Vault** (credenciales
> dinámicas de PostgreSQL con TTL corto) más la rotación de los secretos
> estáticos (MinIO / JWT), orquestada por un **job Celery beat** cuyo cadence
> vive en config y gobernada por un **lever de plataforma OFF/ON en vivo**, con
> el motor diseñado **fail-safe** (un fallo de rotación nunca tira el sistema, y
> dispara una alerta). Reusa el almacenamiento de secretos de Vault (**ADR
> 0003**) y el notificador del Plan 10 (**ADR 0034**).

## Contexto

Hasta el Plan 15, los secretos del stack (DSN de PostgreSQL, claves de MinIO,
firma JWT) eran **estáticos**: se generaban en la instalación y vivían
indefinidamente. Una credencial estática de larga vida que se filtra (un log, un
backup, un dump) es válida hasta que alguien la rota a mano. El hardening de
producción exige **rotación automática**, y Vault ofrece el patrón canónico: el
**database secrets engine** que **acuña roles de PostgreSQL efímeros** con TTL
corto bajo demanda, de modo que una credencial filtrada caduca sola.

Cuestiones de diseño no cerradas por ADRs previos:

1. **¿Cómo se rota sin acoplar el código de la plataforma a un Vault vivo** (que
   no puede correr en CI)?
2. **¿Quién dispara la rotación y con qué cadencia**, y cómo se apaga en caliente
   si algo va mal?
3. **¿Qué pasa si una rotación falla** — ¿se cae el sistema?

## Decisión

### Credenciales dinámicas + rotación de estáticos, detrás de un seam

El cliente de Vault vive detrás del seam `VaultRotationClient`
(`workers.credential_rotation`). El **database secrets engine** configura un
**ROLE** con TTL corto, ligado a la conexión PostgreSQL, que acuña credenciales
de un solo uso. Un **ciclo de rotación**:

1. emite una nueva credencial dinámica;
2. **renueva** y luego **revoca** el lease anterior (la credencial vieja
   caduca);
3. **rota in situ** los secretos estáticos (MinIO / JWT).

Los tests inyectan un `FakeVaultRotationClient` determinista — nada habla con un
Vault real en CI (igual que el resto de seams del plan).

### Schedule en config + lever OFF/ON en vivo

La rotación la dispara un **job Celery beat** que está **registrado** y lee su
**cadencia desde config** (nunca un schedule hardcodeado). El job honra el
**platform setting `cred_rotation_enabled`** en vivo: un operador puede apagar la
rotación sin redeploy. Ese lever OFF/ON es el único camino que se prueba contra
el Postgres real de test (el resto de Vault está mockeado).

### Fail-safe: el motor nunca tira el sistema

Un fallo de rotación **nunca propaga una excepción que tumbe el servicio**: el
motor lo captura, mantiene el sistema arriba con la credencial vigente y dispara
una **alerta** a través del notificador del Plan 10 (ADR 0034). Los secretos
**nunca se loguean en claro**: la contraseña acuñada jamás aparece en una línea
de log estructurado, y el repr de la credencial está redactado.

## Alternativas consideradas

- **Solo rotación de secretos estáticos (sin dynamic secrets).** Rechazada: una
  credencial estática rotada periódicamente sigue siendo de larga vida entre
  rotaciones; las credenciales dinámicas con TTL corto acotan la ventana de
  exposición de forma mucho más estrecha.
- **Schedule hardcodeado en el código.** Rechazada: la cadencia de rotación es
  una decisión operativa que debe ajustarse sin redeploy (named constant
  override-able + setting de plataforma).
- **Dejar que un fallo de rotación pare el servicio (fail-closed).** Rechazada
  para este caso: un fallo del motor de rotación NO debe causar una caída de
  disponibilidad; se prefiere alertar y seguir con la credencial vigente
  (fail-safe), y que el operador intervenga vía runbook.

## Consecuencias

- **Positivas.** La ventana de validez de una credencial filtrada se reduce al
  TTL del lease. La rotación se opera sin redeploy (config + lever). Un fallo de
  rotación es visible (alerta) sin tumbar el sistema. El código es testeable sin
  Vault (seam).
- **Negativas / asunciones.** La rotación real de credenciales contra un Vault
  vivo + un PostgreSQL real con el database secrets engine es un **test humano /
  de stack** (`human_15_03`); CI solo valida la mecánica del motor con un cliente
  fake y el lever contra el Postgres de test. El runbook `05-key-rotation.md`
  documenta la rotación de unseal keys + credenciales y la revocación de
  emergencia.

## Verificación

- `tests/integration/test_credential_rotation.py` — configuración del role del
  database engine; ciclo de rotación (emite + renueva + revoca + rota
  estáticos); job beat registrado leyendo cadence de config + honrando el lever;
  fallo de rotación manejado + alerta; secretos nunca logueados en claro.
- Runbook `docs/06-runbooks/05-key-rotation.md` — procedimiento operativo de
  rotación y revocación.
