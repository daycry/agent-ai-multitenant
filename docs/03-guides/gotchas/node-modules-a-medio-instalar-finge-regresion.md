---
title: "Un `npm install` interrumpido finge una regresión de versión: 4 tests rojos que no lo estaban"
area: node, tests, workflows
encountered: 2026-07-31
stack: npm, Next.js 14.2.x, vitest, jsdom
---

## Síntoma

Tras subir una dependencia (`next` 14.2.5 → 14.2.35), la suite de vitest saca
**4 ficheros en rojo** que estaban verdes. Los fallos son creíbles y del mismo
tipo: el componente monta, la lista sale **vacía**, y el `waitFor` de un
`data-testid` de fila expira. Ningún error en consola, ninguna excepción — solo
ausencia de datos, que es lo que se esperaría de un cambio de comportamiento en
el fetch o en el mock de módulo.

La trampa es que **el diagnóstico obvio encaja**: «el transform nuevo rompió la
interoperabilidad ESM del `vi.mock`, la página usa el `apiFetch` real, falla sin
red y la lista queda vacía». Coherente, verificable en apariencia, y falso.

## Causa raíz

`node_modules` estaba **a medio instalar**. El proceso que subió la dependencia
murió durante (o justo después de) su `npm install`, dejando el árbol de
dependencias inconsistente con `package-lock.json`: unos paquetes en la versión
nueva, otros en la vieja, y algún transitivo a medias.

Y la «confirmación» que parecía cerrar el caso era un artefacto de método:

1. se aparcó el bump y se hizo `npm install` → árbol **limpio** en 14.2.5 → verde;
2. se restauró el bump **sin** reinstalar del todo → árbol **roto** en 14.2.35 → rojo.

Eso no compara versiones: compara una instalación sana con una corrupta. La
conclusión «la versión nueva rompe los tests» tenía la forma de una prueba y no
lo era.

Con `npm install` completo sobre el bump, los 4 pasan. El único rojo que quedó
era **flaky de orden** y también pasó al re-correr la suite entera.

## Fix

**Antes de creerse un rojo que aparece junto a un cambio de dependencias,
reinstala del todo y vuelve a medir:**

```bash
cd apps/admin-panel
npm install          # hasta el final: en este repo tarda ~10 min
node -e "console.log(require('./node_modules/next/package.json').version)"
npx vitest run       # la suite ENTERA, no solo el fichero rojo
```

Y si vas a comparar dos versiones para culpar a una, **las dos ramas del
experimento necesitan instalación completa**. Un `npm install` que no terminó no
es un estado, es un escombro.

Señales de que estás ante esto y no ante una regresión real:

- el fichero rojo pasa **solo** y falla en la suite (mira también
  [el flaky de revisores en paralelo](workflow-parallel-review-source-contamination.md));
- los fallos son «no aparecieron los datos» sin excepción ni error de red;
- el proceso que tocó las dependencias murió a mitad (límite de sesión, timeout,
  Ctrl-C) — en ese caso **asume el escombro** y reinstala antes de diagnosticar.

## Cómo verificar el fix

```bash
npx vitest run   # 94 ficheros / 778 tests verdes con la versión nueva instalada
```

Si sigue rojo tras una reinstalación completa **y** el fichero también falla en
solitario, entonces sí: es una regresión de verdad y toca diagnosticarla.
