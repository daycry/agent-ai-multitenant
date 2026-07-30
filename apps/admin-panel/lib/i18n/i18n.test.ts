import { describe, expect, it } from "vitest";

import { dictionary } from "./dictionary";
import { interpolate, translate } from "./translate";
import { LANGS, type Lang } from "./types";

/** Pares (namespace, clave) del diccionario, para recorrerlo entero. */
function everyEntry(): { ns: string; key: string; texts: Record<Lang, string> }[] {
  const out: { ns: string; key: string; texts: Record<Lang, string> }[] = [];
  for (const [ns, entries] of Object.entries(dictionary)) {
    for (const [key, texts] of Object.entries(entries as Record<string, Record<Lang, string>>)) {
      out.push({ ns, key, texts });
    }
  }
  return out;
}

describe("types", () => {
  it("LANGS es exactamente ES+EN (principio 12 de CLAUDE.md)", () => {
    expect(LANGS).toEqual(["es", "en"]);
  });
});

describe("dictionary — invariantes", () => {
  it("toda clave tiene texto en los dos idiomas y ninguno vacío", () => {
    const entries = everyEntry();
    // Guarda contra el envejecimiento: si el descubrimiento deja de encontrar
    // claves, este test pasaría vacío (verificar-antes-de-implementar §4).
    expect(entries.length).toBeGreaterThanOrEqual(8);

    const broken = entries.filter(({ texts }) =>
      LANGS.some((lang) => typeof texts[lang] !== "string" || texts[lang].trim() === ""),
    );
    expect(broken.map((b) => `${b.ns}.${b.key}`)).toEqual([]);
  });

  it("los marcadores {x} coinciden entre ES y EN", () => {
    const placeholders = (text: string) => (text.match(/\{[a-zA-Z0-9_]+\}/g) ?? []).sort();
    const mismatched = everyEntry().filter(
      ({ texts }) => placeholders(texts.es).join(",") !== placeholders(texts.en).join(","),
    );
    expect(mismatched.map((m) => `${m.ns}.${m.key}`)).toEqual([]);
  });

  it("ninguna traducción es un copia-pega del castellano sin traducir", () => {
    // Palabras que legítimamente se escriben igual en los dos idiomas: términos
    // que la UI castellana ya usaba en inglés (Dashboard, Runs, Settings…) y
    // nombres de producto. Esta lista sólo debe crecer con casos así; si crece
    // con verdaderas traducciones pendientes, el test deja de servir para nada.
    const identicalOnPurpose = new Set([
      "login.emailLabel",
      "nav.dashboard",
      "nav.runs",
      "nav.knowledgeBases",
      "nav.guardrails",
      "nav.marketplace",
      "nav.settings",
      "nav.ollama",
      "nav.sso",
      "nav.backup",
      "users.colEmail",
      "users.colTenant",
      // Nombres de rol del backend: se muestran tal cual a propósito.
      "users.typeSystemAdmin",
      "users.roleTenantAdmin",
      "users.roleTenantUser",
      "users.roleSystemOperator",
    ]);

    const identical = new Set(
      everyEntry()
        .filter(({ texts }) => texts.es === texts.en)
        .map(({ ns, key }) => `${ns}.${key}`),
    );

    expect([...identical].filter((id) => !identicalOnPurpose.has(id))).toEqual([]);

    // La otra dirección: una excepción que ya no aplica (porque la clave se
    // borró o porque alguien SÍ la tradujó) debe salir de la lista. Sin esto la
    // allowlist crece y nunca mengua, y acaba tapando lo que debía vigilar.
    expect([...identicalOnPurpose].filter((id) => !identical.has(id))).toEqual([]);
  });
});

describe("translate", () => {
  it("devuelve el texto del idioma pedido", () => {
    expect(translate("es", "login", "submit")).toBe("Iniciar sesión");
    expect(translate("en", "login", "submit")).toBe("Sign in");
  });

  it("cubre los tres errores del formulario de login en ambos idiomas", () => {
    for (const key of [
      "errorInvalidCredentials",
      "errorRateLimited",
      "errorUnreachable",
    ] as const) {
      expect(translate("es", "login", key)).not.toBe(translate("en", "login", key));
    }
  });

  it("interpola las variables que se le pasan", () => {
    expect(interpolate("Hola {name}, tienes {n} avisos", { name: "Ada", n: 3 })).toBe(
      "Hola Ada, tienes 3 avisos",
    );
  });

  it("deja el marcador intacto si nadie aporta la variable (mejor visible que vacío)", () => {
    expect(interpolate("Hola {name}", {})).toBe("Hola {name}");
    expect(interpolate("Hola {name}")).toBe("Hola {name}");
  });

  it("no reinterpola el valor sustituido (una variable con {otra} dentro no se expande)", () => {
    expect(interpolate("{a}", { a: "{b}", b: "boom" })).toBe("{b}");
  });
});
