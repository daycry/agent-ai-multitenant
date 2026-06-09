"use client";

import { Cpu, HardDrive, Layers, Lock, MemoryStick, Network } from "lucide-react";
import { useMemo } from "react";

import { type InstallerConfig } from "@/lib/config";
import {
  buildConfigGroups,
  buildPreview,
  type ConfigGroup,
  type ResourcePreview,
} from "@/lib/preview";
import { cn } from "@/lib/utils";

import { Checkbox } from "./fields";

interface SummaryStepProps {
  config: InstallerConfig;
  /** The confirm-gate checkbox state, owned by the wizard shell. */
  confirmed: boolean;
  onConfirmChange: (confirmed: boolean) => void;
}

/**
 * Step 7 — Resumen y confirmación (task_15_04).
 *
 * A read-only review of the captured config (steps 2-6) with EVERY secret
 * masked, plus a preview of what the install will provision (services + ports +
 * volumes + estimated RAM/disk), and a confirm gate the operator must tick
 * before the irreversible install (step 8) can start. No host access, no
 * provisioning — pure derivation from the in-memory config.
 */
export function SummaryStep({ config, confirmed, onConfirmChange }: SummaryStepProps) {
  const groups = useMemo<readonly ConfigGroup[]>(() => buildConfigGroups(config), [config]);
  const preview = useMemo<ResourcePreview>(() => buildPreview(config), [config]);

  const portServices = preview.services.filter((s) => s.port !== null);

  return (
    <section data-testid="step-summary" className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 className="text-2xl font-semibold tracking-tight">Resumen y confirmación</h2>
        <p className="text-muted-foreground max-w-prose text-sm">
          Revisa la configuración capturada y los recursos que se aprovisionarán. La instalación es
          irreversible: confirma antes de continuar. Las credenciales se muestran enmascaradas y no
          se vuelven a mostrar.
        </p>
      </header>

      {/* ----- Captured config review (secrets masked) ----- */}
      <div className="flex flex-col gap-4" data-testid="summary-config">
        <h3 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <Layers className="h-4 w-4" /> Configuración
        </h3>
        <div className="grid gap-4 sm:grid-cols-2">
          {groups.map((group) => (
            <div
              key={group.id}
              data-testid={`summary-group-${group.id}`}
              className="border-border flex flex-col gap-2 rounded-md border p-4"
            >
              <p className="text-sm font-medium">{group.title}</p>
              <dl className="flex flex-col gap-1.5">
                {group.rows.map((row, idx) => (
                  <div
                    key={`${group.id}-${idx}`}
                    className="flex items-baseline justify-between gap-3 text-sm"
                  >
                    <dt className="text-muted-foreground">{row.label}</dt>
                    <dd
                      className={cn(
                        "text-right font-mono text-xs",
                        row.secret && "inline-flex items-center gap-1",
                      )}
                      data-testid={row.secret ? `summary-secret-${group.id}-${idx}` : undefined}
                    >
                      {row.secret && <Lock className="h-3 w-3" aria-hidden />}
                      {row.value}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
      </div>

      {/* ----- Resource preview (estimate) ----- */}
      <div className="flex flex-col gap-4" data-testid="summary-preview">
        <h3 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <Cpu className="h-4 w-4" /> Recursos a aprovisionar
        </h3>

        <div className="grid gap-3 sm:grid-cols-3">
          <Estimate
            testid="estimate-services"
            icon={<Layers className="h-4 w-4" />}
            label="Servicios"
            value={String(preview.services.length)}
          />
          <Estimate
            testid="estimate-ram"
            icon={<MemoryStick className="h-4 w-4" />}
            label="RAM estimada"
            value={`~${preview.estimatedRamGib} GiB`}
          />
          <Estimate
            testid="estimate-disk"
            icon={<HardDrive className="h-4 w-4" />}
            label="Disco estimado"
            value={`~${preview.estimatedDiskGib} GiB`}
          />
        </div>

        {preview.ollamaMode !== "none" && (
          <p
            data-testid="estimate-gpu"
            className="text-muted-foreground inline-flex items-center gap-2 text-sm"
          >
            <Cpu className="h-4 w-4" />{" "}
            {preview.ollamaMode === "gpu"
              ? "Ollama en el stack con aceleración por GPU (runtime NVIDIA incluido)."
              : "Ollama en el stack (CPU) para embeddings locales."}
          </p>
        )}

        <p className="text-muted-foreground text-xs">
          Cifras orientativas calculadas a partir de la configuración; el consumo real depende de la
          carga.
        </p>

        {/* Services + ports */}
        <div className="border-border overflow-hidden rounded-md border">
          <table className="w-full text-left text-sm" data-testid="summary-services">
            <caption className="sr-only">Servicios del stack</caption>
            <thead className="bg-muted/50 text-muted-foreground text-xs uppercase">
              <tr>
                <th className="px-3 py-2 font-medium">Servicio</th>
                <th className="px-3 py-2 font-medium">Función</th>
                <th className="px-3 py-2 font-medium">Puerto</th>
              </tr>
            </thead>
            <tbody>
              {preview.services.map((svc) => (
                <tr
                  key={svc.name}
                  data-testid={`service-row-${svc.name}`}
                  className="border-border border-t"
                >
                  <td className="px-3 py-1.5 font-mono text-xs">{svc.name}</td>
                  <td className="text-muted-foreground px-3 py-1.5">{svc.role}</td>
                  <td className="px-3 py-1.5 font-mono text-xs">
                    {svc.port === null ? "—" : svc.port}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Volumes */}
        <div className="flex flex-col gap-2">
          <h4 className="flex items-center gap-2 text-sm font-semibold">
            <Network className="h-4 w-4" /> Volúmenes y rutas persistentes
          </h4>
          <ul className="flex flex-col gap-1" data-testid="summary-volumes">
            {preview.volumes.map((vol) => (
              <li
                key={vol.name}
                data-testid={`volume-row-${vol.name}`}
                className="flex items-baseline justify-between gap-3 text-sm"
              >
                <span className="font-mono text-xs">{vol.name}</span>
                <span className="text-muted-foreground text-xs">
                  {vol.purpose} · ~{vol.diskGib} GiB
                </span>
              </li>
            ))}
          </ul>
        </div>

        <p className="text-muted-foreground text-xs">
          Puertos en contenedor de {portServices.length} servicios expuestos; el acceso externo pasa
          por el proxy inverso.
        </p>
      </div>

      {/* ----- Confirm gate ----- */}
      <div className="border-border bg-muted/30 flex flex-col gap-2 rounded-md border p-4">
        <Checkbox
          id="summary-confirm"
          checked={confirmed}
          onChange={onConfirmChange}
          label="He revisado la configuración y los recursos. Entiendo que la instalación es irreversible."
        />
        <p className="text-muted-foreground text-xs">
          Al pulsar «Instalar» se generará la configuración, se aprovisionará el stack y se
          mostrarán las credenciales y unseal keys UNA sola vez.
        </p>
      </div>
    </section>
  );
}

interface EstimateProps {
  testid: string;
  icon: React.ReactNode;
  label: string;
  value: string;
}

/** A small headline metric card for the resource estimate. */
function Estimate({ testid, icon, label, value }: EstimateProps) {
  return (
    <div data-testid={testid} className="border-border flex flex-col gap-1 rounded-md border p-3">
      <span className="text-muted-foreground inline-flex items-center gap-1.5 text-xs">
        {icon}
        {label}
      </span>
      <span className="text-lg font-semibold">{value}</span>
    </div>
  );
}
