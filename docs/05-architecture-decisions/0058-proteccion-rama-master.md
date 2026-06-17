---
adr_id: "0058"
title: "Protección de la rama master: mecanismo para impedir merges con CI en rojo"
status: accepted
date: 2026-06-11
decided_at: 2026-06-17
decided_by: claude-code (delegación explícita del operador)
authors: [auditoria-claude-2026-06]
plan_referenced: prod-02-ci-en-verde
docs_language: es
---

# ADR 0058 — Protección de la rama `master`

> **Estado: `accepted`** (2026-06-17, por delegación del operador). Decisión:
> **Opción A** (GitHub Pro/Team + branch protection con required status checks)
> como solución definitiva, con la **Opción C** (disciplina + `gh pr checks`,
> ya en `docs/context/conventions.md`) como medida puente activa. **La ejecución
> de A es una acción del dueño del repositorio** (subir de plan + configurar la
> protección): implica coste recurrente y permisos de administración de la cuenta
> GitHub, así que **no puede realizarla un agente** — queda como tarea humana
> documentada abajo. La Opción B (repo público) queda descartada (decisión de
> visibilidad/producto, fuera de alcance). Lo motiva el plan prod-02 (hallazgos
> `tests-1`/`tests-2`): el CI llevaba ~19 runs en rojo mergeados a `master` desde
> 2026-05-29, y el bug de triggers (`main` vs `master`) hacía que la mayoría de
> PRs no ejecutara CI.

## Contexto

La regla dura del propio `CLAUDE.md` dice «NUNCA cambiar a `completed` sin PR
mergeado» y «todo va por PR», pero **nada lo impide técnicamente hoy**:

- El repositorio está en **plan GitHub Free** y es **privado**. La API de
  branch protection responde `403 Upgrade to GitHub Pro` para repos privados
  en Free (`gh api repos/{owner}/{repo}/branches/master/protection` → 403).
- Sin branch protection no hay **required status checks**: cualquier
  colaborador con permiso de escritura puede mergear un PR con el CI en rojo o
  sin CI, exactamente lo que ocurrió entre el 2026-05-29 y el 2026-06-10.
- El plan prod-02 ya corrige los triggers (CI vuelve a ejecutarse sobre
  `master`) y pone el pipeline en verde, pero **resucitar CI no basta**: hace
  falta un mecanismo que **impida el merge** cuando los checks fallan.

## Decisión

**Ratificada (2026-06-17, delegación del operador): Opción A** como solución
definitiva + **Opción C** como puente activo. Opción B descartada.

**Acción pendiente del DUEÑO del repositorio** (no ejecutable por un agente —
requiere plan de pago + admin de la cuenta GitHub):

1. Subir el repositorio a **GitHub Pro** (o Team).
2. En `Settings → Branches → Branch protection rules`, añadir una regla para
   `master` con: _Require a pull request before merging_, _Require status checks
   to pass before merging_ (seleccionar los jobs de `ci.yml`: unit, lint,
   integration, build-images), y _Require branches to be up to date before
   merging_. Opcional: _Require linear history_.
3. Equivalente por CLI cuando el plan lo permita:
   `gh api -X PUT repos/{owner}/{repo}/branches/master/protection ...` (hoy da
   `403 Upgrade to GitHub Pro` en repo privado Free).

Hasta que el dueño complete lo anterior, rige la **Opción C** (medida puente, ya
activa en `docs/context/conventions.md` § «Regla de salida»): ningún merge a
`master` con CI en rojo, verificado con `gh pr checks <pr> --watch`.

## Opciones consideradas

### Opción A (recomendada) — GitHub Pro/Team + branch protection

Subir el plan del repositorio a **GitHub Pro** (o Team) y activar la
protección de `master` con _required status checks_ (los jobs de `ci.yml`),
_require PR before merging_ y, opcionalmente, _require linear history_.

- A favor: gate **técnico** real (GitHub bloquea el merge); coste bajo
  (Pro ~4 $/mes por usuario); **no cambia la exposición** del código (sigue
  privado); es el comportamiento que `CLAUDE.md` ya asume.
- En contra: coste recurrente; requiere acción administrativa del dueño del
  repo (no la puede hacer un agente).

### Opción B — Hacer el repositorio público

Los repos **públicos** tienen branch protection gratis en Free.

- A favor: gratis; gate técnico real.
- En contra: es una **decisión de producto sobre la visibilidad del código**
  (esta plataforma va a producción en una empresa); no la toma este plan ni un
  agente. Expone historial, issues y secretos-por-error si los hubiera.

### Opción C (medida puente, ya activa) — Disciplina + verificación

Sin protección server-side: la regla «ningún merge a `master` con CI en rojo»
se aplica por convención, verificando con `gh pr checks <pr> --watch` antes de
cada merge.

- A favor: cero coste, cero cambio de exposición, **aplicable hoy**.
- En contra: **no es un gate técnico** — depende de la disciplina humana;
  exactamente lo que falló entre 2026-05-29 y 2026-06-10. Sirve de puente, no
  de solución definitiva.

## Consecuencias

### Positivas

- Cierra el hallazgo `tests-2` con una regla explícita y verificable.
- La Opción C protege desde ya, sin esperar a la decisión de plan/visibilidad.
- Con A o B, el «merge en rojo» se vuelve **imposible**, no solo desaconsejado.

### Costes / riesgos

- Mientras siga vigente solo la Opción C, la garantía es **de proceso**, no
  técnica: un descuido puede volver a mergear en rojo.
- La Opción A introduce un coste recurrente y depende de una acción del
  administrador del repositorio.
- La Opción B es irreversible de facto (una vez público, el historial queda
  expuesto) y excede el alcance de prod-02.
