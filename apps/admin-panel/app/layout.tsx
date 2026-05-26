import "./globals.css";

import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "agentic-platform · admin",
  description: "System Admin panel for the agentic platform.",
};

interface RootLayoutProps {
  children: ReactNode;
}

export default function RootLayout({ children }: RootLayoutProps) {
  // `suppressHydrationWarning` en <body> silencia el aviso de React
  // cuando una extensión del navegador (ColorZilla → cz-shortcut-listen,
  // Grammarly → data-gr-*, LastPass → data-lpignore, etc.) inyecta
  // atributos en el body después del SSR y antes de hydration. No tiene
  // que ver con nuestro código; sin esta directiva la consola del dev
  // ve el warning y le distrae buscando un mismatch que no existe.
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-background font-sans antialiased" suppressHydrationWarning>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
