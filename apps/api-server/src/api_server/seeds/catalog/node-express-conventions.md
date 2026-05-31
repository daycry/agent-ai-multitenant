# Convenciones de stack: Node.js + Express

Guía práctica para APIs HTTP con Node.js (TypeScript) y Express, validación con
zod y tests con vitest. Referencia para agentes que generan o revisan backend
Node.

## Layout del repositorio

```
src/
  routes/       # routers Express por recurso
  controllers/  # handlers finos: parsean request, llaman al service, responden
  services/     # casos de uso, lógica de negocio
  repositories/ # acceso a datos (pg / Prisma)
  middleware/   # auth, error handler, request-id, rate limit
  schemas/      # esquemas zod de validación
  config.ts     # carga y valida env una sola vez
  app.ts        # construcción de la app Express (sin listen)
  server.ts     # arranque (listen) — separado para testear app
tests/
```

Separa `app.ts` (construye la app) de `server.ts` (la arranca). Así los tests
importan la app sin abrir un puerto.

## TypeScript estricto

- `tsconfig.json` con `"strict": true`. Prohibido `any`; usa `unknown` y
  estrecha el tipo.
- Tipa los handlers: `(req: Request, res: Response, next: NextFunction)`.
- Evita aserciones `as` salvo en fronteras justificadas.

## Configuración y entorno

Valida el entorno al arrancar con zod; si falta algo, falla rápido:

```ts
import { z } from "zod";

const EnvSchema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]),
  PORT: z.coerce.number().default(3000),
  DATABASE_URL: z.string().url(),
});

export const env = EnvSchema.parse(process.env);
```

No leas `process.env` disperso por el código: una sola fuente tipada.

## Estructura de un router

```ts
import { Router } from "express";

export const projectRouter = Router();

projectRouter.get("/:id", async (req, res, next) => {
  try {
    const project = await projectService.get(req.params.id);
    if (!project) return res.status(404).json({ error: "not found" });
    res.json(project);
  } catch (err) {
    next(err);
  }
});
```

Los controllers no contienen lógica de negocio: parsean, delegan en el service
y responden. Toda excepción va a `next(err)` para el error handler central.

## Middleware

Orden recomendado de la cadena:

1. `helmet()` — cabeceras de seguridad.
2. `express.json({ limit })` — parseo de body con límite.
3. request-id / logging (pino).
4. CORS configurado explícitamente (no `*` en producción).
5. rate limiting.
6. routers.
7. handler 404.
8. **error handler central** (último, con firma de 4 argumentos).

```ts
app.use((err, req, res, next) => {
  req.log.error({ err }, "unhandled");
  const status = err.statusCode ?? 500;
  res.status(status).json({ error: err.publicMessage ?? "internal error" });
});
```

Nunca devuelvas el stack al cliente; loguéalo con un id de correlación.

## Validación con zod

Valida todo input no confiable (body, query, params) en el borde:

```ts
const CreateProject = z.object({
  name: z.string().min(1).max(120),
  description: z.string().optional(),
});

const parsed = CreateProject.safeParse(req.body);
if (!parsed.success) {
  return res.status(422).json({ errors: parsed.error.flatten() });
}
```

Deriva los tipos de los esquemas con `z.infer<typeof CreateProject>` para que
runtime y tipos no diverjan.

## Acceso a Postgres

### Con `pg`

- Usa un único `Pool` compartido; nunca un cliente por request sin liberarlo.
- **Consultas parametrizadas siempre** (`$1, $2`); jamás concatenes SQL.
- Libera clientes en `finally`.

```ts
const { rows } = await pool.query(
  "SELECT id, name FROM projects WHERE tenant_id = $1 AND id = $2",
  [tenantId, id],
);
```

### Con Prisma

- Una instancia singleton de `PrismaClient`.
- Usa transacciones (`prisma.$transaction`) para operaciones multi-tabla.
- Modela `tenantId` en cada tabla multi-tenant y filtra siempre por él.

### Multi-tenancy

Toda query incluye el `tenant_id`. No confíes en que el cliente lo mande:
derívalo del token autenticado y propágalo desde el middleware.

## Async y errores

- Usa `async/await`; envuelve handlers para que las promesas rechazadas vayan a
  `next` (o usa `express-async-errors`).
- No mezcles callbacks y promesas.
- Maneja `unhandledRejection` y `uncaughtException` a nivel de proceso para
  cerrar de forma ordenada.

## Testing con vitest

- `supertest` contra la `app` (sin `listen`) para tests de endpoint:

```ts
import request from "supertest";
import { app } from "../src/app";

it("returns 404 for unknown project", async () => {
  const res = await request(app).get("/projects/unknown");
  expect(res.status).toBe(404);
});
```

- Unit tests de services con repositorios fake.
- Integración con una DB efímera (Docker / testcontainers).
- `vitest` con cobertura (`--coverage`); apunta a >70% en lógica crítica.

## Tooling

- ESLint + Prettier; cero warnings en CI.
- Pre-commit que ejecuta lint + typecheck + tests rápidos.
- Lockfile commiteado (`package-lock.json` / `pnpm-lock.yaml`).
