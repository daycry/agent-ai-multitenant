// @vitest-environment jsdom
// `task_mk_23` (UI-06): el buscador de tenant del diálogo de compartir.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { TenantPicker } from "@/components/marketplace/tenant-picker";

const DIRECTORY = [
  { id: "22222222-0000-0000-0000-000000000002", name: "Tenant B", slug: "tenant-b" },
  { id: "33333333-0000-0000-0000-000000000003", name: "Tenant C", slug: "tenant-c" },
];

function mount(onChange = vi.fn(), value = "") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TenantPicker value={value} onChange={onChange} />
    </QueryClientProvider>,
  );
  return onChange;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("TenantPicker", () => {
  it("no consulta con menos de dos caracteres", async () => {
    mount();
    fireEvent.change(screen.getByTestId("share-target-input"), { target: { value: "t" } });
    await new Promise((r) => setTimeout(r, 350));
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("busca en el directorio, deja elegir y devuelve el id del tenant", async () => {
    apiFetchMock.mockResolvedValue(DIRECTORY);
    const onChange = mount();
    fireEvent.change(screen.getByTestId("share-target-input"), { target: { value: "tenant" } });

    await waitFor(() => expect(screen.getByTestId("share-target-results")).toBeTruthy());
    expect(apiFetchMock).toHaveBeenCalledWith("/marketplace/shares/tenant-directory?q=tenant");

    fireEvent.click(screen.getByTestId("share-target-option-tenant-b"));
    expect(onChange).toHaveBeenCalledWith(DIRECTORY[0].id, DIRECTORY[0]);
    expect(screen.getByTestId("share-target-selected").textContent).toContain("Tenant B");

    fireEvent.click(screen.getByTestId("share-target-clear"));
    expect(onChange).toHaveBeenLastCalledWith("", null);
    expect(screen.getByTestId("share-target-input")).toBeTruthy();
  });

  it("dice cuando no hay coincidencias", async () => {
    apiFetchMock.mockResolvedValue([]);
    mount();
    fireEvent.change(screen.getByTestId("share-target-input"), { target: { value: "zzzz" } });
    await waitFor(() => expect(screen.getByTestId("share-target-no-matches")).toBeTruthy());
  });
});
