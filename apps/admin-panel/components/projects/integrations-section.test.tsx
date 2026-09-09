// @vitest-environment jsdom
// `task_mk_20` (MK-05): la sección «Integraciones» del proyecto — anclas Jira y
// Confluence que el run recibe en su preámbulo.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { ProjectIntegrationsSection } from "@/components/projects/integrations-section";
import { integrationsProblems, toForm, toPayload } from "@/lib/project-integrations";
import { translate } from "@/lib/i18n/translate";

function renderSection(value: Parameters<typeof toForm>[0] = null) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ProjectIntegrationsSection projectId="p-1" value={value} />
    </QueryClientProvider>,
  );
}

function sentBody(): Record<string, unknown> {
  const [path, options] = apiFetchMock.mock.calls[0] as [
    string,
    { method: string; body: Record<string, unknown> },
  ];
  expect(path).toBe("/projects/p-1");
  expect(options.method).toBe("PUT");
  return options.body;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("lib/project-integrations (puro)", () => {
  it("forma ↔ payload: sólo los proveedores con clave principal, sin nulls", () => {
    const form = toForm({
      jira: { project_key: "PLAT", parent_issue_key: "PLAT-120" },
      confluence: { space_key: "ENG", root_page_id: null },
    });
    expect(form.jiraParentIssueKey).toBe("PLAT-120");
    expect(form.confluenceRootPageId).toBe("");
    expect(toPayload(form)).toEqual({
      integrations: {
        jira: { project_key: "PLAT", parent_issue_key: "PLAT-120" },
        confluence: { space_key: "ENG" },
      },
    });
    expect(toPayload(toForm(null))).toEqual({ integrations: {} });
  });

  it("redacta los problemas de formato que la API rechazaría", () => {
    const problems = integrationsProblems(
      {
        jiraProjectKey: "plat",
        jiraParentIssueKey: "PLAT120",
        confluenceSpaceKey: "",
        confluenceRootPageId: "abc",
      },
      "es",
    );
    expect(problems).toEqual([
      translate("es", "projectIntegrations", "problemJiraProjectKey"),
      translate("es", "projectIntegrations", "problemJiraParentIssueKey"),
      translate("es", "projectIntegrations", "problemConfluenceRootWithoutSpace"),
      translate("es", "projectIntegrations", "problemConfluenceRootPageId"),
    ]);
    expect(
      integrationsProblems(
        {
          jiraProjectKey: "",
          jiraParentIssueKey: "",
          confluenceSpaceKey: "",
          confluenceRootPageId: "",
        },
        "es",
      ),
    ).toEqual([]);
  });
});

describe("ProjectIntegrationsSection", () => {
  it("enseña lo que el proyecto ya tiene guardado", () => {
    renderSection({
      jira: { project_key: "PLAT", parent_issue_key: "PLAT-120" },
      confluence: { space_key: "ENG", root_page_id: "123456" },
    });
    expect((screen.getByTestId("integrations-jira-project-key") as HTMLInputElement).value).toBe(
      "PLAT",
    );
    expect(
      (screen.getByTestId("integrations-confluence-root-page-id") as HTMLInputElement).value,
    ).toBe("123456");
  });

  it("guarda con PUT el JSONB validado y sólo lo rellenado", async () => {
    apiFetchMock.mockResolvedValueOnce({});
    renderSection();
    fireEvent.change(screen.getByTestId("integrations-jira-project-key"), {
      target: { value: "PLAT" },
    });
    fireEvent.change(screen.getByTestId("integrations-jira-parent-issue-key"), {
      target: { value: "PLAT-7" },
    });
    fireEvent.click(screen.getByTestId("integrations-save"));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    expect(sentBody()).toEqual({
      integrations: { jira: { project_key: "PLAT", parent_issue_key: "PLAT-7" } },
    });
  });

  it("no deja guardar un formato que la API rechazaría, y dice cuál", () => {
    renderSection();
    fireEvent.change(screen.getByTestId("integrations-jira-project-key"), {
      target: { value: "PLAT" },
    });
    fireEvent.change(screen.getByTestId("integrations-jira-parent-issue-key"), {
      target: { value: "PLAT120" },
    });
    expect(screen.getByTestId("integrations-problems").textContent).toContain(
      translate("es", "projectIntegrations", "problemJiraParentIssueKey"),
    );
    expect((screen.getByTestId("integrations-save") as HTMLButtonElement).disabled).toBe(true);
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("vaciar todo manda `{}`: borra las anclas", async () => {
    apiFetchMock.mockResolvedValueOnce({});
    renderSection({ jira: { project_key: "PLAT" } });
    fireEvent.change(screen.getByTestId("integrations-jira-project-key"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByTestId("integrations-save"));
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalledTimes(1));
    expect(sentBody()).toEqual({ integrations: {} });
  });
});
