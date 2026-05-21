# admin-panel

Next.js 14 App Router · TypeScript strict · Tailwind 3 · shadcn/ui tokens.

The System Admin panel for the agentic platform. Phase 0 ships only
the scaffold + a placeholder home page. Login and the system-health
dashboard arrive in `task_00_13`.

## Layout

```
apps/admin-panel/
├── package.json
├── next.config.js
├── tailwind.config.ts
├── postcss.config.js
├── tsconfig.json
├── components.json         # shadcn/ui config
├── app/
│   ├── layout.tsx          # html shell + Tailwind base
│   ├── page.tsx            # placeholder home
│   └── globals.css         # @tailwind directives + design tokens
├── components/             # reserved for shadcn/ui components (task_00_13)
├── lib/
│   └── utils.ts            # cn() helper (clsx + tailwind-merge)
└── types/                  # generated API types (`npm run generate:api-types`)
```

## Local development

From `apps/admin-panel/`:

```bash
npm install
npm run dev          # http://localhost:3000
npm run build        # production build (used by CI test auto_00_12_a)
npm run typecheck    # tsc --noEmit
npm run lint         # eslint
```

## Generating API types

The Python api-server publishes an OpenAPI schema at
`/openapi.json` (default port: **8001**). With the stack up:

```bash
npm run generate:api-types
```

writes the TypeScript bindings to `types/api.ts`. Wire those into
TanStack Query in `task_00_13`.
