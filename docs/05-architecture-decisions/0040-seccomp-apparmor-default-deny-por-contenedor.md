---
adr: "0040"
title: Endurecimiento por contenedor con seccomp default-deny y perfiles AppArmor MAC, con el runtime no confiable como subconjunto estricto del perfil compartido
status: accepted
date: 2026-05-31
deciders: System Architect, Security, DevOps
phase: 15-instalador-produccion
---

# ADR 0040 — Seccomp default-deny + AppArmor MAC por contenedor

> **Estado: `accepted`.** Recoge la decisión tomada durante el Plan 15 (Fase C —
> endurecimiento de seguridad, `task_15_15` + `task_15_16`) de añadir **dos
> capas de confinamiento del kernel** a cada contenedor: un **perfil seccomp
> default-deny por contenedor** y un **perfil AppArmor (MAC) por contenedor**,
> con el contenedor `agent-runtime` (que ejecuta código no confiable del
> usuario) confinado por un **subconjunto estricto** del perfil compartido. Es
> una profundización del aislamiento por contenedor de la **ADR 0012** y del
> egress sandbox de la **ADR 0019**, y cierra hallazgos del pentest interno
> (`task_15_14`).

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

Los perfiles viven bajo `docker/seccomp/`:

- `default.json` — el perfil compartido de los servicios de plataforma, con
  `defaultAction: SCMP_ACT_ERRNO` (**default-deny**: cualquier syscall fuera de
  la allowlist se rechaza, lo opuesto al default permisivo de Docker).
- `agent-runtime.json` — el perfil del runtime no confiable, un **subconjunto
  estricto** del default (solo puede hacer _menos_).

La familia de syscalls peligrosas (`mount`, `ptrace`, `kexec_load`, `bpf` donde
no se necesita, `init_module`, `setns`, `unshare`…) NO está en ninguna
allowlist. Cada servicio de larga vida en los compose de producción referencia
su perfil vía `security_opt: seccomp=…`; las excepciones host-agent
(cAdvisor / node-exporter) están documentadas. El generador de compose del
instalador (`task_15_07`) **emite la misma referencia**, y el seam de
aislamiento del worker reenvía el **contenido** del perfil al daemon.

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

## Verificación

- `tests/security/test_seccomp_profiles.py` — JSON válido + default-deny +
  syscalls peligrosas ausentes + subconjunto estricto del agent-runtime +
  referencias en compose + emisión del generador + reenvío del seam.
- `tests/security/test_apparmor.py` — perfiles bien formados + negaciones +
  escrituras confinadas + agent-runtime más estricto + referencias en compose.
- `tests/security/test_pentest_findings.py` — invariantes de aislamiento (sin
  socket Docker, `cap_drop` ALL, `no-new-privileges`, agent network `internal`).
