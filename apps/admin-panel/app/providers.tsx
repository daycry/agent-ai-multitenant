"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { setUnauthorizedHandler } from "@/lib/api";
import { LanguageProvider } from "@/lib/lang-context";
import { setSessionQueryClient } from "@/lib/session-cache";

interface ProvidersProps {
  children: ReactNode;
}

export function Providers({ children }: ProvidersProps) {
  const router = useRouter();
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      }),
  );

  // Global 401 handling (task_prod09_10, frontend-3). `apiFetch` has already
  // dropped the session + tenant by the time this runs; what is left is the
  // part that needs a router and a query client, which a module under `lib/`
  // cannot have: wipe the cache so the next user never sees the previous
  // user's data, and send the browser to /login remembering where it was.
  //
  // Registered in an effect (not during render) because it mutates
  // module-level state, and torn down on unmount so a stale closure over a
  // dead router can never fire.
  useEffect(() => {
    setSessionQueryClient(client);
    setUnauthorizedHandler((next) => {
      client.clear();
      const target = next && next !== "/" ? `/login?next=${encodeURIComponent(next)}` : "/login";
      router.replace(target);
    });
    return () => {
      setUnauthorizedHandler(null);
      setSessionQueryClient(null);
    };
  }, [client, router]);

  // `LanguageProvider` va aquí, en el layout RAÍZ, para que las pantallas de
  // sesión (`/login`, `/select-tenant`, `/no-access`) también tengan idioma;
  // hasta prod-16 sólo lo montaba `app/admin/layout.tsx` y por eso el login
  // estaba en una mezcla de ES y EN. NO volver a montarlo dentro de `/admin`:
  // un provider anidado tapa a este.
  return (
    <QueryClientProvider client={client}>
      <LanguageProvider>{children}</LanguageProvider>
    </QueryClientProvider>
  );
}
