// @vitest-environment jsdom

/**
 * Las tres pantallas de backup, migradas al diccionario (plan prod-16,
 * `task_prod16_03`).
 *
 * Antes de esto el módulo entero estaba cableado en castellano: con el toggle
 * en EN un operador anglófono veía "Programación de backups", "Probar
 * conexión" o "Confirmar restore destructivo" — el hallazgo frontend-9 en su
 * forma más literal.
 *
 * Se prueban los TRES ficheros del módulo juntos a propósito: el criterio del
 * plan es "módulo completo", y media pantalla migrada es peor que ninguna
 * porque deja dos idiomas en la misma vista. Cada bloque afirma en los dos
 * sentidos: que el idioma pedido aparece Y que el otro no se cuela.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

// Las tres pantallas gatean con RoleGuard min="system_admin": sin esto sólo se
// vería el fallback de solo lectura y no se probaría el formulario.
vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    user: { user_id: "u1", email: "root@a.test", full_name: "Root", is_system_admin: true },
    isLoading: false,
    isError: false,
    isSystemAdmin: true,
    isSystemOwner: true,
    isTenantAdmin: true,
    isTenantMember: true,
    roleInActiveTenant: "tenant_admin",
  }),
}));

import BackupDestinationsPage from "@/app/admin/backup/destinations/page";
import BackupSchedulePage from "@/app/admin/backup/page";
import RestorePage from "@/app/admin/backup/restore/page";

const STORAGE_KEY = "admin-panel.lang";

function routeApi(path: string): unknown {
  if (path === "/admin/backup/schedule") {
    return { enabled: true, cron: "0 3 * * *", retention_days: 7 };
  }
  if (path === "/admin/backup/destinations") {
    return {
      destinations: [{ type: "s3", name: "offsite", enabled: true, config: { bucket: "b" } }],
    };
  }
  if (path === "/admin/backup/restore/backups") {
    return {
      backups: [
        {
          backup_id: "bk-1",
          encrypted: true,
          created_at: "2026-07-31T03:00:00Z",
          total_size_bytes: 2048,
          locations: ["local"],
        },
      ],
    };
  }
  if (path.endsWith("/preview")) {
    return {
      backup_id: "bk-1",
      encrypted: true,
      created_at: "2026-07-31T03:00:00Z",
      status: "ok",
      total_size_bytes: 2048,
      artifacts: [{ name: "db.sql.gz", kind: "postgres", size_bytes: 1024, source: "local" }],
      per_tenant_available: true,
      tenant_scoped_tables: ["projects", "agents"],
    };
  }
  throw new Error(`unexpected endpoint in test: ${path}`);
}

function renderIn(lang: "es" | "en", node: React.ReactElement) {
  apiFetchMock.mockImplementation((path: string) => Promise.resolve(routeApi(path)));
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
});

describe("backup/page — programación", () => {
  it("en castellano rinde cabecera, etiquetas y ayuda del cron", async () => {
    renderIn("es", <BackupSchedulePage />);

    expect(await screen.findByText("Programación de backups")).toBeDefined();
    expect(await screen.findByLabelText("Backup diario activado")).toBeDefined();
    expect(screen.getByLabelText("Cron (ventana horaria)")).toBeDefined();
    expect(screen.getByLabelText("Retención local (días)")).toBeDefined();
    expect(screen.getByRole("button", { name: "Guardar" })).toBeDefined();
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <BackupSchedulePage />);

    expect(await screen.findByText("Backup schedule")).toBeDefined();
    expect(await screen.findByLabelText("Daily backup enabled")).toBeDefined();
    expect(screen.getByLabelText("Cron (time window)")).toBeDefined();
    expect(screen.getByLabelText("Local retention (days)")).toBeDefined();
    expect(screen.getByRole("button", { name: "Save" })).toBeDefined();

    expect(screen.queryByText("Programación de backups")).toBeNull();
    expect(screen.queryByLabelText("Retención local (días)")).toBeNull();
    expect(screen.queryByRole("button", { name: "Guardar" })).toBeNull();
  });
});

describe("backup/destinations — destinos remotos", () => {
  it("en castellano rinde cabecera, campos del tipo y el botón de prueba", async () => {
    renderIn("es", <BackupDestinationsPage />);

    expect(await screen.findByText("Destinos remotos de backup")).toBeDefined();
    expect(await screen.findByLabelText("Nombre")).toBeDefined();
    expect(screen.getByLabelText("Habilitado")).toBeDefined();
    // El label del campo depende del tipo (s3 → bucket/prefijo/región).
    expect(screen.getByLabelText("Prefijo")).toBeDefined();
    expect(screen.getByRole("button", { name: "Probar conexión" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Eliminar destino" })).toBeDefined();
  });

  it("en inglés traduce también los labels de los campos por tipo", async () => {
    renderIn("en", <BackupDestinationsPage />);

    expect(await screen.findByText("Remote backup destinations")).toBeDefined();
    expect(await screen.findByLabelText("Name")).toBeDefined();
    expect(screen.getByLabelText("Enabled")).toBeDefined();
    expect(screen.getByLabelText("Prefix")).toBeDefined();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Remove destination" })).toBeDefined();

    expect(screen.queryByLabelText("Prefijo")).toBeNull();
    expect(screen.queryByRole("button", { name: "Probar conexión" })).toBeNull();
  });

  it("en inglés traduce los campos de SFTP al cambiar de tipo", async () => {
    renderIn("en", <BackupDestinationsPage />);

    const select = await screen.findByTestId("backup-destination-type-0");
    fireEvent.change(select, { target: { value: "sftp" } });

    await waitFor(() => expect(screen.getByLabelText("Remote path")).toBeDefined());
    // Los requeridos llevan " *" pegado al label, de ahí la expresión regular.
    expect(screen.getByLabelText(/^Username/)).toBeDefined();
    expect(screen.queryByLabelText("Ruta remota")).toBeNull();
    expect(screen.queryByLabelText(/^Usuario/)).toBeNull();
  });
});

describe("backup/restore — restaurar", () => {
  it("en castellano rinde la lista y el diálogo destructivo", async () => {
    renderIn("es", <RestorePage />);

    expect(await screen.findByText("Restaurar desde backup")).toBeDefined();
    expect(await screen.findByText("Backups disponibles")).toBeDefined();

    fireEvent.click(await screen.findByTestId("restore-backup-bk-1"));
    expect(await screen.findByText("Preview del backup")).toBeDefined();
    expect(
      await screen.findByText("Restore completo (detiene el stack y restaura todo)"),
    ).toBeDefined();
    expect(screen.getByText("Artefactos")).toBeDefined();

    fireEvent.click(screen.getByTestId("restore-open-confirm"));
    expect(await screen.findByText("Confirmar restore destructivo")).toBeDefined();
    expect(screen.getByRole("button", { name: "Cancelar" })).toBeDefined();
  });

  it("en inglés rinde la misma secuencia traducida", async () => {
    renderIn("en", <RestorePage />);

    expect(await screen.findByText("Restore from backup")).toBeDefined();
    expect(await screen.findByText("Available backups")).toBeDefined();

    fireEvent.click(await screen.findByTestId("restore-backup-bk-1"));
    expect(await screen.findByText("Backup preview")).toBeDefined();
    expect(
      await screen.findByText("Full restore (stops the stack and restores everything)"),
    ).toBeDefined();
    expect(screen.getByText("Artifacts")).toBeDefined();
    expect(screen.queryByText("Artefactos")).toBeNull();

    fireEvent.click(screen.getByTestId("restore-open-confirm"));
    expect(await screen.findByText("Confirm destructive restore")).toBeDefined();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDefined();

    expect(screen.queryByText("Restaurar desde backup")).toBeNull();
    expect(screen.queryByText("Confirmar restore destructivo")).toBeNull();
  });
});
