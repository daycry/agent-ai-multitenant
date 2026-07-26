// @vitest-environment jsdom
// `task_wf_35`: la sección de límites y gobierno del proyecto.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { ProjectGovernanceSection } from "@/components/projects/governance-section";
import type { GovernanceValue } from "@/lib/project-governance";

function renderSection(value: GovernanceValue | null = null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ProjectGovernanceSection projectId="p-1" value={value} />
    </QueryClientProvider>,
  );
}

function sentBody(): Record<string, unknown> {
  const [, options] = apiFetchMock.mock.calls[0] as [string, { body: Record<string, unknown> }];
  return options.body;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ProjectGovernanceSection", () => {
  it("shows what the project already has stored", () => {
    renderSection({
      execution_budgets: { max_tokens: 120000 },
      budget_amount: "250",
      budget_currency: "EUR",
      budget_period: "monthly",
      human_task_review_mode: "peer_human_reviewer",
    });
    expect((screen.getByTestId("exec-budget-max_tokens") as HTMLInputElement).value).toBe("120000");
    expect((screen.getByTestId("budget-currency") as HTMLInputElement).value).toBe("EUR");
    expect((screen.getByTestId("human-task-review-mode") as HTMLSelectElement).value).toBe(
      "peer_human_reviewer",
    );
  });

  it("sends only the budget the operator filled in, and null for the rest", async () => {
    apiFetchMock.mockResolvedValueOnce({});
    renderSection();

    fireEvent.change(screen.getByTestId("exec-budget-max_cost_usd"), { target: { value: "2" } });
    fireEvent.click(screen.getByTestId("governance-save"));

    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    const body = sentBody();
    expect(body.execution_budgets).toEqual({ max_cost_usd: 2 });
    // Un `0` aquí sería un presupuesto de cero y ningún run arrancaría.
    expect(body.budget_amount).toBeNull();
    expect(body.guardrails_config).toBeNull();
  });

  it("refuses to send a budget the resolver would silently discard", () => {
    renderSection();
    fireEvent.change(screen.getByTestId("exec-budget-max_tokens"), { target: { value: "0" } });

    expect(screen.getByTestId("governance-problems").textContent).toContain("mayor que cero");
    expect((screen.getByTestId("governance-save") as HTMLButtonElement).disabled).toBe(true);
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("refuses malformed guardrails before the round-trip", () => {
    renderSection();
    fireEvent.change(screen.getByTestId("guardrails-config"), { target: { value: "{nope" } });

    expect(screen.getByTestId("governance-problems").textContent).toContain("JSON válido");
    expect((screen.getByTestId("governance-save") as HTMLButtonElement).disabled).toBe(true);
  });

  it("only asks for the custom-period fields when the period is custom", () => {
    renderSection();
    expect(screen.queryByTestId("budget-start-day")).toBeNull();
    fireEvent.change(screen.getByTestId("budget-period"), { target: { value: "custom" } });
    expect(screen.getByTestId("budget-start-day")).toBeTruthy();
  });

  it("surfaces a rejected save instead of pretending it worked", async () => {
    apiFetchMock.mockRejectedValueOnce(new Error("422: presupuesto desconocido"));
    renderSection();

    fireEvent.click(screen.getByTestId("governance-save"));

    expect((await screen.findByTestId("governance-error")).textContent).toContain("422");
  });
});
