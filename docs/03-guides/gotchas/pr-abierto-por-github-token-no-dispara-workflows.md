---
title: "Un PR abierto por el `GITHUB_TOKEN` no dispara NINGÚN workflow, y se mergea sin un solo check"
area: GitHub Actions / CI
encountered: 2026-08-22
stack: GitHub Actions, gh CLI dentro de un workflow, ci.yml sin filtros de rutas
docs_language: es
---

# El PR que llega sin CI, y no porque falte configurarla

## Síntoma

Un workflow abre un pull request por su cuenta (`gh pr create` con
`GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`) y el PR aparece **sin checks**:

```
$ gh pr checks 87
no checks reported on the 'chore/runtime-digests-b2ad4bce52c8' branch
```

Lo desconcertante es que la configuración parece correcta. `ci.yml` declara:

```yaml
on:
  pull_request:
    branches: [master]
```

Sin `paths:`, sin `types:`, sin nada que excluya esa rama. Un PR abierto a mano
sobre la misma rama sí habría disparado la suite entera.

## Causa raíz

GitHub **no dispara workflows** para eventos creados con el `GITHUB_TOKEN`. Es
una protección deliberada contra la recursión: si un workflow pudiera disparar
workflows, un job que abre un PR arrancaría otro job que abre otro PR.

No es un fallo ni algo que se arregle en el YAML del workflow que recibe el
evento: el evento sencillamente no se emite.

## Por qué importa más de lo que parece

El caso real: el PR llevaba `runtime_images.json`, el manifiesto que decide **qué
imagen exacta ejecuta el código no confiable de cada tenant** (ADR 0148). Es
decir que el fichero más sensible del aislamiento del Principio Rector 2 llega a
`master` sin que ningún test lo mire, mientras que un cambio de una coma en un
README pasa por doce jobs.

Y no salta a la vista: el PR se ve normal, mergeable, verde por ausencia. «Sin
checks» y «todos los checks en verde» se parecen mucho en la interfaz cuando uno
va con prisa.

## Qué hacer

Tres salidas, y la elección tiene consecuencias:

1. **Verificar fuera de CI lo que CI no va a mirar.** Es lo que se hizo el
   2026-08-22 con este manifiesto: contrastar los catorce digests contra lo que
   sirve de verdad el registry, pidiendo un token anónimo a `ghcr.io` y comparando
   la cabecera `docker-content-digest` de cada `:v1`. Salió 14/14. Es **más
   fuerte** que lo que habría dicho la CI, porque compara contra la realidad
   externa y no contra el propio fichero.
2. **Abrir el PR con otra identidad** (un PAT o un GitHub App token) para que los
   eventos sí se emitan. Cuesta un secreto de larga vida en el repositorio, que es
   justo lo que este proyecto rechazó al elegir el namespace de GHCR: un secreto
   permanente de alcance amplio a cambio de una comodidad.
3. **Cerrar y reabrir el PR a mano**, que emite el evento con identidad humana.
   Funciona, pero es un paso manual que alguien olvidará.

Lo que **no** vale es asumir que un PR sin checks es un PR aprobado.

## Ver también

- [ADR 0148 — distribución de las imágenes de runtime por digest](../../05-architecture-decisions/0148-distribucion-imagenes-runtime-por-digest.md)
- [`expresion-anidada-en-actions-es-texto-literal.md`](./expresion-anidada-en-actions-es-texto-literal.md) — la otra trampa de Actions del mismo día, también invisible para el linter.
