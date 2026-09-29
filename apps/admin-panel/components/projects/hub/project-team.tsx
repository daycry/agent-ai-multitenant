"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { SectionCard, SectionEmpty } from "@/components/dashboard/section-card";
import { StateBlock } from "@/components/shared/state-block";
import { Badge } from "@/components/ui/badge";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import {
  groupByRole,
  type ProjectTeamData,
  type RoleGap,
  type TeamMember,
} from "@/lib/projects/project-hub";

function MemberLink({ member }: { member: TeamMember }) {
  const t = useT("projectTabs");
  return (
    <Link
      href={member.url_path}
      className="text-sm font-medium hover:underline"
      data-testid={`team-member-${member.agent_id}`}
    >
      {member.name}
      {member.is_team_leader && (
        <Badge variant="muted" className="ml-2">
          {t("teamLeader")}
        </Badge>
      )}
    </Link>
  );
}

function GapItem({ gap }: { gap: RoleGap }) {
  const t = useT("projectTabs");
  return (
    <li className="flex flex-col gap-1" data-testid={`team-gap-${gap.role}`}>
      <span className="text-warning text-sm font-medium">{gap.role}</span>
      <span className="text-muted-foreground text-xs">
        {t("gapAsked", { plans: gap.plans.join(", ") })}
      </span>
      {gap.candidates.length === 0 ? (
        <span className="text-muted-foreground text-xs">{t("gapNoCandidates")}</span>
      ) : (
        <span className="text-xs">
          {t("gapCandidates")}{" "}
          {gap.candidates.map((c) => (
            <Link
              key={c.agent_id}
              href={c.url_path}
              className="mr-2 font-medium hover:underline"
              data-testid={`team-gap-candidate-${c.agent_id}`}
            >
              {c.name}
            </Link>
          ))}
        </span>
      )}
    </li>
  );
}

/**
 * La pestaña Equipo del proyecto (`task_ui_12`): el equipo asignado, los agentes de IA por rol,
 * los agentes humanos y los huecos de rol (roles que piden los planes activos y el equipo no
 * cubre; los mismos que avisa el Resumen), cada agente enlazado a su ficha.
 */
export function ProjectTeam({ projectId }: { projectId: string }) {
  const t = useT("projectTabs");
  const team = useQuery({
    queryKey: ["project-team", projectId],
    queryFn: () => apiFetch<ProjectTeamData>(`/projects/${projectId}/team`),
  });
  const data = team.data;
  const ai = groupByRole((data?.members ?? []).filter((m) => m.agent_type !== "human"));
  const humans = (data?.members ?? []).filter((m) => m.agent_type === "human");

  return (
    <section data-testid="project-team" aria-label={t("teamTitle")}>
      <h2 className="mb-3 text-lg font-semibold">{t("teamTitle")}</h2>
      <StateBlock
        isLoading={team.isLoading}
        isError={team.isError}
        error={team.error}
        loadingLabel={t("teamLoading")}
        loadingSkeleton
        skeletonRows={4}
        errorTitle={t("teamError", { detail: "" })}
        errorTestId="team-error"
      >
        {data && (
          <div className="flex flex-col gap-4">
            {data.team ? (
              <p className="text-sm font-medium" data-testid="team-assigned">
                {t("teamAssigned", { name: data.team.name })}
              </p>
            ) : (
              <p className="text-muted-foreground text-sm" data-testid="team-none">
                {t("teamNone")}
              </p>
            )}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <SectionCard title={t("teamAiTitle")} testid="team-ai">
                {ai.length === 0 ? (
                  <SectionEmpty testid="team-ai-empty">{t("teamAiEmpty")}</SectionEmpty>
                ) : (
                  <ul className="flex flex-col gap-3">
                    {ai.map(([role, members]) => (
                      <li key={role} data-testid={`team-role-${role}`}>
                        <span className="text-muted-foreground text-xs uppercase">{role}</span>
                        <div className="flex flex-wrap gap-x-4 gap-y-1">
                          {members.map((m) => (
                            <MemberLink key={m.agent_id} member={m} />
                          ))}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </SectionCard>
              <SectionCard title={t("teamHumansTitle")} testid="team-humans">
                {humans.length === 0 ? (
                  <SectionEmpty testid="team-humans-empty">{t("teamHumansEmpty")}</SectionEmpty>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {humans.map((m) => (
                      <li key={m.agent_id} className="flex flex-col">
                        <MemberLink member={m} />
                        <span className="text-muted-foreground text-xs">{m.role}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </SectionCard>
              <div className="lg:col-span-2">
                <SectionCard title={t("gapsTitle")} testid="team-gaps">
                  {data.role_gaps.length === 0 ? (
                    <SectionEmpty testid="team-gaps-none">{t("gapsNone")}</SectionEmpty>
                  ) : (
                    <ul className="flex flex-col gap-3">
                      {data.role_gaps.map((gap) => (
                        <GapItem key={gap.role} gap={gap} />
                      ))}
                    </ul>
                  )}
                </SectionCard>
              </div>
            </div>
          </div>
        )}
      </StateBlock>
    </section>
  );
}
