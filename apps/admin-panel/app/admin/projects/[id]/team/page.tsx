"use client";

/**
 * task_ui_12 — pestaña team del proyecto (ver `components/projects/hub/project-team.tsx`).
 */

import { useParams } from "next/navigation";

import { ProjectTeam } from "@/components/projects/hub/project-team";

export default function ProjectTeamPage() {
  const params = useParams<{ id: string }>();
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <ProjectTeam projectId={params.id} />
    </div>
  );
}
