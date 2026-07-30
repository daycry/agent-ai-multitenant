/**
 * Contrato de tipos de `security-headers.js`.
 *
 * El módulo es CommonJS porque lo consume `next.config.js` (ver la cabecera de
 * ese fichero); este `.d.ts` es lo que hace que `tsc --noEmit` y los tests lo
 * vean tipado sin activar `allowJs`.
 */

export interface SecurityHeader {
  key: string;
  value: string;
}

export interface SecurityHeadersOptions {
  /** `process.env.NODE_ENV` del proceso que construye. */
  nodeEnv?: string | undefined;
  /** `process.env.NEXT_PUBLIC_API_URL`: absoluto (`http://…`) o relativo (`/api`). */
  apiUrl?: string | undefined;
  /** `true` promueve la política completa de Report-Only a en vigor. */
  enforceCsp?: boolean | undefined;
}

export interface PublicApiUrlAssertion {
  nodeEnv?: string | undefined;
  apiUrl?: string | undefined;
}

export declare function buildSecurityHeaders(options?: SecurityHeadersOptions): SecurityHeader[];

/** Lanza si `nodeEnv === "production"` y `apiUrl` está ausente o vacía. */
export declare function assertPublicApiUrl(options?: PublicApiUrlAssertion): void;
