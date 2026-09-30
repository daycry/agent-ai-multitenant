"use client";

/**
 * `/admin/memories/[id]` — la ficha de una memoria (`task_mem_20`). Destino de los enlaces de
 * las citas `[mem:…]` del visor de la ejecución y de cada fila de `/admin/memories`.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Brain } from "lucide-react";

import { MemoryCardView } from "@/components/memories/memory-card-view";
import { PageHeader } from "@/components/layout/page-header";
import { useT } from "@/lib/i18n";

export default function MemoryCardPage() {
  const t = useT("memoriesCard");
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";

  return (
    <div
      className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="memory-card-page"
    >
      <Link
        href="/admin/memories"
        className="text-muted-foreground hover:text-foreground mb-3 inline-flex items-center gap-1 text-sm"
        data-testid="memory-card-back"
      >
        <ArrowLeft className="h-4 w-4" />
        {t("back")}
      </Link>
      <PageHeader
        icon={<Brain className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("cardTitle")}
        description={id.slice(0, 8)}
      />
      <div className="mt-6">{id ? <MemoryCardView id={id} /> : null}</div>
    </div>
  );
}
