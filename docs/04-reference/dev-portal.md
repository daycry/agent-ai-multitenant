---
title: Portal de desarrollador y documentación pública — Referencia
audience: integrador, technical-writer, frontend-dev, architect
phase: 15-instalador-produccion
docs_language: es
updated: 2026-05-31
---

# Portal de desarrollador y documentación pública — Referencia

Esta página documenta el **portal de desarrollador público** que entrega el Plan
15 (`task_15_25`): qué superficie expone, dónde vive, por qué se eligió un enfoque
estático ligero y cómo se verifica. El portal es la **cara pública** de la API v1
del Plan 13: agrega y enlaza fuentes que ya existen (el contrato OpenAPI servido
por FastAPI, los SDKs y la documentación canónica del producto) en una landing
navegable sin sesión.

Para el contrato completo de la API ver [`public-api.md`](./public-api.md); para
la guía de integración paso a paso ver
[`../03-guides/api-publica-y-webhooks.md`](../03-guides/api-publica-y-webhooks.md).

## Decisión: estático ligero, no un framework nuevo

El portal es un **route group público de Next.js** dentro de `apps/admin-panel`
(`app/developers/`), **fuera** del segmento `/admin` (que está protegido por el
auth gate). Consecuencias de diseño, todas deliberadas:

- **Público sin sesión.** Un desarrollador lee el contrato antes de tener token,
  igual que el Swagger UI público (`/api/v1/docs`).
- **Sin llamadas a la API.** Las páginas son estáticas (server components que
  renderizan texto y enlaces). No fetchean nada → no necesitan stack vivo → su
  e2e corre en un entorno solo-frontend y CI queda verde.
- **No se añade un framework de docs pesado.** Se descartó Docusaurus / Nextra /
  Mintlify: la API reference ya la sirve FastAPI como Swagger UI, los SDKs traen
  su propio `README.md` y la documentación de producto vive bajo `/docs`. El
  portal es la capa fina que **enlaza** esas fuentes, no una segunda copia.

Reutiliza los primitivos de UI del panel (`Card`, `Button`, tokens Tailwind) y
las convenciones del repo (TypeScript estricto, prettier/eslint). El contenido
de las páginas está en **español** (igual que el resto de docs y de la UI).

## Superficie que expone

| Página        | Ruta                        | Qué surfacea                                                                                                |
| ------------- | --------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Inicio        | `/developers`               | Tarjetas a las cuatro secciones + enlaces a la documentación canónica (`/docs`)                             |
| API Reference | `/developers/api-reference` | Enlaces al OpenAPI 3.1 (`/api/v1/openapi.json`) y al Swagger UI (`/api/v1/docs`); endpoints + scopes        |
| SDKs          | `/developers/sdks`          | Instalación + quickstart de los SDKs Python (`agentic-platform-sdk`) y TypeScript (`@agentic-platform/sdk`) |
| Tutoriales    | `/developers/tutorials`     | Tres pasos: acuñar un `X-API-Token`, llamar a la API v1, configurar un webhook entrante                     |
| Webhooks      | `/developers/webhooks`      | Orígenes soportados, firma HMAC-SHA256 y orden de checks fail-closed                                        |

Los snippets de código del portal reproducen los de la referencia
([`public-api.md`](./public-api.md)) y la guía
([`../03-guides/api-publica-y-webhooks.md`](../03-guides/api-publica-y-webhooks.md));
la fuente de verdad del contrato sigue siendo el OpenAPI generado en proceso por
`build_v1_openapi()`.

## Estructura de ficheros

```
apps/admin-panel/app/developers/
├── layout.tsx                 # shell público (header + nav + footer), sin auth gate
├── portal-ui.tsx              # primitivos estáticos (PageIntro, SectionCard, CodeBlock, ExternalLink)
├── page.tsx                   # landing
├── api-reference/page.tsx
├── sdks/page.tsx
├── tutorials/page.tsx
└── webhooks/page.tsx
apps/admin-panel/e2e/dev-portal.spec.ts   # e2e Playwright (auto_15_25_a)
```

## Verificación

- **Build de producción**: `npm run build` en `apps/admin-panel` compila el route
  group sin errores (TypeScript estricto, lint de Next).
- **Estilo de docs**: prettier + markdownlint sobre esta página.
- **e2e** (`auto_15_25_a`): `npx playwright test e2e/dev-portal.spec.ts`. La spec
  navega las cinco páginas y comprueba las tarjetas, los enlaces al OpenAPI /
  Swagger, los quickstarts de ambos SDKs y el orden de checks de los webhooks. No
  requiere login ni backend (el portal no llama a la API). **Escrita en este plan,
  se ejecuta con el frontend vivo** (no se corre en la verificación de esta
  tarea).

> El portal sirve al test humano `human_15_05` ("Documentación es navegable"): un
> desarrollador nuevo completa el Quick Start, encuentra la API Reference y los
> SDKs documentados, y llega a los runbooks operativos desde la landing.
