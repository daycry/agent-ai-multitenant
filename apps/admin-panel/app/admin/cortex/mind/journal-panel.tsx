"use client";

import { Card, CardContent } from "@/components/ui/card";

// ---------------------------------------------------------------------------
// Diario (C4) — línea temporal: narrativas versionadas + reflexiones/aprendizajes
// ---------------------------------------------------------------------------
export interface CortexJournalEntry {
  kind: string;
  content: string;
  reason: string | null;
  created_at: string;
}

const JOURNAL_KIND_LABEL: Record<string, string> = {
  narrative: "narrativa",
  reflection: "reflexión",
  learning: "aprendizaje",
};

export function JournalPanel({
  entries,
  isLoading,
  isError,
}: {
  entries: CortexJournalEntry[];
  isLoading: boolean;
  isError: boolean;
}) {
  return (
    <Card data-testid="cortex-journal-panel">
      <CardContent className="pt-6">
        <h2 className="text-base font-semibold">Diario</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          La línea temporal del córtex: cómo fue reescribiendo su narrativa (con el motivo de cada
          cambio) y qué reflexionó o aprendió por el camino. Relato de un modelo computacional — no
          consciencia.
        </p>
        {isLoading ? (
          <p className="text-muted-foreground mt-4 text-sm">Cargando…</p>
        ) : isError ? (
          <p className="text-destructive mt-4 text-sm">No se pudo cargar el diario.</p>
        ) : entries.length === 0 ? (
          <p className="text-muted-foreground mt-4 text-sm italic" data-testid="journal-empty">
            Aún no hay entradas: llegan con la primera reflexión (manual o programada) o el primer
            aprendizaje de curiosidad.
          </p>
        ) : (
          <ul className="mt-4 space-y-3" data-testid="journal-entries">
            {entries.map((entry, idx) => (
              <li key={idx} className="border-l-2 pl-3">
                <div className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
                  <span className="rounded-full border px-2 py-0.5">
                    {JOURNAL_KIND_LABEL[entry.kind] ?? entry.kind}
                  </span>
                  <span>{new Date(entry.created_at).toLocaleString()}</span>
                  {entry.reason ? <span className="italic">({entry.reason})</span> : null}
                </div>
                <p className="mt-1 text-sm whitespace-pre-wrap">{entry.content}</p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
