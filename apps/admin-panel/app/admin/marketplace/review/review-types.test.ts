// La aritmética pura de la cola de revisión (ADR 0142 D6, `task_mkt2_10`).
//
// Espeja `tests/unit/test_marketplace_permission_diff.py`: los dos casos que
// muerden son el MISMO en las dos implementaciones, y están aquí a propósito
// para que si una se mueve, se note.

import { describe, expect, it } from "vitest";

import {
  availableActions,
  deltaNeedsAttention,
  isEmptyDelta,
  permissionDelta,
  previousVersion,
  type ListingVersion,
} from "./review-types";

const perm = (type: string, value?: unknown) => ({ type, value });

describe("permissionDelta", () => {
  it("no ve cambios donde no los hay", () => {
    const perms = [perm("allowed_domains", ["a.com"])];
    const delta = permissionDelta(perms, perms);
    expect(isEmptyDelta(delta)).toBe(true);
    expect(deltaNeedsAttention(delta)).toBe(false);
  });

  it("reporta un permiso nuevo y pide atención", () => {
    const delta = permissionDelta(
      [perm("allowed_domains", ["a.com"])],
      [perm("allowed_domains", ["a.com"]), perm("allowed_paths", ["/tmp"])],
    );
    expect(delta.added.map((p) => p.type)).toEqual(["allowed_paths"]);
    expect(delta.removed).toEqual([]);
    expect(deltaNeedsAttention(delta)).toBe(true);
  });

  it("un permiso retirado se enseña pero NO exige atención", () => {
    const delta = permissionDelta(
      [perm("allowed_domains", ["a.com"]), perm("network_policy", "open")],
      [perm("allowed_domains", ["a.com"])],
    );
    expect(delta.removed.map((p) => p.type)).toEqual(["network_policy"]);
    expect(deltaNeedsAttention(delta)).toBe(false);
  });

  // El caso que muerde: mismo tipo, alcance mucho mayor.
  it("un ensanche del mismo tipo cuenta como cambio", () => {
    const delta = permissionDelta(
      [perm("allowed_domains", ["api.acme.com"])],
      [perm("allowed_domains", ["*"])],
    );
    expect(delta.added).toEqual([]);
    expect(delta.changed).toEqual([{ type: "allowed_domains", from: ["api.acme.com"], to: ["*"] }]);
    expect(deltaNeedsAttention(delta)).toBe(true);
  });

  // Y el reverso: sin esto, cada re-serialización del manifest daría un falso
  // aviso, y los avisos falsos se acaban ignorando.
  it("reordenar una lista no es un cambio", () => {
    const delta = permissionDelta(
      [perm("allowed_domains", ["a.com", "b.com"])],
      [perm("allowed_domains", ["b.com", "a.com"])],
    );
    expect(isEmptyDelta(delta)).toBe(true);
  });

  it("sin versión anterior todo es nuevo: nadie había consentido nada", () => {
    const delta = permissionDelta(undefined, [perm("allowed_paths", ["/etc"])]);
    expect(delta.added.map((p) => p.type)).toEqual(["allowed_paths"]);
  });

  it("un descriptor sin `type` no se cuela como permiso", () => {
    const delta = permissionDelta([], [{ tipo: "allowed_paths" } as unknown]);
    expect(delta.added).toEqual([]);
  });

  it("dos descriptores del mismo tipo colapsan al último", () => {
    const delta = permissionDelta(
      [],
      [perm("allowed_domains", ["a.com"]), perm("allowed_domains", ["b.com"])],
    );
    expect(delta.added).toHaveLength(1);
    expect(delta.added[0].value).toEqual(["b.com"]);
  });
});

describe("previousVersion", () => {
  const version = (v: string): ListingVersion => ({
    id: v,
    listing_id: "l",
    version: v,
    changelog: null,
    config_schema: null,
    requested_permissions: [],
    reviewed_by: null,
    reviewed_at: null,
    created_at: "2026-08-01T00:00:00Z",
  });

  it("es la primera fila distinta de la actual", () => {
    expect(previousVersion([version("2.0.0"), version("1.0.0")], "2.0.0")?.version).toBe("1.0.0");
  });

  it("no hay anterior cuando el histórico solo tiene la actual", () => {
    expect(previousVersion([version("1.0.0")], "1.0.0")).toBeUndefined();
  });

  it("no hay anterior con el histórico vacío", () => {
    expect(previousVersion([], "1.0.0")).toBeUndefined();
  });
});

describe("availableActions", () => {
  // Espeja REVIEW_TRANSITIONS. Un botón que el backend rechaza con 409 es la
  // peor UI posible: la que ofrece algo y luego dice que no.
  it("solo lo pendiente se aprueba o rechaza", () => {
    expect(availableActions("pending_review")).toEqual({
      canApprove: true,
      canReject: true,
      canPromote: false,
    });
  });

  it("solo lo publicado se promociona", () => {
    expect(availableActions("published")).toEqual({
      canApprove: false,
      canReject: false,
      canPromote: true,
    });
  });

  it("un borrador y un rechazado no ofrecen nada", () => {
    for (const status of ["draft", "rejected"]) {
      expect(availableActions(status)).toEqual({
        canApprove: false,
        canReject: false,
        canPromote: false,
      });
    }
  });
});
