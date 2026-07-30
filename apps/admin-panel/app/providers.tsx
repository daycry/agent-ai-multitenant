"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { LanguageProvider } from "@/lib/lang-context";

interface ProvidersProps {
  children: ReactNode;
}

export function Providers({ children }: ProvidersProps) {
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
