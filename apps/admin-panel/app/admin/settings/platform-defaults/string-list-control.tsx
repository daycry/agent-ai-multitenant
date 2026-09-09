"use client";

/**
 * Control del tipo `string_list` de los ajustes de plataforma (`task_mk_02`,
 * ADR 0165 D6/D7). Genérico —una lista de cadenas, una por línea— con un único
 * inquilino hoy: `egress.mcp_allowed_hosts`.
 *
 * Lo que el ADR obliga a decir aquí y no en otro sitio:
 *
 * - **El panel jamás dice «permitido».** Tras guardar dice «guardado — pendiente
 *   de aplicar al proxy» con el comando exacto al lado (D7.2). El estado
 *   «aplicado» no se deduce de haber guardado: se **comprueba** (D7.3), y para
 *   eso está el botón que pregunta al proxy host a host.
 * - El hecho global del stack, una vez: el proxy sólo abre CONNECT por 443 y
 *   8443 (D3), no caben comodines (D2) y esto no es control de exfiltración.
 *
 * Vive en su fichero y no dentro de `page.tsx` por el techo de 800 líneas que
 * vigila `check-component-size` y porque el sondeo tiene su propia query.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

/** La única clave que hoy lleva el bloque de egress (sondeo + avisos del ADR). */
export const EGRESS_ALLOWLIST_KEY = "egress.mcp_allowed_hosts";

/** El paso de aplicación que el panel no puede disparar (ADR 0060): se enseña. */
export const APPLY_COMMAND =
  'python3 scripts/egress/render_mcp_allowlist.py --filter "$COMPOSE_DIR/stack/egress-proxy/filter.txt" --hosts-json - && docker compose build egress-proxy && docker compose up -d --force-recreate egress-proxy';

type Verdict = "permitido" | "bloqueado" | "error";

interface ProbeHostResult {
  host: string;
  verdict: Verdict;
  detail: string;
}

interface ProbeResponse {
  proxy_url_configured: boolean;
  results: ProbeHostResult[];
}

const VERDICT_BADGE: Record<Verdict, BadgeVariant> = {
  permitido: "success",
  bloqueado: "warning",
  error: "muted",
};

/** `"a\n b \n\na"` → `["a", "b"]`: una por línea, sin vacías ni repetidas. */
export function parseLines(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line && !out.includes(line)) out.push(line);
  }
  return out;
}

export function StringListControl({
  settingKey,
  value,
  maxItems,
  onSave,
  pending,
  saved,
}: {
  settingKey: string;
  value: unknown;
  maxItems: number | null | undefined;
  onSave: (items: string[]) => void;
  pending: boolean;
  /** `true` justo después de un PUT en verde: enseña «pendiente de aplicar». */
  saved: boolean;
}) {
  const t = useT("platformDefaults");
  const initial = Array.isArray(value) ? (value as unknown[]).map(String) : [];
  const [text, setText] = useState(initial.join("\n"));
  const items = parseLines(text);
  const overLimit = maxItems != null && items.length > maxItems;
  const isEgress = settingKey === EGRESS_ALLOWLIST_KEY;

  return (
    <div className="space-y-2" data-testid={`string-list-${settingKey}`}>
      <textarea
        className="border-input bg-background h-32 w-full rounded-md border p-2 font-mono text-xs"
        value={text}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
        data-testid="string-list-editor"
      />
      <div className="flex items-center justify-between gap-2">
        <p className="text-muted-foreground text-xs">
          {t("stringListHelp")}
          {maxItems != null ? (
            <>
              {" "}
              <span className={overLimit ? "text-destructive" : undefined}>
                {t("stringListCount", { count: items.length, max: maxItems })}
              </span>
            </>
          ) : null}
        </p>
        <Button
          size="sm"
          onClick={() => onSave(items)}
          disabled={pending || overLimit}
          data-testid="platform-setting-save"
        >
          {pending ? t("saving") : t("save")}
        </Button>
      </div>

      {isEgress ? <EgressAllowlistNotes saved={saved} /> : null}
    </div>
  );
}

function EgressAllowlistNotes({ saved }: { saved: boolean }) {
  const t = useT("platformDefaults");
  return (
    <div className="space-y-2">
      {saved ? (
        <div
          className="bg-warning-soft text-warning-soft-foreground border-warning/30 rounded-md border p-2 text-xs"
          data-testid="egress-pending-apply"
        >
          <p>{t("stringListSavedPendingApply")}</p>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-[11px]">
            {APPLY_COMMAND}
          </pre>
        </div>
      ) : null}
      <ul className="text-muted-foreground list-disc space-y-0.5 pl-4 text-xs">
        <li>{t("egressConnectPorts")}</li>
        <li>{t("egressNoWildcards")}</li>
        <li>{t("egressNotExfiltration")}</li>
      </ul>
      <EgressProbe />
    </div>
  );
}

/** D7.3: la única afirmación verdadera por construcción — preguntarle al proxy. */
function EgressProbe() {
  const t = useT("platformDefaults");
  const errorText = useErrorText();
  const probe = useMutation<ProbeResponse, ApiError>({
    mutationFn: () =>
      apiFetch<ProbeResponse>("/admin/egress/mcp-allowlist/probe", {
        method: "POST",
        body: {},
      }),
  });
  const verdictLabel: Record<Verdict, string> = {
    permitido: t("verdictPermitido"),
    bloqueado: t("verdictBloqueado"),
    error: t("verdictError"),
  };

  return (
    <div className="space-y-2" data-testid="egress-probe">
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => probe.mutate()}
          disabled={probe.isPending}
          data-testid="egress-probe-button"
        >
          {probe.isPending ? t("probing") : t("probeButton")}
        </Button>
        <p className="text-muted-foreground text-xs">{t("probeHelp")}</p>
      </div>
      {probe.isError ? (
        <p className="text-destructive text-xs" data-testid="egress-probe-error">
          {errorText(probe.error)}
        </p>
      ) : null}
      {probe.data ? (
        !probe.data.proxy_url_configured ? (
          <p className="text-destructive text-xs" data-testid="egress-probe-no-proxy">
            {t("probeNoProxy")}
          </p>
        ) : probe.data.results.length === 0 ? (
          <p className="text-muted-foreground text-xs" data-testid="egress-probe-empty">
            {t("probeEmpty")}
          </p>
        ) : (
          <ul className="space-y-1" data-testid="egress-probe-results">
            {probe.data.results.map((r) => (
              <li
                key={r.host}
                className="flex items-start gap-2 text-xs"
                data-testid={`egress-probe-${r.host}`}
              >
                <Badge variant={VERDICT_BADGE[r.verdict]}>{verdictLabel[r.verdict]}</Badge>
                <span className="font-mono">{r.host}</span>
                <span className="text-muted-foreground">{r.detail}</span>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}
