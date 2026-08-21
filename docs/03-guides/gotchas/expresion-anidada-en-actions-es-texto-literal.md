---
title: "Un `${{ }}` dentro de otro `${{ }}` es texto literal, y actionlint pasa en verde"
area: GitHub Actions / workflows
encountered: 2026-08-21
stack: GitHub Actions, docker/build-push-action, actionlint 1.7.12
docs_language: es
---

# La expresión que no se evaluó, con el linter de workflows en verde

## Síntoma

Los catorce jobs de una matriz mueren a la vez, con un error que parece de Docker
y no de sintaxis:

```
ERROR: failed to build: invalid tag
  "ghcr.io/${{ github.repository_owner }}/agent-runtime-dotnet-test:v1":
  invalid reference format
```

El `${{ … }}` viajó **tal cual** hasta el tag. Y lo que descarta las sospechas
obvias:

- `actionlint` sobre ese mismo fichero: **0 hallazgos, exit 0**.
- `check yaml` de pre-commit: **passed**.
- La misma variable, usada en OTRO sitio del mismo workflow, sí resolvió: en el
  log aparece `ghcr.io/daycry/agent-runtime-dotnet-test:<sha>` perfectamente
  formado.

Esa última línea es la que desorienta: parece que el contexto se evalúa «a
veces».

## Causa raíz

No es que el `env` de nivel workflow no se evalúe —sí lo hace—, ni que falte un
contexto. Es que **una expresión no se puede anidar dentro de otra**:

```yaml
# MAL: el interior está dentro de un ${{ }} ya abierto, así que es una CADENA
IMAGE_REF: ${{ … && format('{0}/agent-runtime-{1}', 'ghcr.io/${{ github.repository_owner }}', matrix.template) }}
```

Para el evaluador, `'ghcr.io/${{ github.repository_owner }}'` es un literal de
cadena como cualquier otro. La expresión externa **sí** se evalúa —`format`
corre, `matrix.template` resuelve—, y por eso el resultado sale a medias: todo
bien menos el trozo que se creía dinámico.

Y por eso `actionlint` no dice nada: la sintaxis es válida. Lo que está mal es el
significado, que ningún linter de workflows comprueba.

## Cómo se llegó ahí

Sustituyendo un valor a lo bruto. Un `sed 's|ghcr.io/vieja-org|ghcr.io/${{ github.repository_owner }}|g'`
acierta en el `env:` de nivel workflow —donde el destino **sí** es contexto de
expresión— y falla en la cadena entrecomillada de dentro de un `format`, que a
ojo se parece muchísimo.

## Fix

El valor entra como **argumento** de `format`, nunca interpolado en la cadena:

```yaml
# BIEN
IMAGE_REF: ${{ … && format('ghcr.io/{0}/agent-runtime-{1}', github.repository_owner, matrix.template) }}
```

Cuidado con el atajo de usar `env.REGISTRY` ahí: el contexto `env` **no está
disponible** en un `env:` de nivel job (sí en el de un step). `github` sí lo está,
en los dos.

## Por qué duele el doble

El workflow del caso real publicaba **sólo en `master`** —en rama construye y
escanea sin empujar—, así que el error no podía aparecer en ninguna rama ni en
ningún PR: **el merge fue la primera ejecución que lo vio**. Con un camino que
sólo corre en la rama por defecto, una guarda local no es una comodidad, es la
única forma de adelantarlo.

De ahí
[`tests/unit/test_workflow_expressions_are_not_nested.py`](../../../tests/unit/test_workflow_expressions_are_not_nested.py),
que recorre los workflows llevando la cuenta de `${{` sin cerrar. Detalle que
importa: **una expresión regular no sirve** para encontrarlo. La ingenua —
apertura, nada de llaves, otra apertura— devuelve cero coincidencias con el error
delante, porque entre las dos aperturas hay las llaves de los placeholders de
`format` (`{0}`, `{1}`). Hay que recorrer el texto contando profundidad.

## Ver también

- [ADR 0148 — distribución de las imágenes de runtime por digest](../../05-architecture-decisions/0148-distribucion-imagenes-runtime-por-digest.md),
  §«Corrección operativa», que cuenta el fallo de namespace que llevó a este.
