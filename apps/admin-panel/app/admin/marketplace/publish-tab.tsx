"use client";

/**
 * Pestaña «Publicar» del marketplace (`task_ui_32`): el sitio donde acaba lo que sale del
 * tenant. Publicar un listing propio (que entra en la COLA DE REVISIÓN antes de llegar
 * al catálogo, ADR 0142 D6), llegar a esa cola si eres System Admin, y compartir con otro
 * tenant (la antigua pestaña «Compartir»).
 */

import Link from "next/link";
import { ClipboardCheck, PackagePlus } from "lucide-react";

import { SharesTab } from "./shares-tab";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { useT } from "@/lib/i18n";

export function PublishTab() {
  const t = useT("marketplace");
  const tReview = useT("marketplaceReview");
  return (
    <div className="space-y-4">
      <Card data-testid="publish-tab-card">
        <CardContent className="flex flex-col gap-3 py-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1">
            <p className="text-sm">{t("publishTabIntro")}</p>
            <p className="text-muted-foreground text-xs">{tReview("beforePublish")}</p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <RoleGuard min="tenant_admin">
              <Button asChild size="sm" data-testid="marketplace-publish-tab-cta">
                <Link href="/admin/marketplace/private">
                  <PackagePlus className="mr-1 h-3.5 w-3.5" />
                  {t("publishTabPrivateCta")}
                </Link>
              </Button>
            </RoleGuard>
            <RoleGuard min="system_admin">
              <Button asChild variant="outline" size="sm" data-testid="marketplace-review-link">
                <Link href="/admin/marketplace/review">
                  <ClipboardCheck className="mr-1 h-3.5 w-3.5" />
                  {t("publishTabReviewCta")}
                </Link>
              </Button>
            </RoleGuard>
          </div>
        </CardContent>
      </Card>
      <SharesTab />
    </div>
  );
}
