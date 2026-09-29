"use client";

/**
 * Bandeja humana unificada (task_ui_03): todo lo que espera una decisión tuya en
 * una pantalla con pestañas y un contador por pestaña.
 *
 *   - Aprobaciones    `GET /approvals` (sin `human_question`) + la cola humana
 *   - Preguntas       `GET /approvals` con `category == "human_question"` (`ask_human`)
 *   - Revisiones      `GET /inbox/reviews`
 *   - Notificaciones  `GET /notifications/logs` (histórico + sin leer)
 *   - Asignadas a mí  `GET /inbox/assignments` (la bandeja personal del Plan 16)
 *
 * `?tab=` elige la pestaña: `/admin/approvals`, `/admin/human-queue` y
 * `/admin/notifications/inbox` redirigen aquí a la suya.
 */

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Bell,
  ClipboardCheck,
  HelpCircle,
  Home,
  Inbox,
  ListChecks,
  ShieldCheck,
} from "lucide-react";

import { AssignmentsPanel } from "@/app/admin/inbox/assignments-panel";
import { NotificationsPanel } from "@/components/inbox/notifications-panel";
import { RequestsTab } from "@/components/inbox/requests-tab";
import { ReviewsTab } from "@/components/inbox/reviews-tab";
import {
  INBOX_TABS,
  defaultTab,
  isAllClear,
  useInboxCounts,
  type InboxTabId,
  type TabCount,
} from "@/components/inbox/use-inbox-counts";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";

function isTab(value: string | null | undefined): value is InboxTabId {
  return INBOX_TABS.some((tab) => tab === value);
}

/** «…» mientras carga, «!» si la consulta falló: nunca un 0 inventado. */
function countText(count: TabCount): string {
  if (count.state === "loading") return "…";
  if (count.state === "error") return "!";
  return String(count.value);
}

function InboxHub() {
  const t = useT("inboxHub");
  const router = useRouter();
  const requested = useSearchParams()?.get("tab");
  const counts = useInboxCounts();
  // Pestaña ACTIVA, controlada: nada la remonta por un cambio de contador (un
  // `key` derivado de los contadores tiraba lo que el usuario escribía en el
  // sondeo de 30 s). El default por contadores se decide UNA vez, cuando los dos
  // contadores que lo determinan cargan por primera vez; `?tab=` o un clic lo
  // deciden antes y mandan siempre.
  const [active, setActive] = useState<InboxTabId>(isTab(requested) ? requested : "approvals");
  const decided = useRef(isTab(requested));
  const relevantLoaded =
    counts.approvals.state !== "loading" && counts.assigned.state !== "loading";
  useEffect(() => {
    if (isTab(requested)) {
      decided.current = true;
      setActive(requested);
    }
  }, [requested]);
  useEffect(() => {
    if (decided.current || !relevantLoaded) return;
    decided.current = true;
    setActive(defaultTab(counts));
  }, [relevantLoaded, counts]);
  const allClear = isAllClear(counts);

  const labels: Record<InboxTabId, { text: string; icon: React.ReactNode }> = {
    approvals: { text: t("tabApprovals"), icon: <ShieldCheck className="mr-1.5 h-4 w-4" /> },
    questions: { text: t("tabQuestions"), icon: <HelpCircle className="mr-1.5 h-4 w-4" /> },
    reviews: { text: t("tabReviews"), icon: <ClipboardCheck className="mr-1.5 h-4 w-4" /> },
    notifications: { text: t("tabNotifications"), icon: <Bell className="mr-1.5 h-4 w-4" /> },
    assigned: { text: t("tabAssigned"), icon: <ListChecks className="mr-1.5 h-4 w-4" /> },
  };

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8">
      <Breadcrumb
        items={[
          { label: t("home"), href: "/admin", icon: <Home className="h-3.5 w-3.5" /> },
          { label: t("title") },
        ]}
      />
      <PageHeader
        icon={<Inbox className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
      />

      {allClear && (
        <p className="text-muted-foreground mt-4 text-sm" data-testid="inbox-all-clear">
          {t("allClear")}
        </p>
      )}

      <Tabs
        defaultValue={active}
        value={active}
        onValueChange={(next) => {
          if (isTab(next)) {
            decided.current = true;
            setActive(next);
          }
          router.replace(`/admin/inbox?tab=${next}`, { scroll: false });
        }}
        className="mt-6"
      >
        <TabsList data-testid="inbox-hub-tabs" aria-label={t("tabsAria")} className="flex-wrap">
          {INBOX_TABS.map((tab) => (
            <TabsTrigger key={tab} value={tab} data-testid={`inbox-tab-${tab}`}>
              {labels[tab].icon}
              {labels[tab].text}
              <Badge
                variant={
                  counts[tab].state === "error" ||
                  (counts[tab].state === "ok" && counts[tab].value > 0)
                    ? "warning"
                    : "muted"
                }
                className="ml-1.5"
                data-testid={`inbox-count-${tab}`}
              >
                {countText(counts[tab])}
              </Badge>
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="approvals">
          <RequestsTab kind="approvals" />
        </TabsContent>
        <TabsContent value="questions">
          <RequestsTab kind="questions" />
        </TabsContent>
        <TabsContent value="reviews">
          <ReviewsTab />
        </TabsContent>
        <TabsContent value="notifications">
          <NotificationsPanel />
        </TabsContent>
        <TabsContent value="assigned">
          <AssignmentsPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function InboxPage() {
  // useSearchParams necesita una frontera Suspense en el App Router.
  return (
    <Suspense fallback={null}>
      <InboxHub />
    </Suspense>
  );
}
