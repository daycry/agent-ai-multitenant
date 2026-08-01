// @vitest-environment jsdom

/**
 * Caracterización de `notifications` ANTES de partirla (prod-16 `task_prod16_08`).
 *
 * La pantalla tenía 831 líneas con las tres pestañas, el diálogo de canal y la
 * matriz de preferencias en el mismo fichero, y **ningún test**. Partir sin red
 * es limpieza con los dedos cruzados: si una pieza pierde comportamiento, el
 * troceo es una regresión disfrazada de refactor.
 *
 * Esto clava lo que la pantalla HACE, no cómo está escrita: qué pestañas
 * aparecen según el rol, qué manda cada formulario al backend y qué NO manda
 * cuando el JSON está mal. Debe seguir verde después del corte sin tocar ni una
 * aserción — es el único criterio que distingue un refactor de un cambio.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => currentUser,
}));

import NotificationConfigPage from "@/app/admin/notifications/page";

const CHANNEL = {
  id: "ch-1",
  scope: "tenant",
  channel_type: "telegram",
  name: "Ops bot",
  enabled: true,
  config: { chat_id: "12345" },
  owner_user_id: null,
  has_secret: true,
  secret_source: "vault",
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
};

const CATALOG = [
  { event_type: "task_blocked", label_es: "Tarea bloqueada", label_en: "Task blocked" },
];

function wireApi(over: Record<string, unknown> = {}) {
  const data: Record<string, unknown> = {
    "/notifications/channels": [CHANNEL],
    "/notifications/platform/channel-types": {
      enabled: ["telegram"],
      available: ["telegram", "email"],
    },
    "/notifications/preferences": [],
    "/notifications/event-catalog": CATALOG,
    ...over,
  };
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (opts?.method && opts.method !== "GET") return Promise.resolve({});
    return Promise.resolve(data[path] ?? []);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NotificationConfigPage />
    </QueryClientProvider>,
  );
}

/** La llamada de escritura a `path` con ese método, o `undefined`. */
function writeCall(path: string, method: string) {
  return apiFetchMock.mock.calls.find(
    ([p, o]) => p === path && (o as { method?: string })?.method === method,
  );
}

beforeEach(() => {
  currentUser.isSystemAdmin = true;
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  vi.restoreAllMocks();
});

describe("notificaciones — estructura de pestañas", () => {
  it("muestra Canales y Preferencias siempre, y Plataforma solo al System Admin", async () => {
    wireApi();
    mount();

    expect(await screen.findByTestId("tab-channels")).toBeTruthy();
    expect(screen.getByTestId("tab-preferences")).toBeTruthy();
    expect(screen.getByTestId("tab-platform")).toBeTruthy();
  });

  it("oculta la pestaña de plataforma a quien no es System Admin", async () => {
    currentUser.isSystemAdmin = false;
    wireApi();
    mount();

    expect(await screen.findByTestId("tab-channels")).toBeTruthy();
    expect(screen.queryByTestId("tab-platform")).toBeNull();
  });
});

describe("notificaciones — pestaña de canales", () => {
  it("pinta una tarjeta por canal con su transporte y su origen de secreto", async () => {
    wireApi();
    mount();

    await waitFor(() => expect(screen.getByTestId("channel-card-ch-1")).toBeTruthy());
    expect(screen.getByTestId("channel-type-ch-1").textContent).toBe("telegram");
    expect(screen.getByTestId("channel-secret-ch-1").textContent).toContain("Vault");
  });

  it("muestra el estado vacío sin canales", async () => {
    wireApi({ "/notifications/channels": [] });
    mount();

    await waitFor(() => expect(screen.getByTestId("channels-empty")).toBeTruthy());
  });

  it("el alta manda scope, transporte, nombre y el config ya parseado", async () => {
    wireApi();
    mount();

    fireEvent.click(await screen.findByTestId("channel-create-button"));
    await waitFor(() => expect(screen.getByTestId("channel-dialog")).toBeTruthy());

    fireEvent.change(screen.getByTestId("channel-form-name"), { target: { value: "Bot QA" } });
    fireEvent.change(screen.getByTestId("channel-form-config"), {
      target: { value: '{ "chat_id": "77" }' },
    });
    fireEvent.click(screen.getByTestId("channel-form-submit"));

    await waitFor(() => {
      const post = writeCall("/notifications/channels", "POST");
      expect(post).toBeTruthy();
      expect(post?.[1]?.body).toMatchObject({
        scope: "tenant",
        channel_type: "telegram",
        name: "Bot QA",
        enabled: true,
        config: { chat_id: "77" },
      });
    });
  });

  it("un config con JSON inválido avisa y NO llega a llamar al backend", async () => {
    wireApi();
    mount();

    fireEvent.click(await screen.findByTestId("channel-create-button"));
    await waitFor(() => expect(screen.getByTestId("channel-dialog")).toBeTruthy());

    fireEvent.change(screen.getByTestId("channel-form-name"), { target: { value: "Roto" } });
    fireEvent.change(screen.getByTestId("channel-form-config"), { target: { value: "{ no json" } });
    fireEvent.click(screen.getByTestId("channel-form-submit"));

    await waitFor(() => expect(screen.getByTestId("channel-form-config-error")).toBeTruthy());
    expect(writeCall("/notifications/channels", "POST")).toBeUndefined();
  });

  it("editar un canal manda PUT a su id y no ofrece cambiar el ámbito", async () => {
    wireApi();
    mount();

    fireEvent.click(await screen.findByTestId("channel-edit-ch-1"));
    await waitFor(() => expect(screen.getByTestId("channel-dialog")).toBeTruthy());
    // El scope es inmutable tras el alta: el selector no se ofrece al editar.
    expect(screen.queryByTestId("channel-form-scope")).toBeNull();

    fireEvent.change(screen.getByTestId("channel-form-name"), { target: { value: "Ops bot 2" } });
    fireEvent.click(screen.getByTestId("channel-form-submit"));

    await waitFor(() => {
      const put = writeCall("/notifications/channels/ch-1", "PUT");
      expect(put).toBeTruthy();
      expect(put?.[1]?.body).toMatchObject({ name: "Ops bot 2", enabled: true });
    });
  });

  it("borrar pide confirmación antes de llamar al backend", async () => {
    wireApi();
    mount();

    fireEvent.click(await screen.findByTestId("channel-delete-ch-1"));
    await waitFor(() => expect(writeCall("/notifications/channels/ch-1", "DELETE")).toBeTruthy());
    expect(window.confirm).toHaveBeenCalled();
  });
});

