---
title: Auditoría integral de producción — Informe ejecutivo y serie de planes correctivos
version: 1.0
audit_date: 2026-06-10
last_updated: 2026-06-11
status: published
created_by: auditoria-claude-2026-06
docs_language: es
---

# Auditoría integral de producción (2026-06) — Informe ejecutivo

## Veredicto

**La calidad del código de aplicación es notablemente alta, pero la plataforma no es desplegable ni operable en producción hoy.** El aislamiento multi-tenant (RLS con FORCE, fail-closed, gate cross-tenant), la autenticación (Argon2id, JWT con sesión server-side), la disciplina de linting (ruff y mypy strict a cero violaciones) y las 84 migraciones (cadena lineal, todas reversibles) están al nivel de un producto maduro. Lo que falla es todo lo que rodea al código: **no existen las imágenes Docker de las apps ni forma de construirlas, el instalador de producción es un simulacro, el CI lleva 12 días muerto y en rojo, los guardrails no se invocan en ningún flujo real, ningún backup completo puede producirse por un bug de una línea, y la rotación de credenciales es un no-op que audita éxito.**

## Metodología

- **16 dimensiones auditadas en paralelo** (multi-tenancy/RLS, auth/RBAC, secretos, sandbox de contenedores, despliegue, BD/migraciones, API, workers/orquestación, capa LLM, guardrails, frontend, tests/CI, observabilidad, calidad de código, docs/roadmap, rendimiento) + **5 auditorías de hueco** propuestas por críticos de completitud (DR/restore, rotación de claves, RPO/consistencia de backup, SSRF en tools, cadena de suministro).
- **Verificación adversarial multi-lente**: cada hallazgo critical/high fue verificado por 2-3 agentes independientes (lentes: refutar, impacto real, reproducibilidad). Todo hallazgo cita evidencia `fichero:línea` leída directamente del repo.
- Volumen: ~300 agentes, ~3.000 lecturas/búsquedas sobre el working tree de `master` (commit `3632994`), solo lectura.

**Resultados de verificación**: 179 hallazgos brutos → 1 refutado (gap4-4) → **178 vigentes**: **9 critical, 52 high** (el 100 % de critical/high verificado adversarialmente; 1 disputado con matiz, gap4-1), **76 medium** (42 verificados; 34 con evidencia directa pero sin pasada de verificación por lotes), **41 low** (con evidencia, sin verificación individual por diseño).

