"use client";

/**
 * task_ui_12 — pestaña runs del proyecto (ver `components/projects/hub/project-runs.tsx`).
 */

import { useParams } from "next/navigation";

import { ProjectRuns } from "@/components/projects/hub/project-runs";

export default function ProjectRunsPage() {
  const params = useParams<{ id: string }>();
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <ProjectRuns projectId={params.id} />
    </div>
  );
}
