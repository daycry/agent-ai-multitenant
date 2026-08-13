---
title: "El `prettier` del hook NO es el de `npx`: formatear a mano no arregla el hook (v4-alpha vs v3)"
area: pre-commit, tooling
encountered: 2026-07-30
stack: pre-commit, mirrors-prettier v4.0.0-alpha.8, prettier 3.x vía npx
---

## Síntoma

`git commit` falla en el hook de prettier sobre un fichero Markdown. Se formatea a
mano, se comprueba que queda **estable** (dos pasadas sin cambios), se vuelve a
commitear… y el hook falla **otra vez sobre el mismo fichero**. Puede repetirse
indefinidamente:

```
prettier................................................................Failed
- hook id: prettier
- files were modified by this hook

docs\roadmap\prod-12-hardening-tools-agentes.md
```

Parece un bucle de formateadores peleándose (como
[black vs ruff-format](black-vs-ruff-format-chained-call-comment.md)), pero no lo es:
aquí es **el mismo formateador en dos versiones distintas**.

> **Actualizado el 2026-08-13.** El `rev` del hook ya no es `v4.0.0-alpha.8`: es
> `rbubley/mirrors-prettier` en **`v3.9.6`**, que fija `prettier@3.9.6` EXACTO.
> El diagnóstico de abajo sigue valiendo palabra por palabra —el árbitro es el
> hook—, pero ahora la versión que instala es **estable y conocida**, así que
> `npx prettier@3.9.6` sí coincide con él (un `npx prettier` a secas, no: sigue
> siendo «la última publicada hoy»). El porqué del cambio, con la cadena de
> dependencias que hacía flotar la versión pese al `rev` pineado, está en
> [ci-tool-version-drift.md](./ci-tool-version-drift.md).

## Causa raíz

`.pre-commit-config.yaml` usaba `mirrors-prettier` con **`rev: v4.0.0-alpha.8`**, y
pre-commit lo instala en un entorno Node **aislado y pineado**.

`apps/admin-panel/package.json` **no declara `prettier`** y no hay
`node_modules/prettier`. Así que un `npx prettier --write` descarga la **última
estable** (3.x) y la ejecuta. Prettier 3 y Prettier 4-alpha no formatean el Markdown
igual, sobre todo en tablas y saltos de línea de texto largo, así que cada uno
deshace el trabajo del otro:

- formateas con `npx` (v3) → el hook (v4-alpha) lo reescribe y falla;
- el hook lo deja en v4 → si vuelves a pasar `npx`, lo devuelve a v3.

La pista que lo delata: tu pasada manual dice **ESTABLE** y el hook sigue fallando.
Si fueran dos herramientas peleándose, tu pasada manual también sería inestable.

## Fix

**Formatea con el prettier del hook, no con `npx`:**

```bash
.venv/Scripts/python.exe -m pre_commit run prettier --files <ruta>
```

Ejecútalo dos veces: la primera modifica y «falla» (es lo normal en un hook de
auto-fix), la segunda debe pasar. Luego `git add` y commit.

Vale igual para cualquier hook de auto-fix con versión pineada. La regla general:
**el árbitro es el hook**, así que arréglalo con el hook. `pre_commit run <id> --files …`
es la vía; `pre_commit run <id> --all-files` si no sabes qué ficheros van.

Y si quieres que el `npx` local coincida, hay que declarar `prettier` en
`apps/admin-panel/package.json` con la MISMA versión que el `rev` del hook. Mientras
no esté declarado, cualquier `npx prettier` es una versión distinta y arbitraria (la
última publicada ese día).

## Cómo verificar el fix

```bash
.venv/Scripts/python.exe -m pre_commit run prettier --files <ruta>   # 2ª vez: Passed
```

Y no te fíes de `npx prettier --check`: puede decir que está bien con la v3 y el hook
seguir rechazándolo con la v4.
