/**
 * El único `errorText` del panel (plan prod-16, `task_prod16_05`).
 *
 * Antes de esto había **14 copias** del helper (el plan decía 13) repartidas por
 * `users`, `backup` ×3, `llm-providers`, `model-prices`, `tenant-stats`,
 * `marketplace`, `guardrails`, `eval-quality`, `ollama`, `platform-defaults` ×2
 * y `assistant/model-cards`. Todas hacían lo mismo y todas tenían el mismo
 * defecto: `return err.body`, es decir, **pintar el cuerpo crudo del backend en
 * la UI**. En la práctica eso significa que un 502 de nginx enseñaba HTML, un
 * 500 podía enseñar un traceback de Python y un 422 enseñaba el JSON de
 * Pydantic con `loc`/`type` incluidos.
 *
 * Aquí el cuerpo se **lee** (no se muestra): si trae un mensaje legible se usa
 * ése; si no, se sustituye por un texto del diccionario i18n elegido según el
 * status. El cuerpo crudo nunca sale.
 *
 * Vive en su propio módulo y no dentro de `lib/api.ts` (que es lo que pedía la
 * letra del plan) porque necesita importar `ApiError` de allí: meterlo en
 * `api.ts` obligaría a que el wrapper de fetch dependiese del diccionario, y
 * re-exportarlo desde `api.ts` crearía un ciclo `api ⇄ api-error`. El objetivo
 * real del plan —una sola implementación— se cumple igual.
 *
 * Uso en un componente:
 *
 *     const errorText = useErrorText();
 *     …
 *     {mutation.isError && <p>{errorText(mutation.error)}</p>}
 */

import { ApiError } from "@/lib/api";
import { translate } from "@/lib/i18n/translate";
import type { Lang } from "@/lib/i18n/types";

/** Una entrada de la lista `detail` de un 422 de Pydantic. */
interface ValidationIssue {
  loc?: unknown;
  msg?: unknown;
}

/**
 * Segmentos de `loc` que Pydantic pone delante y que al usuario no le dicen
 * nada: sabe que está rellenando un formulario, no de qué parte de la petición
 * HTTP salió el campo.
 */
const LOC_PREFIXES = new Set(["body", "query", "path", "header", "cookie"]);

/** El texto si es una cadena con contenido; `null` en cualquier otro caso. */
function nonEmptyString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

/** `["body","config","0","host"]` → `"config.0.host"`; `["body"]` → `null`. */
function fieldPath(loc: unknown): string | null {
  if (!Array.isArray(loc)) return null;
  const parts = loc
    .map((part) => (typeof part === "string" || typeof part === "number" ? String(part) : null))
    .filter((part): part is string => part !== null);

  // El prefijo sólo se descarta si hay algo detrás: un `loc: ["body"]` a secas
  // no debe quedarse sin nombre de campo Y sin mensaje.
  const meaningful = parts.length > 1 && LOC_PREFIXES.has(parts[0]) ? parts.slice(1) : parts;
  return meaningful.length === 0 ? null : meaningful.join(".");
}

/** `"email: value is not a valid email address"`, o sólo el mensaje si no hay campo. */
function issueText(issue: ValidationIssue): string | null {
  const msg = nonEmptyString(issue.msg);
  if (!msg) return null;
  const field = fieldPath(issue.loc);
  return field ? `${field}: ${msg}` : msg;
}

/**
 * El mensaje legible que trae el cuerpo de una respuesta de error, o `null`.
 *
 * Reconoce las cuatro formas que produce este backend:
 *
 *   1. `{"detail": "texto"}`                     — `HTTPException(detail=str)`
 *   2. `{"detail": [{loc, msg, type}, …]}`        — validación de Pydantic (422)
 *   3. `{"detail": {"code": …, "message": …}}`    — errores con código
 *   4. `{"message": "texto"}`                     — respuestas que no usan detail
 *
 * Cualquier otra cosa (HTML de un gateway, un JSON con `traceback`, un cuerpo
 * vacío) devuelve `null` **a propósito**: es la condición que hace que el
 * llamante caiga al mensaje traducido en vez de enseñar lo que llegó.
 */
export function apiErrorDetail(body: string): string | null {
  if (nonEmptyString(body) === null) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return null; // no es JSON: no se pinta
  }

  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;

  const record = parsed as Record<string, unknown>;
  const { detail } = record;

  const plain = nonEmptyString(detail);
  if (plain) return plain;

  if (Array.isArray(detail)) {
    const issues = detail
      .filter((item): item is ValidationIssue => typeof item === "object" && item !== null)
      .map(issueText)
      .filter((text): text is string => text !== null);
    if (issues.length > 0) return issues.join("; ");
  }

  if (typeof detail === "object" && detail !== null && !Array.isArray(detail)) {
    const nested = detail as Record<string, unknown>;
    const message = nonEmptyString(nested.message) ?? nonEmptyString(nested.detail);
    if (message) return message;
  }

  return nonEmptyString(record.message);
}

/** La clave del diccionario que corresponde a un status HTTP. */
function statusKey(
  status: number,
):
  | "badRequest"
  | "unauthorized"
  | "forbidden"
  | "notFound"
  | "conflict"
  | "invalidData"
  | "tooManyRequests"
  | "server"
  | null {
  switch (status) {
    case 400:
      return "badRequest";
    case 401:
      return "unauthorized";
    case 403:
      return "forbidden";
    case 404:
      return "notFound";
    case 409:
      return "conflict";
    case 422:
      return "invalidData";
    case 429:
      return "tooManyRequests";
    default:
      return status >= 500 ? "server" : null;
  }
}

/**
 * `TypeError: Failed to fetch` (Chrome) / `NetworkError…` (Firefox) es lo que
 * lanza `fetch` cuando el api-server no responde. No es un mensaje para un
 * operador, así que se traduce.
 */
function isNetworkFailure(err: Error): boolean {
  return err instanceof TypeError && /fetch|network/i.test(err.message);
}

/**
 * Texto de error listo para pintar, en `lang`.
 *
 * Orden de preferencia:
 *
 *   1. El `detail` legible del backend (ver `apiErrorDetail`).
 *   2. Un mensaje del diccionario según el status.
 *   3. `withStatus` con el número, para códigos fuera del mapa.
 *   4. Para errores que no son de API: su `message`, salvo el fallo de red de
 *      `fetch`, que se traduce.
 *   5. `unexpected` para todo lo demás — nunca `String(err)`, que produciría
 *      `"[object Object]"`, `"null"` o `"undefined"` en pantalla.
 */
export function errorText(err: unknown, lang: Lang = "es"): string {
  if (err instanceof ApiError) {
    const detail = apiErrorDetail(err.body);
    if (detail) return detail;

    const key = statusKey(err.status);
    return key
      ? translate(lang, "errors", key)
      : translate(lang, "errors", "withStatus", { status: err.status });
  }

  if (err instanceof Error) {
    if (isNetworkFailure(err)) return translate(lang, "errors", "network");
    return nonEmptyString(err.message) ?? translate(lang, "errors", "unexpected");
  }

  return translate(lang, "errors", "unexpected");
}
