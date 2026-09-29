"use client";

/**
 * task_ui_12 — pestaña costs del proyecto (ver `components/projects/hub/project-costs.tsx`).
 */

import { useParams } from "next/navigation";

import { ProjectCosts } from "@/components/projects/hub/project-costs";

export default function ProjectCostsPage() {
  const params = useParams<{ id: string }>();
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <ProjectCosts projectId={params.id} />
    </div>
  );
}
