// task_ui_03 — las tres rutas antiguas siguen respondiendo: redirigen a su pestaña.

import { beforeEach, describe, expect, it, vi } from "vitest";

const redirectMock = vi.fn();
vi.mock("next/navigation", () => ({
  redirect: (to: string) => redirectMock(to),
}));

import ApprovalsPage from "@/app/admin/approvals/page";
import HumanQueuePage from "@/app/admin/human-queue/page";
import NotificationsInboxPage from "@/app/admin/notifications/inbox/page";

beforeEach(() => redirectMock.mockReset());

describe("redirects a la bandeja unificada", () => {
  it("/admin/approvals -> pestaña Aprobaciones", () => {
    ApprovalsPage();
    expect(redirectMock).toHaveBeenCalledWith("/admin/inbox?tab=approvals");
  });
  it("/admin/human-queue -> pestaña Aprobaciones (donde vive la cola)", () => {
    HumanQueuePage();
    expect(redirectMock).toHaveBeenCalledWith("/admin/inbox?tab=approvals");
  });
  it("/admin/notifications/inbox -> pestaña Notificaciones", () => {
    NotificationsInboxPage();
    expect(redirectMock).toHaveBeenCalledWith("/admin/inbox?tab=notifications");
  });
});
