/**
 * Plan 06.18, punto 4 del checklist humano: «el selector de runtime muestra
 * nombres legibles, nunca el slug».
 *
 * El catálogo lo sirve el backend (`GET /runtime-templates`) con una etiqueta
 * bilingüe por plantilla; `runtimeLabel` es el único sitio del panel que decide
 * QUÉ texto ve el operador. Antes de 06.18 cada pantalla inventaba sus propias
 * etiquetas (14 ids en Comandos vs 12 en Dep-cache) y, cuando no acertaba,
 * pintaba el slug crudo (`php-phpunit`) — que es un identificador, no un nombre.
 *
 * Aquí se clava el contrato de esa función: la etiqueta del idioma activo, y
 * NUNCA el id. El caso EN es el que se rompía en la práctica (un `label.es`
 * devuelto con `lang="en"` pasa desapercibido en una demo en español).
 */

import { describe, expect, it } from "vitest";

import { runtimeLabel, type RuntimeTemplateDto } from "./runtime-templates";

function template(overrides: Partial<RuntimeTemplateDto> = {}): RuntimeTemplateDto {
  return {
    id: "php-phpunit",
    label: { es: "PHP · PHPUnit", en: "PHP · PHPUnit (tests)" },
    dep_cache_mount: "/composer",
    network_policy: "restricted",
    ...overrides,
  };
}

describe("runtimeLabel", () => {
  it("resuelve el id a la etiqueta ES cuando el idioma activo es español", () => {
    expect(runtimeLabel(template(), "es")).toBe("PHP · PHPUnit");
  });

  it("resuelve el id a la etiqueta EN cuando el idioma activo es inglés", () => {
    // El bug silencioso: devolver el ES con lang="en" no se nota en una demo
    // en español. Las dos etiquetas del fixture son DISTINTAS a propósito.
    expect(runtimeLabel(template(), "en")).toBe("PHP · PHPUnit (tests)");
  });

  it("nunca devuelve el slug del catálogo (es un identificador, no un nombre)", () => {
    const catalog: RuntimeTemplateDto[] = [
      template(),
      template({
        id: "node-jest",
        label: { es: "Node · Jest", en: "Node · Jest" },
        dep_cache_mount: "/node_modules",
      }),
      template({
        id: "python-pytest",
        label: { es: "Python · pytest", en: "Python · pytest" },
        dep_cache_mount: "/pip",
      }),
    ];
    // La guarda encontró algo (si el catálogo del fixture se vaciara, este
    // bloque pasaría en vacío y dejaría de proteger nada).
    expect(catalog.length).toBeGreaterThanOrEqual(3);
    for (const rt of catalog) {
      for (const lang of ["es", "en"] as const) {
        expect(runtimeLabel(rt, lang)).not.toBe(rt.id);
      }
    }
  });

  it("distingue dos plantillas del mismo stack por su etiqueta, no por el id", () => {
    // php-phpunit y php-pest comparten prefijo: si la UI cayera al slug, el
    // operador tendría que leer identificadores para elegir.
    const phpunit = template();
    const pest = template({ id: "php-pest", label: { es: "PHP · Pest", en: "PHP · Pest" } });
    expect(runtimeLabel(phpunit, "es")).not.toBe(runtimeLabel(pest, "es"));
  });
});
