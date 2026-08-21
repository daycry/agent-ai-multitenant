// @vitest-environment jsdom

/**
 * `notifications`, migrado al diccionario (plan prod-16, `task_prod16_04`).
 *
 * Cinco superficies: las tres pestañas (canales, preferencias, plataforma), el
 * diálogo de alta/edición de canal y la bandeja (`inbox/`). Se rinden en los DOS
 * idiomas y se afirma en ambos sentidos.
 *
 * Dos cosas que sólo salen al mirar la pantalla entera:
 *
 *   * **La matriz de preferencias pintaba `label_es` siempre.** El backend sirve
 *     el catálogo de eventos bilingüe (`label_es` + `label_en`, NOTIF-3) y la UI
 *     leía sólo la cara castellana, así que con el toggle en EN la primera
 *     columna seguía diciendo «Tarea bloqueada». No es un literal cableado —es
 *     un campo del backend mal elegido—, y por eso ninguna guarda podía verlo.
 *   * **Los estados de un canal y de un envío no se traducen a propósito**:
 *     `telegram`, `queued`, `dead_letter` son el enum del backend y lo que el
 *     operador busca en los logs.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

const currentUser = {
  isSystemAdmin: true,
  isTenantAdmin: true,
  isTenantMember: true,
  isLoading: false,
};
vi.mock("@/lib/use-current-user", () => ({ useCurrentUser: () => currentUser }));

import NotificationConfigPage from "@/app/admin/notifications/page";
import NotificationInboxPage from "@/app/admin/notifications/inbox/page";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

const CHANNEL = {
  id: "ch-1",
  scope: "tenant",
  channel_type: "telegram",
  name: "Ops bot",
  enabled: true,
  config: {},
  owner_user_id: null,
  has_secret: true,
  secret_source: "encrypted",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const LOG = {
  id: "log-1",
  channel_id: "ch-1",
  event_type: "task_blocked",
  channel_type: "telegram",
  status: "dead_letter",
  target: null,
  attempt: 3,
  error: null,
  sent_at: null,
  created_at: "2026-08-01T00:00:00Z",
  subject: "Tarea bloqueada",
  body: "cuerpo",
  read: false,
};

function wireConfig(channels: unknown[] = [CHANNEL]) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/notifications/channels") return Promise.resolve(channels);
    if (path === "/notifications/platform/channel-types")
      return Promise.resolve({ enabled: ["telegram"], available: ["telegram", "slack"] });
    if (path === "/notifications/preferences") return Promise.resolve([]);
    if (path === "/notifications/event-catalog")
      return Promise.resolve([
        { event_type: "task_blocked", label_es: "Tarea bloqueada", label_en: "Task blocked" },
      ]);
    return Promise.resolve([]);
  });
}

function wireInbox(items: unknown[] = [LOG]) {
  apiFetchMock.mockImplementation(() =>
    Promise.resolve({ items, total: items.length, unread: 1, limit: 25, offset: 0 }),
  );
}

function renderIn(lang: "es" | "en", node: React.ReactElement) {
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>{node}</LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
  currentUser.isSystemAdmin = true;
});

describe("configuración de notificaciones en castellano", () => {
  it("rinde cabecera, pestañas y la tarjeta del canal", async () => {
    wireConfig();
    renderIn("es", <NotificationConfigPage />);

    expect(await screen.findByText("Notificaciones")).toBeDefined();
    expect(screen.getByTestId("tab-channels").textContent).toBe("Canales");
    expect(screen.getByTestId("tab-preferences").textContent).toBe("Preferencias");
    expect(screen.getByTestId("tab-platform").textContent).toBe("Plataforma");
    expect(screen.getByTestId("channel-create-button").textContent).toContain("Nuevo canal");
    await waitFor(() => expect(screen.getByTestId("channel-card-ch-1")).toBeTruthy());
    const card = within(screen.getByTestId("channel-card-ch-1"));
    expect(card.getByText("activo")).toBeDefined();
  });
});

describe("configuración de notificaciones en inglés", () => {
  it("traduce cabecera, pestañas y acciones", async () => {
    wireConfig();
    renderIn("en", <NotificationConfigPage />);

    expect(await screen.findByText("Notifications")).toBeDefined();
    expect(screen.getByTestId("tab-channels").textContent).toBe("Channels");
    expect(screen.getByTestId("tab-preferences").textContent).toBe("Preferences");
    expect(screen.getByTestId("tab-platform").textContent).toBe("Platform");
    expect(screen.getByTestId("channel-create-button").textContent).toContain("New channel");

    expect(screen.queryByText("Notificaciones")).toBeNull();
    expect(screen.queryByText("Canales")).toBeNull();
  });

  it("traduce la tarjeta del canal y su origen de secreto", async () => {
    wireConfig();
    renderIn("en", <NotificationConfigPage />);

    await waitFor(() => expect(screen.getByTestId("channel-card-ch-1")).toBeTruthy());
    const card = within(screen.getByTestId("channel-card-ch-1"));
    expect(card.getByText("active")).toBeDefined();
    expect(screen.getByTestId("channel-secret-ch-1").textContent).toContain("encrypted at rest");
    expect(card.getByRole("button", { name: "Edit channel" })).toBeDefined();
    expect(card.getByRole("button", { name: "Delete channel" })).toBeDefined();
    // El transporte NO se traduce: es el enum del backend.
    expect(screen.getByTestId("channel-type-ch-1").textContent).toBe("telegram");

    expect(card.queryByText("activo")).toBeNull();
  });

  it("traduce el estado vacío de canales", async () => {
    wireConfig([]);
    renderIn("en", <NotificationConfigPage />);

    const empty = await screen.findByTestId("channels-empty");
    expect(empty.textContent).toContain("No channels configured yet");
  });

  it("traduce el diálogo de alta de canal entero", async () => {
    wireConfig();
    renderIn("en", <NotificationConfigPage />);

    fireEvent.click(await screen.findByTestId("channel-create-button"));
    await waitFor(() => expect(screen.getByTestId("channel-dialog")).toBeTruthy());
    const dialog = within(screen.getByTestId("channel-dialog"));

    expect(dialog.getByText("New channel")).toBeDefined();
    expect(dialog.getByText("Scope")).toBeDefined();
    expect(dialog.getByText("Tenant (shared)")).toBeDefined();
    expect(dialog.getByText("User (only me)")).toBeDefined();
    expect(dialog.getByText("Transport")).toBeDefined();
    expect(dialog.getByText("Name")).toBeDefined();
    expect(dialog.getByText("Config (JSON, no secrets)")).toBeDefined();
    expect(dialog.getByText("Secret (optional)")).toBeDefined();
    expect(dialog.getByText(/It is encrypted at rest before being stored/)).toBeDefined();
    expect(dialog.getByText("Channel active")).toBeDefined();
    expect(screen.getByTestId("channel-form-cancel").textContent).toBe("Cancel");
    expect(screen.getByTestId("channel-form-submit").textContent).toBe("Create");
    expect(screen.getByTestId("channel-form-secret").getAttribute("placeholder")).toBe(
      "bot token / password / key",
    );

    expect(dialog.queryByText("Ámbito")).toBeNull();
  });

  it("traduce el aviso de JSON inválido, que estaba dentro del diálogo", async () => {
    wireConfig();
    renderIn("en", <NotificationConfigPage />);

    fireEvent.click(await screen.findByTestId("channel-create-button"));
    await waitFor(() => expect(screen.getByTestId("channel-form-config")).toBeTruthy());
    fireEvent.change(screen.getByTestId("channel-form-name"), { target: { value: "x" } });
    fireEvent.change(screen.getByTestId("channel-form-config"), { target: { value: "{" } });
    fireEvent.click(screen.getByTestId("channel-form-submit"));

    const err = await screen.findByTestId("channel-form-config-error");
    expect(err.textContent).toBe("The config is not valid JSON.");
  });

  it("traduce la pestaña de preferencias, incluido el catálogo bilingüe del backend", async () => {
    wireConfig();
    renderIn("en", <NotificationConfigPage />);

    fireEvent.click(await screen.findByTestId("tab-preferences"));
    await waitFor(() => expect(screen.getByTestId("preferences-tab")).toBeTruthy());

    expect(screen.getByText("Routing rules")).toBeDefined();
    expect(screen.getByText("Event")).toBeDefined();
    // `label_en` del catálogo, que antes NUNCA se usaba.
    const row = within(await screen.findByTestId("preferences-row-task_blocked"));
    expect(row.getByText("Task blocked")).toBeDefined();
    expect(row.queryByText("Tarea bloqueada")).toBeNull();
  });

  it("traduce la pestaña de plataforma", async () => {
    wireConfig();
    renderIn("en", <NotificationConfigPage />);

    fireEvent.click(await screen.findByTestId("tab-platform"));
    await waitFor(() => expect(screen.getByTestId("platform-channel-types")).toBeTruthy());

    expect(screen.getByText("Globally enabled transports")).toBeDefined();
    expect(
      screen.getByText(/A tenant can only configure channels of the transports/),
    ).toBeDefined();
    expect(screen.getByTestId("platform-save").textContent).toBe("Save");

    expect(screen.queryByText("Transportes habilitados globalmente")).toBeNull();
  });
});

describe("bandeja de notificaciones en castellano", () => {
  it("rinde cabecera, filtros y la fila", async () => {
    wireInbox();
    renderIn("es", <NotificationInboxPage />);

    expect(await screen.findByText("Bandeja de notificaciones")).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("inbox-list")).toBeTruthy());
    expect(screen.getByTestId("inbox-unread-badge").textContent).toBe("1 sin leer");
    expect(screen.getByText("Solo sin leer")).toBeDefined();
    expect(screen.getByTestId("inbox-mark-all-read").textContent).toContain(
      "Marcar todo como leído",
    );
    expect(screen.getByTestId("inbox-retry-log-1").textContent).toContain("Reintentar");
  });
});

describe("bandeja de notificaciones en inglés", () => {
  it("traduce cabecera, filtros, paginación y la fila", async () => {
    wireInbox();
    renderIn("en", <NotificationInboxPage />);

    expect(await screen.findByText("Notification inbox")).toBeDefined();
    expect(screen.getByTestId("inbox-scope-tenant").textContent).toBe("Tenant");
    expect(screen.getByTestId("inbox-scope-platform").textContent).toBe("Platform");
    await waitFor(() => expect(screen.getByTestId("inbox-list")).toBeTruthy());
    expect(screen.getByTestId("inbox-unread-badge").textContent).toBe("1 unread");
    expect(screen.getByText("Status")).toBeDefined();
    expect(screen.getByText("Unread only")).toBeDefined();
    expect(screen.getByTestId("inbox-mark-all-read").textContent).toContain("Mark all as read");
    expect(screen.getByTestId("inbox-prev-page").textContent).toBe("Previous");
    expect(screen.getByTestId("inbox-next-page").textContent).toBe("Next");
    expect(screen.getByTestId("inbox-count").textContent).toBe("1–1 of 1");

    const row = within(screen.getByTestId("inbox-row-log-1"));
    expect(row.getByText("attempt 3")).toBeDefined();
    expect(screen.getByTestId("inbox-retry-log-1").textContent).toContain("Retry");
    expect(screen.getByTestId("inbox-mark-read-log-1").textContent).toBe("Mark read");
    // El estado del envío NO se traduce: es el enum del backend.
    expect(screen.getByTestId("inbox-status-log-1").textContent).toBe("dead_letter");

    expect(screen.queryByText("Bandeja de notificaciones")).toBeNull();
    expect(screen.queryByText("Solo sin leer")).toBeNull();
    expect(screen.queryByText("Anterior")).toBeNull();
  });

  it("traduce el estado vacío de la bandeja", async () => {
    wireInbox([]);
    renderIn("en", <NotificationInboxPage />);

    const empty = await screen.findByTestId("inbox-empty");
    expect(empty.textContent).toContain("No notifications match the filter");
  });
});
