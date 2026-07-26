import { describe, expect, it } from "vitest";

import { lifecycleActions } from "@/app/admin/projects/[id]/plans/[planId]/plan-lifecycle-section";

describe("lifecycleActions", () => {
  it("offers «aprobar y arrancar» only from pending_approval", () => {
    // task_wf_41. El atajo une los dos clics que la misma persona da seguidos,
    // pero no puede aparecer en ningún otro estado: sería insinuar que se salta
    // la aprobación, que es exactamente lo que el backend impide con un 409.
    expect(lifecycleActions("pending_approval").canApproveAndStart).toBe(true);
    for (const status of [
      "draft",
      "pending_second_approval",
      "approved",
      "in_progress",
      "blocked",
      "completed",
      "rejected",
    ]) {
      expect(lifecycleActions(status).canApproveAndStart).toBe(false);
    }
  });

  it("keeps «aprobar plan» available for the second signer", () => {
    // En pending_second_approval solo cabe firmar: arrancar no es cosa del
    // primer firmante, y el segundo tiene que ser otra persona.
    const second = lifecycleActions("pending_second_approval");
    expect(second.canApprove).toBe(true);
    expect(second.canApproveAndStart).toBe(false);
    expect(second.canStart).toBe(false);
  });

  it("does not change the transitions that already existed", () => {
    expect(lifecycleActions("draft").canSendToApproval).toBe(true);
    expect(lifecycleActions("approved").canStart).toBe(true);
    expect(lifecycleActions("blocked").canUnblock).toBe(true);
  });

  it("offers nothing on a terminal plan", () => {
    // La barra es de acciones, no un indicador de estado: sin transición que
    // ofrecer, no se pinta.
    expect(Object.values(lifecycleActions("completed")).some(Boolean)).toBe(false);
    expect(Object.values(lifecycleActions("cancelled")).some(Boolean)).toBe(false);
  });
});
