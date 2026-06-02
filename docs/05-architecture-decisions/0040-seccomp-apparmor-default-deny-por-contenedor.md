---
adr: "0040"
title: Endurecimiento por contenedor con seccomp default-deny y perfiles AppArmor MAC, con el runtime no confiable como subconjunto estricto del perfil compartido
status: accepted
date: 2026-05-31
deciders: System Architect, Security, DevOps
phase: 15-instalador-produccion
---

# ADR 0040 — Seccomp default-deny + AppArmor MAC por contenedor

> **Estado: `accepted` (revisado 2026-05-31).** Recoge la decisión tomada
> durante el Plan 15 (Fase C — endurecimiento de seguridad, `task_15_15` +
> `task_15_16`) de añadir **dos capas de confinamiento del kernel** a cada
> contenedor: un **perfil seccomp por contenedor** y un **perfil AppArmor (MAC)
> por contenedor**, con el contenedor `agent-runtime` (que ejecuta código no
> confiable del usuario) confinado por un **subconjunto estricto** del perfil
> seccomp más estricto. Es una profundización del aislamiento por contenedor de
> la **ADR 0012** y del egress sandbox de la **ADR 0019**, y cierra hallazgos del
> pentest interno (`task_15_14`).
>
> **⚠ Revisión (2026-05-31): seccomp diferenciado confiable vs. no confiable.**
> La decisión original aplicaba **un perfil seccomp hand-rolled default-deny
> (`docker/seccomp/default.json`) a TODOS los servicios**, incluidos los de
> plataforma confiables. **Eso fue un error y se ha corregido.** Ver
> [Revisión](#revisión-2026-05-31--seccomp-confiable-vs-no-confiable).

## Contexto

El principio rector §2 (aislamiento por contenedor) ya exigía `cap-drop ALL`,
sin socket Docker y `no-new-privileges`. Pero el seccomp **por defecto** de
Docker es **permisivo** (lista de bloqueo, ~44 syscalls bloqueadas de >300) y no
había confinamiento MAC (Mandatory Access Control). Para un contenedor que
ejecuta código arbitrario del usuario (`agent-runtime`), eso deja abierta una
amplia superficie de syscalls (`mount`, `ptrace`, `kexec_load`, `init_module`,
`setns`, `unshare`…) que habilitan escapes de contenedor y manipulación del
host. El pentest interno (`task_15_14`) lo marcó como el hallazgo de mayor
prioridad para producción.

Dos cuestiones de diseño no quedaban cerradas:

1. **¿Un perfil seccomp único para todos, o uno por contenedor?** Un servicio de
   plataforma (PostgreSQL, api-server) necesita syscalls que un runtime de
   agente no confiable jamás debería poder invocar.
2. **¿Cómo se valida el hardening si el kernel-enforcement real no corre en CI**
   (no hay host Linux privilegiado con seccomp/AppArmor cargado ni harness de
   escape en el entorno de CI)?

## Decisión

### Seccomp default-deny por contenedor

> **⚠ Esta subsección describe la decisión ORIGINAL; ver
> [Revisión](#revisión-2026-05-31--seccomp-confiable-vs-no-confiable) para el
> estado vigente.** En concreto: `default.json` **ya NO se cablea a los
> servicios confiables** (que usan el seccomp por defecto de Docker); pasó a ser
> un perfil **opt-in**. El subconjunto estricto del runtime no confiable
> (`agent-runtime.json`) se mantiene y es lo que de verdad importa.

Los perfiles viven bajo `docker/seccomp/`:

- `default.json` — perfil default-deny **opt-in** (originalmente el perfil
  compartido de los servicios de plataforma), con `defaultAction:
SCMP_ACT_ERRNO` (**default-deny**: cualquier syscall fuera de la allowlist se
  rechaza, lo opuesto al default permisivo de Docker). **Revisado:** ya no se
  aplica a los servicios confiables por defecto (ver Revisión).
- `agent-runtime.json` — el perfil del runtime **no confiable**, un
  **subconjunto estricto** del `default.json` (solo puede hacer _menos_), pero
  con los esenciales de arranque para que un contenedor no confiable arranque.

La familia de syscalls peligrosas (`mount`, `ptrace`, `kexec_load`, `bpf` donde
no se necesita, `init_module`, `setns`, `unshare`…) NO está en ninguna
allowlist. El seam de aislamiento del worker reenvía el **contenido** del perfil
no confiable al daemon. (En la decisión original cada servicio de larga vida
referenciaba `default.json` vía `security_opt: seccomp=…`; **eso se revirtió**,
ver Revisión.)

### AppArmor (MAC) por contenedor

Los perfiles viven bajo `docker/apparmor/`:

- `agentic-default.profile` — el perfil compartido de los servicios de
  plataforma.
- `agent-runtime.profile` — el perfil del runtime no confiable, **más estricto**
  que el default.

Cada perfil **deniega** las primitivas de escape / manipulación del host
(`mount`, `pivot_root`, `ptrace`, módulos del kernel, I/O raw, el socket Docker,
escrituras a `/proc/sys` y `/sys`) y **confina** las escrituras a un conjunto
acotado de directorios (no un `/** rw` general). El perfil del agent-runtime
solo escribe `/workspace` + `/tmp` y deniega escrituras a `/var/lib` / `/data` /
`/root`. Cada servicio de larga vida referencia su perfil vía
`security_opt: apparmor=…`; el generador de compose lo emite; el seam de
aislamiento del worker reenvía el **nombre** del perfil al daemon.

### Validación estructural en CI, enforcement real como test humano

El enforcement-por-el-kernel real NO corre en CI. En su lugar las suites de
seguridad **validan la postura estructuralmente** y fallan ante una regresión:
JSON válido con default-deny, syscalls peligrosas ausentes, el agent-runtime
como subconjunto estricto, sintaxis AppArmor bien formada, negaciones presentes,
referencias `security_opt` en todos los servicios de los compose de producción,
y el generador del instalador emitiéndolas. El enforcement real es un test
humano documentado (`internal-pentest-methodology.md` §5 +
`apparmor-profiles.md`) confirmado en un host Linux con seccomp/AppArmor activos.

## Alternativas consideradas

- **Mantener el seccomp permisivo por defecto de Docker.** Rechazada: deja >250
  syscalls disponibles para código no confiable.
- **Un único perfil para todos los contenedores.** Rechazada: un servicio de
  plataforma necesita syscalls que un agente no confiable no debe tener; el
  least-privilege exige perfiles diferenciados.
- **Solo seccomp, sin AppArmor.** Rechazada: seccomp filtra syscalls pero no
  confina rutas de fichero ni capacidades del filesystem; las dos capas son
  complementarias (defensa en profundidad).

## Consecuencias

- **Positivas.** Superficie de syscalls drásticamente reducida; el runtime no
  confiable confinado por least-privilege en dos capas; un retroceso de
  hardening (quitar una referencia, ablandar el default-deny, añadir una syscall
  peligrosa) hace fallar la suite antes del merge.
- **Negativas / asunciones.** El enforcement real depende de un host Linux con
  seccomp/AppArmor cargado (no Docker Desktop/Windows); la validación de CI es
  estructural, no de comportamiento del kernel. Añadir una syscall necesaria a un
  servicio exige editar su perfil (coste de mantenimiento deliberado a favor del
  least-privilege).

## Revisión (2026-05-31) — seccomp confiable vs. no confiable

**Malfunción confirmada contra el stack vivo.** El perfil hand-rolled
default-deny `docker/seccomp/default.json` se aplicaba **forzosamente a TODOS
los servicios** (vía el anchor `x-seccomp` de `docker-compose.yml`, el overlay
`docker-compose.monitoring.yml` y el generador del instalador). Esa allowlist
estaba **incompleta para imágenes reales de plataforma**:

- **PostgreSQL** entraba en crash-loop: `FATAL: signalfd() failed` y luego
  `shmget: Operation not permitted` (le faltaban `signalfd`/`signalfd4` y la IPC
  SysV en la práctica del proceso real).
- **Vault y MinIO** (servicios Go) **SIGSEGV** (exit 139) por syscalls de runtime
  ausentes que el runtime de Go necesita al arrancar.

El orquestador verificó que **cambiar los servicios confiables al perfil seccomp
por defecto de Docker** arregla el arranque (postgres/redis/vault `healthy`).

**El modelo de amenazas correcto** (CLAUDE.md principio §2): una allowlist
estricta de syscalls es para los **runtimes no confiables** (agent/test/review)
que ejecutan código hostil — **no** para los servicios de plataforma de primera
parte (imágenes oficiales: postgres, redis, minio, vault, clamav, docling,
api-server, workers, prometheus, grafana, …). Esos servicios confiables deben
usar el **seccomp por defecto de Docker** (una allowlist de ~350 syscalls probada
en producción que ya deniega `mount`/`ptrace`/`kexec`/`bpf`/… mientras hace
funcionar las imágenes) más `no-new-privileges` + `cap_drop` + AppArmor.

### Decisión revisada

1. **Servicios confiables (plataforma):** dejan de pinear el perfil hand-rolled.
   Su hardening es `no-new-privileges:true` + `apparmor=agentic-default` +
   `cap_drop: [ALL]` + el **seccomp por defecto de Docker** (no se sobrescribe).
   Esto aplica al anchor `x-seccomp` de `docker-compose.yml`, al overlay
   `docker-compose.monitoring.yml` y a lo que **emite el generador del
   instalador** (`compose_generator.py`: ya **no** emite `SECCOMP_DEFAULT_PROFILE`).
2. **Runtime no confiable (agent/test/review):** sigue usando la allowlist
   estricta `docker/seccomp/agent-runtime.json` (subconjunto estricto), que el
   worker pina al lanzar (`workers.isolation`, reenvía el **contenido**). Se le
   **añadieron las syscalls esenciales de arranque** que le faltaban por haber
   sido modelado como subconjunto del default roto (`signalfd`/`signalfd4`,
   `set_tid_address`/`set_robust_list`/`get_robust_list`, `membarrier`), SIN
   añadir ninguna de la familia peligrosa, para que un contenedor no confiable
   pueda realmente arrancar.
3. **`docker/seccomp/default.json` se conserva** como **perfil opt-in de
   endurecimiento extra**, documentado, que **ya no se cablea a ningún servicio
   por defecto**. Un operador puede pinearlo a un servicio confiable **tras
   validarlo en su propio kernel/versión de Docker**. Se mantiene como perfil
   default-deny estructuralmente válido (syscalls peligrosas ausentes) y con su
   test estructural.

> Nota: la **decisión de capas AppArmor** de esta ADR **no cambia**. Solo cambia
> el seccomp de los servicios **confiables** (de hand-rolled default-deny → al
> default de Docker). El least-privilege por capas para el runtime no confiable
> se mantiene intacto.

## Verificación

- `tests/security/test_seccomp_profiles.py` — JSON válido + default-deny +
  syscalls peligrosas ausentes en ambos perfiles + subconjunto estricto del
  agent-runtime + **esenciales de arranque del agent-runtime presentes** +
  servicios confiables con `no-new-privileges` y **sin** pin seccomp
  hand-rolled + el generador emite la misma postura confiable + reenvío del seam
  del contenido del perfil no confiable.
- `tests/security/test_apparmor.py` — perfiles bien formados + negaciones +
  escrituras confinadas + agent-runtime más estricto + referencias en compose.
- `tests/unit/test_compose_generator.py` — el generador emite
  `no-new-privileges` + `apparmor` y **no** un pin seccomp hand-rolled.
- `tests/security/test_pentest_findings.py` — invariantes de aislamiento (sin
  socket Docker, `cap_drop` ALL, `no-new-privileges`, agent network `internal`).
