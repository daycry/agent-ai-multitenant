// @vitest-environment jsdom
// task_ui_03 — la bandeja humana unificada: cinco pestañas con contador y
// «nada pendiente». Los endpoints son los que ya existían.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});
let tabParam: string | null = null;
const replaceMock = vi.fn();
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: pushMock }),
  useSearchParams: () => ({ get: (k: string) => (k === "tab" ? tabParam : null) }),
}));
vi.mock("@/components/ui/role-guard", () => ({
  RoleGuard: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import InboxPage from "@/app/admin/inbox/page";

const APPROVAL = {
  id: "ap1",
  execution_id: "e1",
  task_id: "t1",
  project_id: "p1",
  category: "production_deploy",
  action: { tool: "shell_exec" },
  status: "pending",
  requested_at: "2026-09-01T10:00:00Z",
};
const QUESTION = {
  ...APPROVAL,
  id: "q1",
  category: "human_question",
  action: { args: { question: "¿Qué rama uso?", options: ["main"] } },
};
const REVIEW = {
  assignment_id: "r1",
  task_id: "t2",
  human_agent_id: null,
  assignment_status: "pending_acceptance",
  task_status: "in_review",
  assigned_at: "2026-09-01T10:00:00Z",
  task_title: "Revisar login",
  task_description: null,
  project_id: "p1",
  project_name: "Demo",
  plan_id: null,
  plan_title: null,
  submitted_output: "hecho",
  retry_count: 0,
};

function routes(data: Record<string, unknown>) {
  apiFetchMock.mockImplementation((path: string) => {
    const key = Object.keys(data).find((k) => path.startsWith(k));
    return Promise.resolve(key ? data[key] : []);
  });
}
const NOTIFS = (unread: number) => ({ items: [], total: unread, unread, limit: 1, offset: 0 });

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <InboxPage />
    </QueryClientProvider>,
  );
}
const count = (tab: string) => screen.getByTestId(`inbox-count-${tab}`).textContent;

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  replaceMock.mockReset();
  tabParam = null;
});

describe("bandeja humana unificada", () => {
  it("cada pestaña lleva el contador de su fuente", async () => {
    routes({
      "/approvals": [APPROVAL, QUESTION, { ...QUESTION, id: "q2" }],
      "/inbox/reviews": [REVIEW],
      "/notifications/logs": NOTIFS(4),
      "/inbox/assignments": [],
      "/human-queue": [
        {
          kind: "plan_validation",
          id: "pv",
          title: "Plan",
          project_name: null,
          age_seconds: 60,
          url_path: "/x",
        },
        {
          kind: "approval_request",
          id: "ar",
          title: "dup",
          project_name: null,
          age_seconds: 60,
          url_path: "/y",
        },
      ],
    });
    mount();
    await waitFor(() => expect(count("questions")).toBe("2"));
    // 1 aprobación + 1 decisión de la cola que no es una solicitud (la solicitud ya sale como tarjeta)
    expect(count("approvals")).toBe("2");
    expect(count("reviews")).toBe("1");
    expect(count("notifications")).toBe("4");
    expect(count("assigned")).toBe("0");
    expect(screen.queryByTestId("inbox-all-clear")).toBeNull();
  });

  it("nada pendiente: todos los contadores a 0 y el mensaje de paz", async () => {
    routes({ "/notifications/logs": NOTIFS(0) });
    mount();
    expect(await screen.findByTestId("inbox-all-clear")).toBeTruthy();
    for (const tab of ["approvals", "questions", "reviews", "notifications", "assigned"]) {
      expect(count(tab)).toBe("0");
    }
  });

  it("las preguntas ask_human salen en su pestaña, no en Aprobaciones", async () => {
    routes({ "/approvals": [APPROVAL, QUESTION], "/notifications/logs": NOTIFS(0) });
    mount();
    expect(await screen.findByTestId("approval-card-ap1")).toBeTruthy();
    expect(screen.queryByTestId("approval-card-q1")).toBeNull();
    fireEvent.click(screen.getByTestId("inbox-tab-questions"));
    expect(await screen.findByTestId("approval-card-q1")).toBeTruthy();
    expect(screen.queryByTestId("approval-card-ap1")).toBeNull();
    expect(replaceMock).toHaveBeenCalledWith("/admin/inbox?tab=questions", expect.anything());
  });

  it("?tab= abre la pestaña pedida; las tareas asignadas siguen ahí", async () => {
    tabParam = "assigned";
    routes({ "/inbox/assignments": [], "/notifications/logs": NOTIFS(0) });
    mount();
    expect(await screen.findByTestId("inbox-tab-active")).toBeTruthy();
    expect(await screen.findByTestId("inbox-empty")).toBeTruthy();
  });

  it("aprobar una solicitud hace POST /approvals/{id}/resolve", async () => {
    routes({ "/approvals": [APPROVAL], "/notifications/logs": NOTIFS(0) });
    mount();
    fireEvent.click(await screen.findByTestId("approve-ap1"));
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        "/approvals/ap1/resolve",
        expect.objectContaining({ method: "POST", body: { approved: true, reason: null } }),
      ),
    );
  });

  it("la cola humana enseña las otras decisiones con su edad y enlace", async () => {
    routes({
      "/notifications/logs": NOTIFS(0),
      "/human-queue": [
        {
          kind: "plan_validation",
          id: "pv",
          title: "Plan esperando",
          project_name: "Demo",
          age_seconds: 30 * 3600,
          url_path: "/admin/plans/pv",
        },
      ],
    });
    mount();
    const row = await screen.findByTestId("hq-item-0");
    expect(row.textContent).toContain("Plan esperando");
    expect(row.textContent).toContain("1 d");
    fireEvent.click(row);
    expect(pushMock).toHaveBeenCalledWith("/admin/plans/pv");
  });
});