describe("notificaciones — matriz de preferencias", () => {
  it("una fila por evento del catálogo y una columna por transporte configurado", async () => {
    wireApi();
    mount();

    fireEvent.click(await screen.findByTestId("tab-preferences"));
    await waitFor(() => expect(screen.getByTestId("preferences-matrix")).toBeTruthy());
    expect(screen.getByTestId("preferences-row-task_blocked")).toBeTruthy();
    expect(screen.getByTestId("preference-task_blocked-telegram")).toBeTruthy();
  });

  it("sin canales configurados no hay matriz, hay explicación", async () => {
    wireApi({ "/notifications/channels": [] });
    mount();

    fireEvent.click(await screen.findByTestId("tab-preferences"));
    await waitFor(() => expect(screen.getByTestId("preferences-empty")).toBeTruthy());
  });

  it("desmarcar una casilla manda el opt-out con scope de usuario", async () => {
    wireApi();
    mount();

    fireEvent.click(await screen.findByTestId("tab-preferences"));
    await waitFor(() =>
      expect(screen.getByTestId("preference-task_blocked-telegram")).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("preference-task_blocked-telegram"));

    await waitFor(() => {
      const put = writeCall("/notifications/preferences", "PUT");
      expect(put).toBeTruthy();
      expect(put?.[1]?.body).toMatchObject({
        scope: "user",
        event_type: "task_blocked",
        channel_type: "telegram",
        enabled: false,
      });
    });
  });

  it("una regla guardada gana al default ON", async () => {
    wireApi({
      "/notifications/preferences": [
        {
          id: "p1",
          scope: "user",
          event_type: "task_blocked",
          channel_type: "telegram",
          enabled: false,
          owner_user_id: "u1",
          quiet_hours_start: null,
          quiet_hours_end: null,
          quiet_hours_tz: null,
        },
      ],
    });
    mount();

    fireEvent.click(await screen.findByTestId("tab-preferences"));
    await waitFor(() =>
      expect(screen.getByTestId("preference-task_blocked-telegram")).toBeTruthy(),
    );
    const box = screen.getByTestId("preference-task_blocked-telegram") as HTMLInputElement;
    expect(box.checked).toBe(false);
  });
});

describe("notificaciones — pestaña de plataforma", () => {
  it("guarda la lista de transportes habilitados que quedó marcada", async () => {
    wireApi();
    mount();

    fireEvent.click(await screen.findByTestId("tab-platform"));
    await waitFor(() => expect(screen.getByTestId("platform-channel-types")).toBeTruthy());

    // telegram viene habilitado; marcar email debe mandar los dos.
    fireEvent.click(screen.getByTestId("platform-type-email"));
    fireEvent.click(screen.getByTestId("platform-save"));

    await waitFor(() => {
      const put = writeCall("/notifications/platform/channel-types", "PUT");
      expect(put).toBeTruthy();
      const enabled = (put?.[1]?.body as { enabled: string[] }).enabled;
      expect([...enabled].sort()).toEqual(["email", "telegram"]);
    });
  });
});
