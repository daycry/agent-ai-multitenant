/**
 * Thin fetch wrapper for the api-server.
 *
 * Reads NEXT_PUBLIC_API_URL at build time. Tokens come from `lib/auth`
 * — never passed in by callers, so we only have one place to swap
 * localStorage for an httpOnly cookie later.
 */

import { getToken } from "@/lib/auth";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  body: string;

  constructor(status: number, body: string) {
    super(`api ${status}: ${body}`);
    this.status = status;
    this.body = body;
  }
}

export interface ApiFetchOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const { body, headers: extraHeaders, ...rest } = options;
  const headers = new Headers(extraHeaders);
  headers.set("Accept", "application/json");

  if (body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const token = getToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...rest,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
