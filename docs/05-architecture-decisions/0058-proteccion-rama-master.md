---
adr_id: "0058"
title: "Protección de la rama master: mecanismo para impedir merges con CI en rojo"
status: proposed
date: 2026-06-11
authors: [auditoria-claude-2026-06]
plan_referenced: prod-02-ci-en-verde
docs_language: es
---

# ADR 0058 — Protección de la rama `master`

> **Estado: `proposed`** — requiere decisión del operador. Lo motiva el plan
> prod-02 (hallazgos `tests-1`/`tests-2` de la auditoría de producción
> 2026-06): el CI llevaba ~19 runs consecutivos en rojo mergeados a `master`
> desde 2026-05-29, y el bug de triggers (`main` vs `master`) hacía que la
> mayoría de PRs no ejecutara CI en absoluto.

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

Pendiente de elección humana entre las tres opciones de abajo. La
**recomendación de la auditoría es la Opción A** (GitHub Pro/Team + required
status checks), con la **Opción C activa desde el día 1** como medida puente
mientras se decide (ya documentada en `docs/context/conventions.md`,
sección «Regla de salida»).

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
