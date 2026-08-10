"use client";

import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ApiError } from "@/lib/api";
import { apiFetch } from "@/lib/api";

import type { MemberUpdate, Team, TeamMember } from "./team-types";

// ---------------------------------------------------------------------------
// Member metadata edit dialog (Plan 06.17 task_06_17_15, ADR 0053)
//
// La única escritura nueva de la UI a nivel de equipo: invoca el
// `PUT /teams/{id}/members/{agent_id}` ya existente para fijar
// is_team_leader / role_in_team / assignment_priority.
// ---------------------------------------------------------------------------

export function MemberEditDialog({
  teamId,
  member,
  agentName,
  open,
  onOpenChange,
  onSaved,
}: {
  teamId: string;
  member: TeamMember;
  agentName: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [isLeader, setIsLeader] = useState(member.is_team_leader);
  const [roleInTeam, setRoleInTeam] = useState(member.role_in_team ?? "");
  const [priority, setPriority] = useState(String(member.assignment_priority));

  useEffect(() => {
    if (open) {
      setIsLeader(member.is_team_leader);
      setRoleInTeam(member.role_in_team ?? "");
      setPriority(String(member.assignment_priority));
    }
  }, [open, member.is_team_leader, member.role_in_team, member.assignment_priority]);

  const mutation = useMutation<Team, ApiError, MemberUpdate>({
    mutationFn: (payload) =>
      apiFetch<Team>(`/teams/${teamId}/members/${member.agent_id}`, {
        method: "PUT",
        body: payload,
      }),
    onSuccess: onSaved,
  });

  const priorityNum = Number(priority);
  const priorityValid = Number.isInteger(priorityNum) && priorityNum >= 0 && priorityNum <= 1000;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="member-edit-dialog">
        <DialogHeader>
          <DialogTitle>Editar miembro</DialogTitle>
          <DialogDescription>
            Metadata de <strong>{agentName}</strong> en este equipo: si es líder, su rol y su
            prioridad de asignación.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={isLeader}
              onChange={(e) => setIsLeader(e.target.checked)}
              data-testid="member-edit-leader"
            />
            <span>Líder del equipo</span>
          </label>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="me-role">Rol en el equipo</Label>
            <Input
              id="me-role"
              value={roleInTeam}
              onChange={(e) => setRoleInTeam(e.target.value)}
              placeholder="p. ej. Tech Lead"
              data-testid="member-edit-role"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="me-priority">Prioridad de asignación (0–1000)</Label>
            <Input
              id="me-priority"
              type="number"
              min={0}
              max={1000}
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              data-testid="member-edit-priority"
            />
          </div>
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="member-edit-error"
            >
              {mutation.error?.body ?? mutation.error?.message ?? "Error al guardar"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            disabled={!priorityValid || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                is_team_leader: isLeader,
                role_in_team: roleInTeam.trim() || null,
                assignment_priority: priorityNum,
              })
            }
            data-testid="member-edit-save"
          >
            {mutation.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
