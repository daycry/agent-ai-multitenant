/**
 * Contrato de tipos de `next.config.js` (CommonJS, no transpilable).
 *
 * Existe para que `lib/security-headers.test.ts` pueda importar la config real
 * y comprobar que las cabeceras están CABLEADAS, no sólo que la función que las
 * construye existe — el modo de fallo dominante de este repo es "mecanismo
 * entregado, cero llamantes" (docs/03-guides/verificar-antes-de-implementar.md).
 */

import type { SecurityHeader } from "./lib/security-headers";

export interface HeaderRule {
  source: string;
  headers: SecurityHeader[];
}

export interface AdminPanelNextConfig {
  reactStrictMode: boolean;
  output: string;
  poweredByHeader: boolean;
  headers: () => Promise<HeaderRule[]>;
}

declare const nextConfig: AdminPanelNextConfig;
export default nextConfig;
