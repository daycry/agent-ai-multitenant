---
title: "ADR 0144: Propagación de un secreto rotado — regenerar el entorno y reiniciar, no leer Vault en caliente"
status: accepted
date: 2026-07-31
deciders: [claude-code]
relates_to: [0012, 0136, 0143]
plan_referenced: prod-05-rotacion-claves
task: task_prod05_06
---

# ADR 0144: Propagación de un secreto rotado

> **Estado: `accepted`.** Decisión **técnica de arquitectura de despliegue**, sin
> impacto en producto: nadie ve nada distinto en la UI y no cambia ningún
> contrato de API. **Opción elegida: B — el valor rotado se propaga regenerando
> el `.env` y reiniciando los servicios afectados en la misma ventana.**

## Contexto verificado (2026-07-31)

El hallazgo gap2-2 dice que aunque el job de rotación escribiera de verdad en
Vault, **nada propagaría el valor rotado**:

- `api_server.config.get_settings` está decorado con `@lru_cache(maxsize=1)`: la
  configuración se lee **una vez por proceso**. Lo mismo en
  `workers.config.get_settings` y en el dispatcher.
- Nadie lee `secret/platform/jwt` ni `platform/minio` en runtime. Las claves
  llegan por variable de entorno, inyectadas en el arranque del contenedor.

Es decir: hoy la propagación **es** un reinicio, solo que uno que nadie ha
documentado ni automatizado.

Además, el runbook vigente afirmaba lo contrario — «los servicios siguen
autenticando … **sin reinicio**» (`05-key-rotation.md`, sección «Verificación de
un ciclo»). Esa frase describía un comportamiento que el código no tiene y que
nunca ha tenido.

## Opciones

### Opción A — los servicios leen Vault en runtime, con recarga periódica

- Elegante: rotar deja de necesitar un reinicio, y una revocación de emergencia
  surtiría efecto en la siguiente recarga.
- Invasiva de verdad: hay que romper el patrón `@lru_cache` en **todo** el
  código que lee `get_settings()` (cientos de llamadas en tres apps), decidir qué
  pasa con una request en vuelo cuando el valor cambia a mitad, y añadir a Vault
  al camino crítico de cada proceso — un Vault sellado pasaría de «no se pueden
  resolver credenciales nuevas» a «los servicios no pueden arrancar ni recargar».
- Aporta poco en el alcance real: **Docker Compose en una sola máquina**
  (CLAUDE.md). No hay flota, no hay rolling deploy, no hay decenas de réplicas
  que reiniciar de una en una. `docker compose up -d api-server workers` tarda
  segundos.

### Opción B — regenerar el entorno + reinicio coordinado (elegida)

- El paso de propagación es explícito, auditable y reversible: se ve en el
  `.env`, se ve en el `docker compose`, y volver atrás es volver a poner el valor
  anterior.
- El corte que un reinicio implicaría **ya está resuelto por otra vía**: la
  aceptación dual de `task_prod05_04` significa que la clave antigua sigue
  validando, así que ni las sesiones humanas ni los `AGENTIC_INTERNAL_TOKEN` en
  vuelo se rompen durante la ventana. Esa es la razón por la que B deja de ser
  «la opción con corte».
- Coste: la ventana de reinicio existe (segundos de 502 detrás de Caddy) y el
  paso es manual mientras no haya script.

## Decisión

**Opción B.** El argumento no es «A es peor», es que **A resuelve un problema que
este despliegue no tiene** a cambio de meter Vault en el camino crítico de
arranque de tres servicios. Con dual-accept ya implementado, la única ventaja
real de A sobre B — no cortar nada — desaparece.

Si algún día el alcance cambia a multi-máquina, este ADR se supersede: entonces
«reiniciar todos los servicios en la misma ventana» deja de ser una frase de una
línea.

## Consecuencias

- La rotación de un secreto de firma/cifrado es un procedimiento de **tres pasos
  con un reinicio en medio**, y el runbook `05-key-rotation.md` lo describe así,
  clave por clave.
- El job de rotación marca cada entrada KV que escribe con
  **`pending_apply=true`** (`workers.credential_rotation_hvac`). El marcador
  distingue «rotado en Vault» de «rotado y en vigor en los servicios», que es
  justo la distinción que gap2-2 echaba en falta. La propagación lo pone a
  `false`.
- Para MinIO, la propagación es además la frontera del patrón
  **add-then-remove**: la credencial antigua se revoca **después** del reinicio,
  con `revoke_previous_minio_credential`. Revocar antes deja sin object storage a
  toda la plataforma.
- La afirmación «sin reinicio» del runbook anterior **se retira**: era falsa.

## Lo que este ADR NO entregaba — cerrado el 2026-08-10

> Esta sección decía que `scripts/rotate-platform-secret.sh` **no estaba
> escrito** y que la propagación era «un procedimiento humano documentado, no un
> comando». Ya no: el script existe.

`scripts/rotate-platform-secret.sh <jwt|minio>` implementa la opción B de este
ADR de punta a punta: lee el valor de `secret/platform/<nombre>`, reescribe el
`.env` —**anteponiendo** la clave JWT nueva y conservando el anillo anterior, o
escribiendo las dos mitades de la credencial MinIO—, reinicia los servicios
afectados en la misma ventana y, **sólo para MinIO y sólo después del
reinicio**, revoca la anterior vía `python -m workers.rotation_apply
--revoke-previous-minio`.

Ese orden es la razón de ser del script y está pineado por
`tests/unit/test_rotate_platform_secret_script.py`
(`test_minio_revocation_happens_only_after_the_restart`): el fallo que se
pretende hacer imposible no es teclear mal, es **invertir los pasos 2 y 3 bajo
presión**, que deja a la plataforma entera sin object storage.

Lo que sigue siendo manual **a propósito**: la retirada de la clave JWT antigua
del anillo (paso 3 de §1 del runbook). Depende de un reloj —el TTL máximo de
token en vuelo— y automatizarla convertiría una decisión en un efecto secundario.

Sigue sin entregarse: el reinicio del **stack de dev/manuales** no se beneficia
del script, porque ese compose lleva los secretos incrustados en línea en vez de
leerlos del `.env`.
