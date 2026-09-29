"use client";

/**
 * «Añadir conocimiento» en tres pasos (`task_ui_21`, plan ui-reestructuracion).
 *
 *   1. Elegir una base o crear una nueva.
 *   2. Subir documentos o pegar texto.
 *   3. Indexado en vivo, con reintento de lo que falle.
 *
 * Con `projectId`, la concesión de la base al proyecto es IMPLÍCITA: se hace al
 * subir (una base nueva nace ya concedida; una existente se concede si aún no lo
 * estaba), y nada de eso se le enseña al usuario. Nada se toca hasta el botón del
 * paso 2, así que cancelar antes no deja bases ni concesiones a medias.
 *
 * El texto pegado viaja como un `.md` por el mismo endpoint de subida: la
 * ingesta no tiene alta por texto. Una URL no se admite porque la ingesta sólo
 * recibe ficheros; no se ofrece lo que no existe.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch } from "@/lib/api";
import { CSRF_HEADER, getCsrfToken } from "@/lib/auth";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { isTerminal, type DocumentStatus, type KbDocument, type KbRef } from "./kb-types";

const STATUS_VARIANT: Record<DocumentStatus, BadgeVariant> = {
  pending: "muted",
  processing: "warning",
  indexed: "success",
  failed: "danger",
};
const STATUS_KEY = {
  pending: "docPending",
  processing: "docProcessing",
  indexed: "docIndexed",
  failed: "docFailed",
} as const;

const POLL_MS = 1500;

type Choice = { kind: "existing"; id: string } | { kind: "new" } | null;

export interface AddKnowledgeWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Con proyecto, la base elegida o creada se concede a él sin preguntar. */
  projectId?: string;
  /** Bases que el proyecto ya tiene concedidas (para no re-conceder). */
  grantedKbIds?: ReadonlySet<string>;
  onDone: () => void;
}

async function uploadFile(kbId: string, file: File): Promise<KbDocument> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
  // Multipart: no pasa por `apiFetch` (que codifica JSON). Repite a mano el
  // contrato de sesión: cookie por `credentials` + prueba CSRF (ADR 0133).
  const csrf = getCsrfToken();
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${apiUrl}/knowledge-bases/${kbId}/documents`, {
    method: "POST",
    credentials: "include",
    headers: csrf ? { [CSRF_HEADER]: csrf } : {},
    body: formData,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${file.name}: ${text || `HTTP ${response.status}`}`);
  }
  return (await response.json()) as KbDocument;
}

