# Convenciones de stack: React + Next.js

Guía práctica para frontend con Next.js 14+ (App Router) y React: server vs
client components, data fetching con TanStack Query, formularios con
react-hook-form y testing con vitest + Playwright. Referencia para agentes que
generan o revisan UI.

## Estructura del proyecto (App Router)

```
app/
  layout.tsx        # layout raíz (server component)
  page.tsx          # ruta /
  (marketing)/      # route groups para organizar sin afectar la URL
  projects/
    page.tsx        # /projects
    [id]/page.tsx   # /projects/:id
    loading.tsx     # UI de carga (Suspense)
    error.tsx       # error boundary de segmento (client component)
components/
  ui/               # primitivos (shadcn/ui)
  features/         # componentes de dominio
lib/                # helpers, clientes (api, query)
hooks/              # hooks reutilizables
```

Co-localiza `loading.tsx`, `error.tsx` y `not-found.tsx` por segmento para
estados de carga y error declarativos.

## Server Components vs Client Components

- Por defecto, los componentes del App Router son **server components**: se
  renderizan en el servidor, no envían JS al cliente, pueden hacer fetch y
  acceder a secretos del servidor.
- Marca con `"use client"` **sólo** los componentes que necesitan estado,
  efectos, eventos o APIs del navegador.
- Empuja la frontera `"use client"` lo más abajo posible en el árbol: mantén
  los wrappers como server components y aísla la interactividad en hojas.
- No pongas secretos ni claves en client components: viajan al navegador.

## Data fetching

### En server components

Haz `fetch` directamente; Next cachea y deduplica:

```tsx
export default async function ProjectPage({ params }: { params: { id: string } }) {
  const res = await fetch(`${process.env.API_URL}/projects/${params.id}`, {
    next: { revalidate: 60 },
  });
  if (!res.ok) notFound();
  const project = await res.json();
  return <ProjectView project={project} />;
}
```

- Controla el cacheado con `cache` / `next.revalidate`.
- Para mutaciones usa **Server Actions** (`"use server"`) y revalida con
  `revalidatePath` / `revalidateTag`.

### En client components con TanStack Query

Para datos interactivos (refetch, paginación infinita, optimistic updates):

```tsx
function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get("/projects").then((r) => r.data),
    staleTime: 30_000,
  });
}
```

- Claves de query estables y estructuradas (`["projects", { status }]`).
- Mutaciones con `useMutation` + invalidación de las queries afectadas.
- Configura `staleTime`/`gcTime` con criterio; no uses números mágicos
  dispersos, céntralos en la config del `QueryClient`.

## Estado

- El servidor es la fuente de verdad de los datos remotos: deja que TanStack
  Query gestione caché, no dupliques en estado global.
- Estado de UI local con `useState`/`useReducer`.
- Estado global de cliente sólo si hace falta (tema, sesión): Context para
  poco, Zustand para más; evita Redux salvo necesidad real.
- No metas datos del servidor en estado global manual: es una fuente de bugs
  de sincronización.

## Formularios con react-hook-form

- `react-hook-form` para rendimiento (re-renders mínimos) + validación con un
  schema (zod) vía resolver:

```tsx
const schema = z.object({ name: z.string().min(1).max(120) });

const form = useForm<z.infer<typeof schema>>({
  resolver: zodResolver(schema),
});
```

- Valida en cliente para UX y **siempre** revalida en servidor: el cliente no
  es confiable.
- Componentes controlados sólo cuando hace falta; deja el form no controlado
  por defecto.

## Rendimiento

- `next/image` para imágenes (optimización, lazy, tamaños).
- `next/font` para fuentes (sin layout shift, sin requests externos).
- Code splitting con `next/dynamic` para componentes pesados o sólo-cliente.
- Memoiza con criterio (`memo`, `useMemo`, `useCallback`) — sólo cuando hay un
  coste real medido, no por defecto.
- Listas grandes: virtualiza (`@tanstack/react-virtual`).

## Accesibilidad y semántica

- HTML semántico (`button`, `nav`, `main`, headings en orden).
- Todo control interactivo accesible por teclado y con `aria-*` cuando el HTML
  no basta.
- Contraste y foco visibles; usa los primitivos accesibles de shadcn/ui /
  Radix.

## TypeScript y tipos

- `strict: true`; prohibido `any`. Tipa props con interfaces/`type`.
- Deriva tipos de los datos (de los schemas zod, del cliente API) para que
  runtime y tipos no diverjan.

## Testing

### Unit / componente con vitest

```tsx
import { render, screen } from "@testing-library/react";

it("shows the project name", () => {
  render(<ProjectCard project={{ id: "1", name: "Demo" }} />);
  expect(screen.getByText("Demo")).toBeInTheDocument();
});
```

- Testing Library: prueba comportamiento visible, no detalles de
  implementación. Selecciona por rol/texto, no por clases CSS.
- Mockea la capa de red (MSW) en vez de parchear `fetch` a mano.

### E2E con Playwright

- Flujos de usuario reales contra la app levantada.
- Selectores estables (`getByRole`, `data-testid`), no XPaths frágiles.
- Aísla datos por test; no dependas del orden de ejecución.

## Tooling

- ESLint (`next/core-web-vitals`) + Prettier; cero warnings en CI.
- Pre-commit con lint + typecheck.
- Variables de entorno: sólo las prefijadas con `NEXT_PUBLIC_` llegan al
  cliente; todo lo demás se queda en el servidor.
