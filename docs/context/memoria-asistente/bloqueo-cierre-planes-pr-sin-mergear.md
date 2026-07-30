---
name: bloqueo-cierre-planes-pr-sin-mergear
description: "El PR #66 se mergeó el 2026-07-30 (07:32 UTC), así que el criterio 5 de cierre ya se cumple para todo lo anterior — y la lección es que un dato de estado se regenera, no se guarda"
metadata:
  node_type: memory
  type: project
  originSessionId: 5d8f55fb-8d51-43ab-8655-49099d7db010
  modified: 2026-07-30T18:44:53.733Z
---

CLAUDE.md pone como regla dura: «NUNCA cambiar `status: completed` sin la entrada de
changelog generada **y el PR mergeado**» (criterio 5 de cierre).

**RESUELTO el 2026-07-30:** el **PR #66** se mergeó a las **07:32 UTC** (merge commit
`72fe899b`), y con él los 543 commits que `plan/runs-visor-trabajo` llevaba de ventaja
sobre `master`. Los ~46 planes en `pending_human_validation` cuyo código iba en ese PR
ya no están bloqueados por este criterio.

**Por qué esta memoria sigue existiendo, aunque su bloqueo haya desaparecido:** se
escribió esa misma mañana afirmando que el PR estaba abierto y que la rama iba 543
commits por delante. Era falso desde las 07:32, y se citó varias veces durante horas
como si fuera cierto. Un dato de ESTADO caduca en horas; guardarlo en memoria lo
convierte en una afirmación que envejece mintiendo.

**Cómo aplicarlo:** no guardes estado, **regenéralo** antes de citarlo:

```bash
git rev-list --left-right --count origin/master...HEAD   # behind / ahead
gh pr view <n> --json state,mergedAt,mergeCommit
git merge-base --is-ancestor HEAD origin/master && echo "ya está en master"
```

Lo que sí es durable y merece estar aquí: **el criterio 5 existe** y hay que
comprobarlo antes de tocar un `status:`; y **mergear a `master` es decisión del
operador**, nunca un efecto colateral. Ver
[[alcance-real-planes-pendientes-2026-07-30]] y [[no-desbloquear-sin-verificacion]].
