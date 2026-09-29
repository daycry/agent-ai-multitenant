// @vitest-environment jsdom
// AUD16-10/11 (auditoría 2026-07-16): el inbox muestra el CONTENIDO persistido
// (subject/body de in_app) y un System Admin puede cambiar al scope PLATAFORMA
// (los envíos tenant_id NULL eran invisibles para cualquier humano).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

// El RoleGuard real depende del contexto de auth; aquí siempre renderiza (el
// gate efectivo es el backend — 403 para no-System-Admin).
vi.mock("@/components/ui/role-guard", () => ({
  RoleGuard: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import NotificationInboxPage from "@/app/admin/notifications/inbox/page";

function log(overrides: Record<string, unknown>) {
  return {
    id: "l1",
    channel_id: "c1",
    event_type: "infra_alert",
    channel_type: "in_app",
    status: "sent",
    target: "platform-inbox",
    attempt: 1,
    error: null,
    sent_at: "2026-07-16T14:16:00Z",
    created_at: "2026-07-16T14:16:00Z",
    subject: null,
    body: null,
    read: false,
    ...overrides,
  };
}

function inbox(items: unknown[]) {
  return { items, total: items.length, unread: items.length, limit: 25, offset: 0 };
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NotificationInboxPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("inbox de notificaciones (AUD16-10/11)", () => {
  it("muestra subject y body persistidos de una notif in_app", async () => {
    apiFetchMock.mockResolvedValue(
      inbox([
        log({
          id: "l1",
          subject: "[warning] HostSwapActive",
          body: "HostSwapActive: el host lleva 5 minutos con swap activo.",
        }),
      ]),
    );
    mount();
    await waitFor(() => expect(screen.getByTestId("inbox-subject-l1")).toBeTruthy());
    expect(screen.getByTestId("inbox-subject-l1").textContent).toContain("HostSwapActive");
    expect(screen.getByTestId("inbox-body-l1").textContent).toContain("swap activo");
  });

  it("una fila sin contenido (histórica) no rompe ni muestra huecos", async () => {
    apiFetchMock.mockResolvedValue(inbox([log({ id: "l2" })]));
    mount();
    await waitFor(() => expect(screen.getByTestId("inbox-row-l2")).toBeTruthy());
    expect(screen.queryByTestId("inbox-subject-l2")).toBeNull();
    expect(screen.queryByTestId("inbox-body-l2")).toBeNull();
  });

  it("cambiar al scope Plataforma consulta /notifications/platform/logs", async () => {
    apiFetchMock.mockResolvedValue(inbox([]));
    mount();
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalled());
    expect(String(apiFetchMock.mock.calls[0][0])).toContain("/notifications/logs?");

    fireEvent.click(screen.getByTestId("inbox-scope-platform"));
    await waitFor(() =>
      expect(
        apiFetchMock.mock.calls.some((c) =>
          String(c[0]).startsWith("/notifications/platform/logs?"),
        ),
      ).toBe(true),
    );
  });
});
