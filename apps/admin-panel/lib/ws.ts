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
import { getTenantId } from "@/lib/tenant-storage";

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

/**
 * Build a `ws(s)://` URL for an api-server WebSocket path, with the token and
 * — for a superadmin acting on behalf of a tenant — the selected `tenant_id`
 * attached as query params.
 *
 * The tenant_id is the WebSocket mirror of the `X-Tenant-Id` REST header
 * (`lib/api`): the browser WebSocket API can't set headers, so the tenant the
 * admin is acting as travels in the query string instead. Without it, an admin
 * viewing a tenant that isn't their JWT `tid` had every stream rejected under
 * RLS and the socket reconnected forever. Non-admins have it ignored server-side.
 */
export function wsUrl(path: string): string {
  const base = resolveWsBase(API_URL);
  const params = new URLSearchParams();
  const token = getToken();
  if (token) params.set("token", token);
  const tenantId = getTenantId();
  if (tenantId) params.set("tenant_id", tenantId);
  const query = params.toString();
  if (!query) return `${base}${path}`;
  const sep = path.includes("?") ? "&" : "?";
  return `${base}${path}${sep}${query}`;
}

const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 10_000;

/**
 * Capped exponential backoff (ms) for WebSocket reconnect attempts:
 * 500 → 1000 → 2000 → … capped at 10s. A negative attempt counts as the first.
 */
export function reconnectDelayMs(attempt: number): number {
  const exp = RECONNECT_BASE_MS * 2 ** Math.max(0, attempt);
  return Math.min(exp, RECONNECT_MAX_MS);
}

/**
 * Subscribe to a WebSocket for the lifetime of the component.
 *
 * `onMessage` is called with each parsed JSON frame. The latest
 * callback is always used (kept in a ref) so the socket is not torn
 * down and rebuilt when the handler identity changes — only `url` does
 * that. A null `url` means "do not connect".
 *
 * Auto-reconnects with capped exponential backoff: a dropped socket (proxy
 * idle-timeout, sleep/wake, transient network blip) recovers on its own,
 * so a long-running turn (a planning round spans minutes) keeps streaming
 * live instead of going silent until a manual reload.
 */
export function useWebSocket(url: string | null, onMessage: (data: unknown) => void): void {
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    if (!url || typeof window === "undefined") return;

    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let disposed = false; // set on cleanup so a pending reconnect never fires

    const connect = () => {
      socket = new WebSocket(url);
      socket.onopen = () => {
        attempt = 0; // a successful connection resets the backoff
      };
      socket.onmessage = (event) => {
        try {
          handlerRef.current(JSON.parse(event.data as string));
        } catch {
          // A non-JSON frame is not something the UI can use — drop it.
        }
      };
      socket.onclose = () => {
        if (disposed) return; // unmounted / url changed → don't resurrect
        reconnectTimer = setTimeout(connect, reconnectDelayMs(attempt++));
      };
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [url]);
}
