// Las preguntas que la ficha y el catálogo le hacen a `update-check`.
//
// Estos tests vivían dentro de `update-banner.test.tsx` mientras la única
// superficie era la ficha. Al llegar el aviso del catálogo (`task_mkt2_12`) se
// mudaron aquí con la lógica: son el contrato que las DOS pantallas comparten,
// y lo que impide que una diga «pide más permisos» donde la otra dice que no.
//
// El caso que más importa es `hasUpdate`. `update_available` NO significa «hay
// algo más nuevo» sino «hay un destino elegible ya» (`target_version != null`
// en el backend). Un salto de MAJOR sin opt-in llega con `update_available` en
// false y `outdated` en true: quien gatea por el primero deja la versión mayor
// invisible, que es justo lo que pasaba en la ficha.

import { describe, expect, it } from "vitest";

import {
  awaitsMajorOptIn,
  deltaWidens,
  hasUpdate,
  pendingTypes,
  proposedVersion,
  requiresConsent,
  updateCheckKey,
  updateCheckPath,
  type UpdateCheck,
} from "./update-check";

const AL_DIA: UpdateCheck = {
  installation_id: "inst-1",
  listing_id: "listing-1",
  name: "acme-checker",
  installed_version: "1.2.0",
  latest_version: "1.2.0",
  target_version: null,
  outdated: false,
  update_available: false,
  latest_is_major_bump: false,
  permission_delta: null,
  requires_consent: false,
};

const MENOR: UpdateCheck = {
  ...AL_DIA,
  latest_version: "1.3.0",
  target_version: "1.3.0",
  outdated: true,
  update_available: true,
};

/** Lo único más nuevo cruza un major y nadie pidió el opt-in: lo que el backend emite. */
const SOLO_MAJOR: UpdateCheck = {
  ...AL_DIA,
  latest_version: "2.0.0",
  target_version: null,
  outdated: true,
  update_available: false,
  latest_is_major_bump: true,
};

describe("hasUpdate", () => {
  it("una instalación al día no tiene nada que anunciar", () => {
    expect(hasUpdate(AL_DIA)).toBe(false);
    expect(hasUpdate(undefined)).toBe(false);
    expect(hasUpdate(null)).toBe(false);
  });

  it("un salto menor con destino propuesto, sí", () => {
    expect(hasUpdate(MENOR)).toBe(true);
  });

  it("un salto de MAJOR pendiente del opt-in TAMBIÉN, aunque update_available sea false", () => {
    // Ésta es la línea que separa un aviso visible de uno que no existe.
    expect(SOLO_MAJOR.update_available).toBe(false);
    expect(hasUpdate(SOLO_MAJOR)).toBe(true);
  });
});

describe("awaitsMajorOptIn / proposedVersion", () => {
  it("hay major y no hay destino ⇒ falta el opt-in", () => {
    expect(awaitsMajorOptIn(SOLO_MAJOR)).toBe(true);
    // Sin destino se habla de la versión que EXISTE, no de una a la que se
    // pueda saltar de un clic.
    expect(proposedVersion(SOLO_MAJOR)).toBe("2.0.0");
  });

  it("con destino propuesto no falta nada, y se habla de ese destino", () => {
    expect(awaitsMajorOptIn(MENOR)).toBe(false);
    expect(proposedVersion(MENOR)).toBe("1.3.0");
  });
});

describe("el delta de permisos", () => {
  const AMPLÍA = {
    added: [{ type: "filesystem_write", value: ["/tmp"] }],
    removed: [],
    changed: [{ type: "allowed_domains", from: ["api.acme.com"], to: ["*"] }],
  };

  it("quitar un permiso NO cuenta como ampliación: no hay nada que decidir", () => {
    const soloQuita = { added: [], removed: [{ type: "network" }], changed: [] };
    expect(deltaWidens(soloQuita)).toBe(false);
    expect(pendingTypes(soloQuita)).toEqual([]);
  });

  it("añadir o cambiar SÍ amplía, y ambos entran en los tipos a consentir", () => {
    expect(deltaWidens(AMPLÍA)).toBe(true);
    expect(pendingTypes(AMPLÍA)).toEqual(["allowed_domains", "filesystem_write"]);
  });

  it("sin delta no amplía nada (una instalación sin histórico previo)", () => {
    expect(deltaWidens(null)).toBe(false);
    expect(pendingTypes(undefined)).toEqual([]);
  });
});

describe("requiresConsent", () => {
  it("basta con la bandera del backend (instalación sin snapshot que comparar)", () => {
    expect(requiresConsent({ ...MENOR, requires_consent: true })).toBe(true);
  });

  it("o con un delta que ensancha, aunque la bandera no venga", () => {
    expect(
      requiresConsent({
        ...MENOR,
        requires_consent: false,
        permission_delta: {
          added: [],
          removed: [],
          changed: [{ type: "allowed_domains", from: ["api.acme.com"], to: ["*"] }],
        },
      }),
    ).toBe(true);
  });

  it("un delta que sólo quita permisos no pide consentir nada", () => {
    expect(
      requiresConsent({
        ...MENOR,
        permission_delta: { added: [], removed: [{ type: "network" }], changed: [] },
      }),
    ).toBe(false);
  });
});

describe("ruta y clave de caché", () => {
  it("la ruta lleva el opt-in de major explícito", () => {
    expect(updateCheckPath("inst-1", false)).toContain("allow_major=false");
    expect(updateCheckPath("inst-1", true)).toContain("allow_major=true");
  });

  it("la clave es la MISMA que usa la ficha, para no volver a preguntar", () => {
    // Si esto se separa, abrir la ficha desde el aviso del catálogo dispara una
    // segunda petición idéntica y el aviso puede quedar en desacuerdo con ella.
    expect(updateCheckKey("inst-1", false)).toEqual(["marketplace-update-check", "inst-1", false]);
  });
});
