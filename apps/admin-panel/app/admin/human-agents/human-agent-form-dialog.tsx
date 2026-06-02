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
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { ApiError, apiFetch } from "@/lib/api";

import type { AssignableUser, HumanAgent } from "./page";

// Human-agent role labels — the human-task shapes the gallery cares about.
const ROLE_OPTIONS = [
  "reviewer",
  "security",
  "devops",
  "frontend_dev",
  "backend_dev",
  "architect",
  "technical_writer",
  "specialist",
  "custom",
];

// MVP notification channels (email, in-app, personal assistant).
const CHANNEL_OPTIONS = ["email", "in_app", "assistant"];

const CURRENCY_OPTIONS = ["EUR", "USD", "GBP"];

interface ConfigPayload {
  assigned_user_id: string | null;
  hourly_rate: string | null;
  hourly_rate_currency: string | null;
  notification_channels: string[];
  acceptance_timeout_hours: number;
  escalation_target_user_id: string | null;
  expected_response_time_hours: number | null;
  expected_execution_time_hours: number | null;
}

interface CreatePayload {
  name: string;
  description: string | null;
  role: string;
  config: ConfigPayload;
}

interface UpdatePayload {
  name: string;
  description: string | null;
  role: string;
  config: ConfigPayload;
}

function numOrNull(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

/**
 * Native <select> for picking a tenant member by user_id. The set is small
 * (the tenant's members) so a plain select beats a server-side combobox.
 */
function UserSelect({
  id,
  value,
  users,
  onChange,
  testid,
}: {
  id: string;
  value: string;
  users: AssignableUser[];
  onChange: (v: string) => void;
  testid: string;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="border-input bg-background rounded-md border px-3 py-2 text-sm"
      data-testid={testid}
    >
      <option value="">— Sin asignar —</option>
      {users.map((u) => (
        <option key={u.user_id} value={u.user_id}>
          {u.full_name ? `${u.full_name} (${u.email})` : u.email}
        </option>
      ))}
    </select>
  );
}

export function HumanAgentFormDialog({
  open,
  onOpenChange,
  editing,
  users,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  editing: HumanAgent | null;
  users: AssignableUser[];
  onSaved: () => void;
}) {
  const isEdit = editing !== null;

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [role, setRole] = useState("reviewer");
  const [assignedUserId, setAssignedUserId] = useState("");
  const [hourlyRate, setHourlyRate] = useState("");
  const [currency, setCurrency] = useState("EUR");
  const [channels, setChannels] = useState<string[]>(["email", "in_app"]);
  const [acceptanceTimeout, setAcceptanceTimeout] = useState("24");
  const [escalationUserId, setEscalationUserId] = useState("");
  const [expectedResponse, setExpectedResponse] = useState("");
  const [expectedExecution, setExpectedExecution] = useState("");

  // Hydrate the form when (re)opening, from the agent being edited or defaults.
  useEffect(() => {
    if (!open) return;
    if (editing) {
      const cfg = editing.config;
      setName(editing.name);
      setDescription(editing.description ?? "");
      setRole(editing.role);
      setAssignedUserId(cfg?.assigned_user_id ?? "");
      setHourlyRate(cfg?.hourly_rate ?? "");
      setCurrency(cfg?.hourly_rate_currency ?? "EUR");
      setChannels(cfg?.notification_channels ?? []);
      setAcceptanceTimeout(String(cfg?.acceptance_timeout_hours ?? 24));
      setEscalationUserId(cfg?.escalation_target_user_id ?? "");
      setExpectedResponse(
        cfg?.expected_response_time_hours != null ? String(cfg.expected_response_time_hours) : "",
      );
      setExpectedExecution(
        cfg?.expected_execution_time_hours != null ? String(cfg.expected_execution_time_hours) : "",
      );
    } else {
      setName("");
      setDescription("");
      setRole("reviewer");
      setAssignedUserId("");
      setHourlyRate("");
      setCurrency("EUR");
      setChannels(["email", "in_app"]);
      setAcceptanceTimeout("24");
      setEscalationUserId("");
      setExpectedResponse("");
      setExpectedExecution("");
    }
  }, [open, editing]);

  const mutation = useMutation<HumanAgent, ApiError, void>({
    mutationFn: () => {
      const config: ConfigPayload = {
        assigned_user_id: assignedUserId || null,
        hourly_rate: hourlyRate.trim() || null,
        hourly_rate_currency: hourlyRate.trim() ? currency : null,
        notification_channels: channels,
        acceptance_timeout_hours: Number(acceptanceTimeout) || 24,
        escalation_target_user_id: escalationUserId || null,
        expected_response_time_hours: numOrNull(expectedResponse),
        expected_execution_time_hours: numOrNull(expectedExecution),
      };
      const body: CreatePayload | UpdatePayload = {
        name: name.trim(),
        description: description.trim() || null,
        role,
        config,
      };
      if (isEdit && editing) {
        return apiFetch<HumanAgent>(`/human-agents/${editing.id}`, {
          method: "PUT",
          body,
        });
      }
      return apiFetch<HumanAgent>("/human-agents", { method: "POST", body });
    },
    onSuccess: onSaved,
  });

  function toggleChannel(ch: string) {
    setChannels((prev) => (prev.includes(ch) ? prev.filter((c) => c !== ch) : [...prev, ch]));
  }

  const submitDisabled = !name.trim() || mutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Editar agente humano" : "Nuevo agente humano"}</DialogTitle>
          <DialogDescription>
            Un agente humano representa a una persona (o rol) asignable a tareas del plan. La
            asignación es por usuario concreto (modo MVP).
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-4">
          {/* Identity */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ha-name">Nombre</Label>
              <Input
                id="ha-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                data-testid="ha-name"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ha-role">Rol</Label>
              <select
                id="ha-role"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="border-input bg-background rounded-md border px-3 py-2 text-sm"
                data-testid="ha-role"
              >
                {ROLE_OPTIONS.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Descripción</Label>
            <MarkdownTextarea
              value={description}
              onChange={setDescription}
              rows={2}
              data-testid="ha-description"
            />
          </div>

          {/* Assignment */}
          <fieldset className="border-border space-y-3 rounded-md border p-3">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wide">
              Asignación
            </legend>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ha-assigned-user">Usuario asignado (specific_user)</Label>
              <UserSelect
                id="ha-assigned-user"
                value={assignedUserId}
                users={users}
                onChange={setAssignedUserId}
                testid="ha-assigned-user"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ha-escalation-user">Escalación si no acepta a tiempo</Label>
              <UserSelect
                id="ha-escalation-user"
                value={escalationUserId}
                users={users}
                onChange={setEscalationUserId}
                testid="ha-escalation-user"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ha-acceptance-timeout">Timeout de aceptación (horas)</Label>
              <Input
                id="ha-acceptance-timeout"
                type="number"
                min={1}
                max={720}
                value={acceptanceTimeout}
                onChange={(e) => setAcceptanceTimeout(e.target.value)}
                data-testid="ha-acceptance-timeout"
              />
            </div>
          </fieldset>

          {/* Cost */}
          <fieldset className="border-border space-y-3 rounded-md border p-3">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wide">Coste</legend>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="ha-hourly-rate">Tarifa por hora</Label>
                <Input
                  id="ha-hourly-rate"
                  type="number"
                  min={0}
                  step="0.01"
                  value={hourlyRate}
                  onChange={(e) => setHourlyRate(e.target.value)}
                  data-testid="ha-hourly-rate"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="ha-currency">Moneda</Label>
                <select
                  id="ha-currency"
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value)}
                  className="border-input bg-background rounded-md border px-3 py-2 text-sm"
                  data-testid="ha-currency"
                >
                  {CURRENCY_OPTIONS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </fieldset>

          {/* Notifications */}
          <fieldset className="border-border space-y-2 rounded-md border p-3">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wide">
              Canales de notificación
            </legend>
            <div className="flex flex-wrap gap-3">
              {CHANNEL_OPTIONS.map((ch) => (
                <label key={ch} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={channels.includes(ch)}
                    onChange={() => toggleChannel(ch)}
                    data-testid={`ha-channel-${ch}`}
                  />
                  {ch}
                </label>
              ))}
            </div>
          </fieldset>

          {/* Planning estimates */}
          <fieldset className="border-border space-y-3 rounded-md border p-3">
            <legend className="px-1 text-xs font-semibold uppercase tracking-wide">
              Estimaciones (planning)
            </legend>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="ha-expected-response">Respuesta esperada (horas)</Label>
                <Input
                  id="ha-expected-response"
                  type="number"
                  min={0}
                  value={expectedResponse}
                  onChange={(e) => setExpectedResponse(e.target.value)}
                  data-testid="ha-expected-response"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="ha-expected-execution">Ejecución esperada (horas)</Label>
                <Input
                  id="ha-expected-execution"
                  type="number"
                  min={0}
                  value={expectedExecution}
                  onChange={(e) => setExpectedExecution(e.target.value)}
                  data-testid="ha-expected-execution"
                />
              </div>
            </div>
          </fieldset>

          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="ha-form-error"
            >
              {mutation.error?.message ?? "Error al guardar"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            disabled={submitDisabled}
            onClick={() => mutation.mutate()}
            data-testid="ha-submit"
          >
            {mutation.isPending ? "Guardando…" : isEdit ? "Guardar cambios" : "Crear"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