export function AddKnowledgeWizard({
  open,
  onOpenChange,
  projectId,
  grantedKbIds,
  onDone,
}: AddKnowledgeWizardProps) {
  const t = useT("knowledgeWizard");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [choice, setChoice] = useState<Choice>(null);
  const [newName, setNewName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [text, setText] = useState("");
  const [textTitle, setTextTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [kbId, setKbId] = useState<string | null>(null);
  const [docIds, setDocIds] = useState<string[]>([]);

  const catalogQuery = useQuery({
    queryKey: ["kb-catalog"],
    queryFn: () => apiFetch<KbRef[]>("/knowledge-bases"),
    refetchOnWindowFocus: false,
  });
  // Las bases del catálogo global son de sólo lectura y las obsoletas rechazan
  // documentos nuevos: no se ofrecen como destino.
  const targets = (catalogQuery.data ?? []).filter(
    (k) => !k.is_builtin && !k.embedding_model_stale,
  );

  const docsQuery = useQuery({
    queryKey: ["kb-documents", kbId],
    queryFn: () => apiFetch<KbDocument[]>(`/knowledge-bases/${kbId}/documents`),
    enabled: step === 3 && kbId !== null,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => {
      const seen = (query.state.data ?? []).filter((d) => docIds.includes(d.id));
      const pending = seen.length < docIds.length || seen.some((d) => !isTerminal(d.status));
      return pending ? POLL_MS : false;
    },
  });
  const tracked = docIds.map(
    (id) =>
      (docsQuery.data ?? []).find((d) => d.id === id) ??
      ({ id, title: "…", status: "pending", error_message: null } as KbDocument),
  );

  const step1Valid =
    choice?.kind === "existing" || (choice?.kind === "new" && newName.trim().length > 0);
  const hasContent = files.length > 0 || text.trim().length > 0;

  const submit = useMutation({
    mutationFn: async () => {
      let target = kbId;
      if (target === null) {
        if (choice?.kind === "existing") {
          target = choice.id;
          if (projectId && !grantedKbIds?.has(target)) {
            await apiFetch(`/knowledge-bases/${target}/projects`, {
              method: "POST",
              body: { project_id: projectId },
            });
          }
        } else {
          const created = await apiFetch<KbRef>("/knowledge-bases", {
            method: "POST",
            body: { name: newName.trim(), ...(projectId ? { project_id: projectId } : {}) },
          });
          target = created.id;
        }
        setKbId(target);
      }
      for (const file of files) {
        const created = await uploadFile(target, file);
        setDocIds((prev) => [...prev, created.id]);
        // Lo ya subido no se re-sube si el usuario reintenta tras un fallo.
        setFiles((prev) => prev.slice(1));
      }
      if (text.trim()) {
        const base = textTitle.trim() || t("pastedTextName");
        const created = await uploadFile(
          target,
          new File([text], `${base}.md`, { type: "text/markdown" }),
        );
        setDocIds((prev) => [...prev, created.id]);
        setText("");
      }
      return target;
    },
    onSuccess: () => {
      setError(null);
      setStep(3);
    },
    onError: (err) => setError(errorText(err)),
  });

  const retry = useMutation({
    mutationFn: (documentId: string) =>
      apiFetch(`/knowledge-bases/${kbId}/documents/${documentId}/reindex`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["kb-documents", kbId] }),
  });

  function finish() {
    onDone();
    onOpenChange(false);
  }

  const stepLabel = step === 1 ? t("step1") : step === 2 ? t("step2") : t("step3");

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!submit.isPending) {
          if (!next && step === 3) onDone();
          onOpenChange(next);
        }
      }}
    >
      <DialogContent data-testid="kb-wizard" data-step={step}>
        <DialogHeader>
          <DialogTitle>{t("wizardTitle")}</DialogTitle>
          <p className="text-muted-foreground text-xs" data-testid="kb-wizard-progress">
            {t("stepOf", { step, total: 3 })} · {stepLabel}
          </p>
        </DialogHeader>
        <DialogBody className="space-y-3">
          {step === 1 ? (
            <>
              {catalogQuery.isLoading ? (
                <p className="text-muted-foreground text-sm">{t("loading")}</p>
              ) : (
                <ul className="max-h-56 space-y-1 overflow-y-auto">
                  {targets.map((k) => {
                    const picked = choice?.kind === "existing" && choice.id === k.id;
                    return (
                      <li key={k.id}>
                        <button
                          type="button"
                          onClick={() => setChoice({ kind: "existing", id: k.id })}
                          aria-pressed={picked}
                          className={`w-full rounded border px-3 py-2 text-left text-sm ${
                            picked ? "border-primary bg-muted" : ""
                          }`}
                          data-testid={`kb-wizard-pick-${k.id}`}
                        >
                          {k.name}
                          {k.category ? (
                            <span className="text-muted-foreground ml-2 text-xs">
                              {k.category.name}
                            </span>
                          ) : null}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
              <button
                type="button"
                onClick={() => setChoice({ kind: "new" })}
                aria-pressed={choice?.kind === "new"}
                className={`w-full rounded border px-3 py-2 text-left text-sm ${
                  choice?.kind === "new" ? "border-primary bg-muted" : ""
                }`}
                data-testid="kb-wizard-create-new"
              >
                {t("createNew")}
              </button>
              {choice?.kind === "new" ? (
                <div>
                  <Label htmlFor="kb-wizard-new-name">{t("newName")}</Label>
                  <Input
                    id="kb-wizard-new-name"
                    data-testid="kb-wizard-new-name"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                  />
                </div>
              ) : null}
            </>
          ) : null}

          {step === 2 ? (
            <>
              <div>
                <Label htmlFor="kb-wizard-file">{t("filesLabel")}</Label>
                <Input
                  id="kb-wizard-file"
                  data-testid="kb-wizard-file"
                  type="file"
                  multiple
                  onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
                />
              </div>
              <div>
                <Label htmlFor="kb-wizard-text-title">{t("textTitleLabel")}</Label>
                <Input
                  id="kb-wizard-text-title"
                  data-testid="kb-wizard-text-title"
                  value={textTitle}
                  onChange={(e) => setTextTitle(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="kb-wizard-text">{t("textLabel")}</Label>
                <textarea
                  id="kb-wizard-text"
                  data-testid="kb-wizard-text"
                  rows={5}
                  className="border-input bg-background w-full rounded-md border px-3 py-2 text-sm"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                />
              </div>
              {error ? (
                <p className="text-destructive text-xs" data-testid="kb-wizard-error">
                  {error}
                </p>
              ) : null}
            </>
          ) : null}

          {step === 3 ? (
            <ul className="space-y-2" data-testid="kb-wizard-docs">
              {tracked.map((d) => (
                <li
                  key={d.id}
                  className="rounded border px-3 py-2 text-sm"
                  data-testid={`kb-wizard-doc-${d.id}`}
                  data-status={d.status}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0 truncate">{d.title}</span>
                    <Badge variant={STATUS_VARIANT[d.status]}>{t(STATUS_KEY[d.status])}</Badge>
                  </div>
                  {d.status === "failed" ? (
                    <div className="mt-2 space-y-1">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={retry.isPending}
                        onClick={() => retry.mutate(d.id)}
                        data-testid={`kb-wizard-retry-${d.id}`}
                      >
                        <RefreshCw className="mr-1 h-3.5 w-3.5" />
                        {t("retry")}
                      </Button>
                      {d.error_message ? (
                        <details
                          className="text-muted-foreground text-xs"
                          data-testid={`kb-wizard-error-detail-${d.id}`}
                        >
                          <summary className="cursor-pointer">{t("failureDetail")}</summary>
                          <p className="mt-1 break-all font-mono">{d.error_message}</p>
                          <p className="mt-1 font-mono">id: {d.id}</p>
                        </details>
                      ) : null}
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </DialogBody>
        <DialogFooter>
          {step === 1 ? (
            <Button onClick={() => setStep(2)} disabled={!step1Valid} data-testid="kb-wizard-next">
              {t("next")}
            </Button>
          ) : null}
          {step === 2 ? (
            <>
              <Button
                variant="outline"
                onClick={() => setStep(1)}
                disabled={submit.isPending || kbId !== null}
                data-testid="kb-wizard-back"
              >
                {t("back")}
              </Button>
              <Button
                onClick={() => submit.mutate()}
                disabled={!hasContent || submit.isPending}
                data-testid="kb-wizard-submit"
              >
                {submit.isPending ? t("uploading") : t("upload")}
              </Button>
            </>
          ) : null}
          {step === 3 ? (
            <Button onClick={finish} data-testid="kb-wizard-done">
              {t("done")}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
