import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { apiErrorDetail, errorText } from "./api-error";
import { LANGS } from "./i18n/types";

describe("apiErrorDetail — lectura del cuerpo del backend", () => {
  it("saca el detail string de un HTTPException de FastAPI", () => {
    expect(apiErrorDetail('{"detail":"Tenant slug already exists"}')).toBe(
      "Tenant slug already exists",
    );
  });

  it("saca campo + mensaje de un 422 de validación de Pydantic", () => {
    const body = JSON.stringify({
      detail: [
        {
          loc: ["body", "email"],
          msg: "value is not a valid email address",
          type: "value_error.email",
        },
      ],
    });
    expect(apiErrorDetail(body)).toBe("email: value is not a valid email address");
  });

  it("une varios errores de validación en una sola frase", () => {
    const body = JSON.stringify({
      detail: [
        { loc: ["body", "email"], msg: "field required", type: "missing" },
        { loc: ["body", "role"], msg: "unexpected value", type: "enum" },
      ],
    });
    expect(apiErrorDetail(body)).toBe("email: field required; role: unexpected value");
  });

  it("descarta el prefijo body/query/path del loc, que no dice nada al usuario", () => {
    const body = JSON.stringify({
      detail: [{ loc: ["query", "limit"], msg: "input should be less than 100" }],
    });
    expect(apiErrorDetail(body)).toBe("limit: input should be less than 100");
  });

  it("usa el loc entero cuando está anidado", () => {
    const body = JSON.stringify({
      detail: [{ loc: ["body", "config", "0", "host"], msg: "field required" }],
    });
    expect(apiErrorDetail(body)).toBe("config.0.host: field required");
  });

  it("saca el message de un detail objeto (errores con código)", () => {
    const body = JSON.stringify({ detail: { code: "quota_exceeded", message: "Sin presupuesto" } });
    expect(apiErrorDetail(body)).toBe("Sin presupuesto");
  });

  it("acepta {message} en la raíz (respuestas que no usan detail)", () => {
    expect(apiErrorDetail('{"message":"Destino no alcanzable"}')).toBe("Destino no alcanzable");
  });

  it("devuelve null cuando el cuerpo no es JSON — un HTML de gateway no se pinta", () => {
    expect(apiErrorDetail("<html><body>502 Bad Gateway</body></html>")).toBeNull();
  });

  it("devuelve null con cuerpo vacío", () => {
    expect(apiErrorDetail("")).toBeNull();
    expect(apiErrorDetail("   ")).toBeNull();
  });

  it("devuelve null cuando el JSON no lleva ningún campo legible", () => {
    expect(apiErrorDetail('{"traceback":"File \\"x.py\\", line 1"}')).toBeNull();
    expect(apiErrorDetail("[1,2,3]")).toBeNull();
  });

  it("devuelve null si el detail es una cadena vacía o sólo espacios", () => {
    expect(apiErrorDetail('{"detail":"  "}')).toBeNull();
  });
});

describe("errorText — nunca pinta el cuerpo crudo", () => {
  it("prefiere el detail legible del backend", () => {
    const err = new ApiError(409, '{"detail":"Tenant slug already exists"}');
    expect(errorText(err, "es")).toBe("Tenant slug already exists");
  });

  it("cae a un mensaje traducido por status cuando el cuerpo es ilegible", () => {
    const html = "<html><head><title>502</title></head><body>nginx</body></html>";
    const es = errorText(new ApiError(502, html), "es");
    const en = errorText(new ApiError(502, html), "en");

    expect(es).not.toContain("nginx");
    expect(es).not.toContain("<html>");
    expect(en).not.toContain("nginx");
    expect(es).not.toBe(en);
  });

  it("no filtra el traceback de un 500 a la pantalla", () => {
    const body = '{"traceback":"Traceback (most recent call last): ZeroDivisionError"}';
    const text = errorText(new ApiError(500, body), "es");
    expect(text).not.toContain("Traceback");
    expect(text).not.toContain("ZeroDivisionError");
    expect(text.length).toBeGreaterThan(0);
  });

  it("distingue los status más frecuentes con mensajes distintos", () => {
    const statuses = [400, 401, 403, 404, 409, 422, 429, 500];
    const texts = statuses.map((status) => errorText(new ApiError(status, ""), "es"));
    expect(new Set(texts).size).toBe(statuses.length);
  });

  it("da un mensaje para cualquier status, incluso uno raro, con el número dentro", () => {
    expect(errorText(new ApiError(418, ""), "es")).toContain("418");
  });

  it("traduce todos los mensajes por status a los dos idiomas", () => {
    for (const status of [400, 401, 403, 404, 409, 422, 429, 500, 418]) {
      const es = errorText(new ApiError(status, ""), "es");
      const en = errorText(new ApiError(status, ""), "en");
      expect(es, `status ${status}`).not.toBe(en);
    }
  });

  it("pasa el message de un Error normal (no es cuerpo del backend)", () => {
    expect(errorText(new Error("El fichero excede 10 MB"), "es")).toBe("El fichero excede 10 MB");
  });

  it("traduce el fallo de red de fetch en vez de mostrar 'Failed to fetch'", () => {
    const es = errorText(new TypeError("Failed to fetch"), "es");
    const en = errorText(new TypeError("Failed to fetch"), "en");
    expect(es).not.toContain("Failed to fetch");
    expect(en).not.toContain("Failed to fetch");
    expect(es).not.toBe(en);
  });

  it("no pinta '[object Object]' cuando lo lanzado no es un Error", () => {
    for (const thrown of [{ oops: true }, null, undefined, 42]) {
      const text = errorText(thrown, "es");
      expect(text).not.toContain("[object Object]");
      expect(text).not.toBe("null");
      expect(text).not.toBe("undefined");
      expect(text.trim().length).toBeGreaterThan(0);
    }
  });

  it("un Error sin mensaje no deja la pantalla en blanco", () => {
    expect(errorText(new Error(""), "es").trim().length).toBeGreaterThan(0);
  });

  it("el idioma por defecto es ES (el default del panel)", () => {
    expect(errorText(new ApiError(404, ""))).toBe(errorText(new ApiError(404, ""), "es"));
  });

  it("responde en los dos idiomas del catálogo cerrado, sin excepciones", () => {
    for (const lang of LANGS) {
      expect(errorText(new ApiError(403, ""), lang).trim().length).toBeGreaterThan(0);
    }
  });
});
