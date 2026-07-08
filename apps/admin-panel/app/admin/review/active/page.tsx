"use client";

// /admin/review/active?plan=<planId> — resuelve la sesión de review del plan y
// redirige a su detalle real (/admin/review/<sessionId>).
//
// QA humano 2026-07-07: el detalle del plan enlazaba aquí pero la ruta no
// existía, así que la dinámica `[id]` tragaba "active" como session id y el
// panel reventaba en el error boundary. Esta página estática gana a `[id]` en
// el router de Next y hace la resolución vía GET /plans/{id}/review-session.

import { Suspense, useEffect } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Loader2, SearchX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";

interface PlanReviewSessionResponse {
  session_id: string;
  status: string;
  review_url: string;
  app_url: string;
}

export default function ActiveReviewRedirectPage() {
  // useSearchParams needs a Suspense boundary in the App Router.
  return (
    <Suspense fallback={null}>
      <ActiveReviewResolver />
    </Suspense>
  );
}

function ActiveReviewResolver() {
  const search = useSearchParams();
  const router = useRouter();
  const planId = search?.get("plan") ?? "";

  const sessionQuery = useQuery({
    queryKey: ["plan-review-session", planId],
    queryFn: () => apiFetch<PlanReviewSessionResponse>(`/plans/${planId}/review-session`),
    enabled: Boolean(planId),
    retry: false,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (sessionQuery.data?.session_id) {
      router.replace(`/admin/review/${sessionQuery.data.session_id}`);
    }
  }, [sessionQuery.data, router]);

  const notFound = sessionQuery.error instanceof ApiError && sessionQuery.error.status === 404;

  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-16" data-testid="review-active-resolver">
      <Card>
        <CardContent className="flex flex-col items-center gap-4 py-12 text-center">
          {!planId ? (
            <>
              <SearchX className="text-muted-foreground h-10 w-10" />
              <p className="text-sm font-medium">Falta el parámetro ?plan=&lt;id&gt;.</p>
              <p className="text-muted-foreground text-xs">
                Abre esta página desde la tarjeta «Sesión de review» del detalle de un plan.
              </p>
            </>
          ) : notFound ? (
            <>
              <SearchX className="text-muted-foreground h-10 w-10" />
              <p className="text-sm font-medium">Este plan aún no tiene sesión de review.</p>
              <p className="text-muted-foreground text-xs">
                La sesión se crea automáticamente cuando el plan entra en validación humana.
              </p>
            </>
          ) : sessionQuery.isError ? (
            <>
              <SearchX className="text-destructive h-10 w-10" />
              <p className="text-sm font-medium">No se pudo resolver la sesión de review.</p>
              <p className="text-muted-foreground text-xs">
                {String((sessionQuery.error as Error)?.message ?? "error desconocido")}
              </p>
            </>
          ) : (
            <>
              <Loader2 className="text-muted-foreground h-10 w-10 animate-spin" />
              <p className="text-sm font-medium">Resolviendo la sesión de review del plan…</p>
            </>
          )}
          {planId && (
            <Button asChild variant="outline" size="sm">
              <Link href={`/admin/plans/${planId}/escalated`}>Volver al plan</Link>
            </Button>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
