"use client";

/**
 * WebSocket helper for the real-time endpoints (Plan 02 Fase E).
 *
 * The browser WebSocket API cannot set an Authorization header, so the
 * JWT travels as a `?token=` query parameter — same token `lib/api`
 * uses for REST calls.
 */

import { useEffect, useRef } from "react";

import { getToken } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

type WsLoc = { protocol: string; host: string };

/**
 * Resolve the `ws(s)://` origin for the configured API base.
 *
 * - Absolute base (`http(s)://host`): just swap the scheme.
 * - Relative base (`/api`, single-origin behind the Caddy reverse proxy —
 *   prod-01/prod-09): the browser WebSocket API rejects relative URLs, so we
 *   build an ABSOLUTE origin from the current page location (https→wss).
 *
 * `loc` is injected for testing; in the browser it defaults to
 * `window.location`.
 */
export function resolveWsBase(apiUrl: string, loc?: WsLoc): string {
  if (/^https?:\/\//i.test(apiUrl)) return apiUrl.replace(/^http/i, "ws");
  const l = loc ?? (typeof window !== "undefined" ? window.location : undefined);
  if (l) {
    const scheme = l.protocol === "https:" ? "wss:" : "ws:";
    const rel = apiUrl.startsWith("/") ? apiUrl : `/${apiUrl}`;
    return `${scheme}//${l.host}${rel}`;
  }
  return apiUrl.replace(/^http/i, "ws");
}

/** Build a `ws(s)://` URL for an api-server WebSocket path, token attached. */
export function wsUrl(path: string): string {
  const base = resolveWsBase(API_URL);
  const token = getToken();
  if (!token) return `${base}${path}`;
  const sep = path.includes("?") ? "&" : "?";
  return `${base}${path}${sep}token=${encodeURIComponent(token)}`;
}

/**
 * Subscribe to a WebSocket for the lifetime of the component.
 *
 * `onMessage` is called with each parsed JSON frame. The latest
 * callback is always used (kept in a ref) so the socket is not torn
 * down and rebuilt when the handler identity changes — only `url` does
 * that. A null `url` means "do not connect".
 */
export function useWebSocket(url: string | null, onMessage: (data: unknown) => void): void {
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    if (!url || typeof window === "undefined") return;

    const socket = new WebSocket(url);
    socket.onmessage = (event) => {
      try {
        handlerRef.current(JSON.parse(event.data as string));
      } catch {
        // A non-JSON frame is not something the UI can use — drop it.
      }
    };

    return () => socket.close();
  }, [url]);
}