El detalle completo de hallazgos (descripción, evidencia, recomendación y votos de verificación) está exportado en `C:\tmp\auditoria\` (un fichero por área + `merged.json`); cada plan de la serie incorpora el detalle de sus hallazgos.

## Salud por área

| Área                     | Salud       | Área                             | Salud       |
| ------------------------ | ----------- | -------------------------------- | ----------- |
| Multi-tenancy y RLS      | 🟢 good     | Capa LLM y proveedores           | 🟡 fair     |
| Calidad del API server   | 🟢 good     | Frontend admin-panel             | 🟡 fair     |
| Rendimiento              | 🟢 good     | Observabilidad                   | 🟡 fair     |
| AuthN/RBAC               | 🟡 fair     | Calidad de código                | 🟡 fair     |
| Secretos y configuración | 🟡 fair     | Docs y roadmap                   | 🟡 fair     |
| Sandbox de contenedores  | 🟡 fair     | SSRF en tools de agentes         | 🟡 fair     |
| BD y migraciones         | 🟡 fair     | **Despliegue/operación**         | 🔴 **poor** |
| Workers y orquestación   | 🟡 fair     | **Guardrails/validación humana** | 🔴 **poor** |
| **Tests y CI**           | 🔴 **poor** | **Backup/DR (restore, RPO)**     | 🔴 **poor** |
| **Rotación de claves**   | 🔴 **poor** | **Cadena de suministro**         | 🔴 **poor** |

## Los 9 hallazgos críticos (todos confirmados)

| #   | Hallazgo                                                                                                                                                | Plan que lo cierra |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------ |
| 1   | El instalador de producción es un simulacro: stubs que imprimen credenciales falsas (`deploy-1`)                                                        | prod-01            |
| 2   | No hay forma de ejecutar las apps en producción: las imágenes `ghcr.io/agentic-platform/*` no existen, sin Dockerfiles ni pipeline (`deploy-2`)         | prod-01            |
| 3   | El motor de guardrails (4 hooks) no se invoca en ningún flujo de producción — Principio nº10 incumplido (`guardrails-1`)                                | prod-03            |
| 4   | Las 4 plantillas de validación humana no gatean nada: vocabularios de categorías incompatibles y fail-open — Principio nº11 incumplido (`guardrails-2`) | prod-03            |
| 5   | CI no se ejecuta en el flujo real: los 3 workflows disparan sobre `main` pero la rama es `master` (`tests-1`)                                           | prod-02            |
| 6   | CI en rojo en ~19 runs consecutivos desde 2026-05-29 y se mergeó igualmente (`tests-2`)                                                                 | prod-02            |
| 7   | El `tar` del backup omite `--create`: ningún backup completo real puede producirse; los tests no lo detectan porque inyectan un runner fake (`gap3-1`)  | prod-04            |
| 8   | Dependencia circular del DR: la clave que descifra el backup vive dentro del propio backup o en el `.env` de la máquina perdida (`gap1-1`)              | prod-04            |
| 9   | El job de rotación de credenciales es un no-op que audita SUCCEEDED: siempre usa el `FakeVaultRotationClient` (`gap2-1`)                                | prod-05            |

## Acciones inmediatas recomendadas (antes de cualquier plan)

1. **Retirar `vault-init-output/` del working tree** y custodiar la clave offsite; el root token de Vault lleva semanas en claro en disco (no está trackeado en git, pero es legible por cualquier proceso/agente con acceso al repo). Re-inicializar Vault. (Se formaliza en prod-10, tarea 1.)
2. **No considerar protegido ningún merge**: el CI no se ejecuta desde 2026-05-29 (triggers sobre `main`). Hasta cerrar prod-02, ejecutar la suite localmente antes de cada merge.
3. **No prometer capacidad de backup**: hoy ningún backup completo puede producirse (bug `tar`) y el restore nunca se ha ejecutado end-to-end.

## Serie de planes correctivos (16 planes, 233 tareas, ~248 persona-días)

Todos en `status: pending_approval` — **ningún plan se ha empezado; requieren aprobación humana** según el protocolo de CLAUDE.md (solo uno `in_progress` a la vez).

| Plan                                                                                  | Prio | Tareas | Esfuerzo | Duración | Bloqueado por |
| ------------------------------------------------------------------------------------- | ---- | ------ | -------- | -------- | ------------- |
| [prod-01-despliegue-ejecutable](./prod-01-despliegue-ejecutable.md)                   | P0   | 20     | 23 pd    | 5-6 sem  | —             |
| [prod-02-ci-en-verde](./prod-02-ci-en-verde.md)                                       | P0   | 12     | 9 pd     | 2-3 sem  | —             |
| [prod-03-guardrails-validacion-humana](./prod-03-guardrails-validacion-humana.md)     | P0   | 16     | 18,5 pd  | 4 sem    | —             |
| [prod-04-backup-dr-restaurable](./prod-04-backup-dr-restaurable.md)                   | P0   | 14     | 17 pd    | 3-4 sem  | prod-01       |
| [prod-05-rotacion-claves](./prod-05-rotacion-claves.md)                               | P0   | 10     | 13 pd    | 3-4 sem  | —             |
| [prod-06-ciclo-vida-ejecucion](./prod-06-ciclo-vida-ejecucion.md)                     | P1   | 16     | 20 pd    | 4-5 sem  | —             |
| [prod-07-fiabilidad-llm-costes](./prod-07-fiabilidad-llm-costes.md)                   | P1   | 16     | 14 pd    | 3-4 sem  | —             |
| [prod-08-observabilidad-alertas](./prod-08-observabilidad-alertas.md)                 | P1   | 16     | 18 pd    | 3-4 sem  | prod-01       |
| [prod-09-sesiones-autorizacion-frontend](./prod-09-sesiones-autorizacion-frontend.md) | P1   | 18     | 20 pd    | 4-5 sem  | —             |
| [prod-10-vault-secretos-operables](./prod-10-vault-secretos-operables.md)             | P1   | 12     | 13 pd    | 3-4 sem  | —             |
| [prod-11-cadena-suministro](./prod-11-cadena-suministro.md)                           | P1   | 13     | 10 pd    | 2-3 sem  | prod-02       |
| [prod-12-hardening-tools-agentes](./prod-12-hardening-tools-agentes.md)               | P1   | 13     | 17 pd    | 3-4 sem  | —             |
| [prod-13-rendimiento-y-datos](./prod-13-rendimiento-y-datos.md)                       | P1   | 23     | 21 pd    | 3-4 sem  | —             |
| [prod-14-tenancy-defensa-profundidad](./prod-14-tenancy-defensa-profundidad.md)       | P2   | 11     | 7 pd     | 7-10 d   | —             |
| [prod-15-gobernanza-roadmap-docs](./prod-15-gobernanza-roadmap-docs.md)               | P2   | 11     | 9 pd     | 2-3 sem  | —             |
| [prod-16-frontend-i18n-calidad](./prod-16-frontend-i18n-calidad.md)                   | P2   | 12     | 18,5 pd  | 3-4 sem  | —             |
| [prod-17-bucle-ai-reviewer](./prod-17-bucle-ai-reviewer.md)                           | P2   | 7      | 10-13 pd | 2-3 sem  | prod-06       |

Coste humano estimado total: **~112.000–149.000 €** (a 450-600 €/persona-día). Cada hallazgo vigente está asignado a exactamente un plan (verificado automáticamente; `db-9` se cierra en prod-14 y prod-13 lo referencia). **prod-17** se añadió después (2026-06-26): es la parte A de `task_prod06_dag_03` que prod-06 difirió a un plan dedicado (ADR 0084 Opción B) — el bucle del AI reviewer (workers-1).

## Orden de ejecución recomendado

**Ola 1 — P0 en paralelo (semanas 1-6)**: `prod-02` (CI primero: es barato y des-riesga todo lo demás), `prod-01`, `prod-03`, `prod-05`. Con 3-4 personas, ~6 semanas.

**Ola 2 — P1 (semanas 4-12, solapable)**: `prod-04` (al cerrar prod-01), `prod-11` (al cerrar prod-02), y en paralelo según capacidad: `prod-06`, `prod-07`, `prod-10`, `prod-09`, `prod-08` (al cerrar prod-01), `prod-12`, `prod-13`.

**Ola 3 — P2 (a partir de semana 10)**: `prod-14`, `prod-15`, `prod-16`.

**Gate de producción**: no desplegar para usuarios reales hasta cerrar la Ola 1 completa + `prod-04` (DR drill superado) + `prod-10` (Vault operable). El resto de P1 puede cerrarse con la plataforma en piloto controlado.

## Notas de gobernanza

- Esta serie **no modifica ningún fichero existente** del roadmap: `README.md` y `EXECUTION-SEQUENCE.md` siguen desactualizados (hallazgo `docsroadmap-3`) y se regularizan en prod-15 junto con las ~20 fases en `pending_human_validation` (hallazgo `docsroadmap-2`).
- Varios planes incluyen **ADRs propuestos** para decisiones que corresponden a un humano (Loki: desplegar o retirar; cookies httpOnly vs localStorage; fail-open/fail-closed por check de guardrails; destino de las colas heavy/gpu; cifrado Fernet-en-DB vs Vault; protección de la rama master).
- El hallazgo `gap4-4` (egress-proxy como único filtro) fue **refutado** en verificación y no genera tarea; `gap4-1` (SSRF) quedó **disputado**: el agujero de diseño es real pero hoy inalcanzable (deny-all de facto) — prod-12 lo trata como deuda a cerrar antes de cablear las allowlists.
- Los 34 hallazgos medium sin pasada de verificación por lotes (por límites de sesión durante la auditoría) entran en sus planes con su evidencia `fichero:línea`; la primera tarea de cada plan afectado debe re-confirmarlos antes de implementar.
