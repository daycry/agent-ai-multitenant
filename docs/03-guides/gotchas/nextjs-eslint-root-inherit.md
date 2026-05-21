---
title: `.eslintrc.json` de Next.js hereda del root y pide plugins TS ausentes
area: next.js
encountered: 2026-05-20
stack: Next.js 14, ESLint 8
---

## Síntoma

```
⨯ ESLint: Failed to load plugin '@typescript-eslint' declared in
  '..\..\.eslintrc.json': Cannot find module
  '@typescript-eslint/eslint-plugin'
```

`npm run build` emite el warning aunque dentro de `apps/admin-panel/`
no necesites @typescript-eslint.

## Causa raíz

ESLint sube por el árbol de directorios buscando configs y los
**combina** (merge ascendente) hasta encontrar `root: true`. Si la
raíz del monorepo tiene un `.eslintrc.json` que extiende
`@typescript-eslint/...`, esos plugins se exigen también dentro de
`apps/admin-panel/`, donde no están instalados.

## Fix

Marca el `.eslintrc.json` del sub-proyecto como **raíz** explícita:

```json
{
  "root": true,
  "extends": ["next/core-web-vitals"],
  "rules": { ... }
}
```

Eso corta la herencia. Ahora ESLint solo usa el config local de
admin-panel (que sí tiene `eslint-config-next` instalado).

## Cómo verificar el fix

```bash
cd apps/admin-panel
npm run build
# El paso "Linting and checking validity of types" no menciona
# @typescript-eslint.
```
