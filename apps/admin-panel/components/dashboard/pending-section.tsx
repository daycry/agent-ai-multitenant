"use client";

import Link from "next/link";

import { SectionCard, SectionEmpty } from "@/components/dashboard/section-card";
import { ageParts, type DashboardPending } from "@/lib/dashboard/tenant-dashboard";
import { useT } from "@/lib/i18n";

const KIND_KEYS = {
  plan_validation: "kindPlanValidation",
  plan_unblock: "kindPlanUnblock",
  approval_request: "kindApprovalRequest",
  run_review: "kindRunReview",
  run_approval: "kindRunApproval",
} as const;

/** 1 — Lo que espera a un humano. Lo primero del dashboard, por decisión del operador. */
export function PendingSection({ pending }: { pending: DashboardPending }) {
  const t = useT("tenantDashboard");
  const age = (seconds: number) => {
    const { value, unit } = ageParts(seconds);
    const key = unit === "m" ? "ageMinutes" : unit === "h" ? "ageHours" : "ageDays";
    return t(key, { value });
  };
  const kindLabel = (kind: string) => t(KIND_KEYS[kind as keyof typeof KIND_KEYS] ?? "kindOther");
  const rest = pending.total - pending.oldest.length;
  return (
    <SectionCard
      title={t("pendingTitle")}
      testid="dashboard-pending"
      action={
        pending.total > 0 ? (
          <Link href="/admin/inbox" className="text-primary text-sm hover:underline">
            {t("pendingAll")}
          </Link>
        ) : undefined
      }
    >
      {pending.total === 0 ? (
        <SectionEmpty testid="dashboard-pending-empty">{t("pendingEmpty")}</SectionEmpty>
      ) : (
        <ul className="flex flex-col gap-2" data-testid="dashboard-pending-list">
          {pending.oldest.map((item) => (
            <li key={`${item.kind}-${item.id}`} className="flex flex-col">
              <Link href={item.url_path} className="text-sm font-medium hover:underline">
                {item.title}
              </Link>
              <span className="text-muted-foreground text-xs">
                {kindLabel(item.kind)}
                {item.project_name ? ` · ${item.project_name}` : ""} · {age(item.age_seconds)}
              </span>
            </li>
          ))}
          {rest > 0 && (
            <li className="text-muted-foreground text-xs" data-testid="dashboard-pending-more">
              {t("pendingMore", { count: rest })}
            </li>
          )}
        </ul>
      )}
    </SectionCard>
  );
}