describe("bandeja humana: carga y error no son «nada pendiente»", () => {
  function failing(failPaths: string[], data: Record<string, unknown> = {}) {
    apiFetchMock.mockImplementation((path: string) => {
      if (failPaths.some((f) => path.startsWith(f))) return Promise.reject(new Error("boom"));
      const key = Object.keys(data).find((k) => path.startsWith(k));
      return Promise.resolve(key ? data[key] : []);
    });
  }

  it("si /inbox/reviews da 500, su contador no es 0 y no hay «Nada pendiente»", async () => {
    failing(["/inbox/reviews"], { "/notifications/logs": NOTIFS(0) });
    mount();
    await waitFor(() => expect(count("reviews")).toBe("!"));
    expect(count("approvals")).toBe("0");
    expect(screen.queryByTestId("inbox-all-clear")).toBeNull();
  });

  it("mientras cargan las consultas no hay «Nada pendiente» ni contadores a 0", async () => {
    apiFetchMock.mockImplementation(() => new Promise(() => undefined));
    mount();
    expect(count("approvals")).toBe("…");
    expect(count("reviews")).toBe("…");
    expect(screen.queryByTestId("inbox-all-clear")).toBeNull();
  });

  it("si falla /human-queue, Aprobaciones lo dice en vez de esconderlo", async () => {
    failing(["/human-queue"], { "/approvals": [APPROVAL], "/notifications/logs": NOTIFS(0) });
    mount();
    expect(await screen.findByTestId("human-queue-error")).toBeTruthy();
    expect(screen.getByTestId("approval-card-ap1")).toBeTruthy();
    expect(count("approvals")).toBe("!");
  });

  it("las Revisiones muestran su error, no una lista vacía", async () => {
    tabParam = "reviews";
    failing(["/inbox/reviews"], { "/notifications/logs": NOTIFS(0) });
    mount();
    expect(await screen.findByTestId("reviews-error")).toBeTruthy();
  });

  it("sin nada por aprobar y con tareas asignadas, abre en «Asignadas a mí»", async () => {
    routes({
      "/inbox/assignments": [{ id: "a1" }],
      "/notifications/logs": NOTIFS(0),
    });
    mount();
    await waitFor(() =>
      expect(screen.getByTestId("inbox-tab-assigned").getAttribute("data-state")).toBe("active"),
    );
  });
});
