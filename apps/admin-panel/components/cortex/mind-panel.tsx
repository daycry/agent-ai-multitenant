"use client";

/**
 * `MindPanel` — el núcleo del Panel de Mente como COMPONENTE (Córtex F2, ADR 0075).
 *
 * Es lo que la casilla F2 pedía por nombre y no existía: los diales PAD, las
 * sensaciones (drives) y el aviso honesto, sin estado propio ni consultas, para
 * poder montarlo en DOS sitios y testearlo aislado:
 *
 *   - la pantalla `app/admin/cortex/mind/` (vista completa, con su curva de
 *     mood, sus episodios y el resto de tarjetas), y
 *   - la segunda columna del córtex `app/admin/cortex/page.tsx`, donde el owner
 *     ve el estado del córtex MIENTRAS conversa con él, que es cuando significa
 *     algo (antes tenía que cambiar de pantalla para saber cómo estaba).
 *
 * Dos reglas que este componente encarna, y que su test clava:
 *
 *   1. **El aviso honesto no es removible** (ADR 0075 §6). Se pinta SIEMPRE,
 *      incluso sin estado — no hay ninguna rama en la que salgan diales sin él.
 *      El texto sale de la nota bilingüe del backend (`note_es`/`note_en`) y, si
 *      viene vacía, del diccionario.
 *   2. **ES+EN** (principio rector 12). Todo el copy va por `useT`; aquí no se
 *      escribe castellano fijo. Sólo el `mood_label` se pinta crudo: es un dato
 *      del backend, no texto de UI.
 *
 * `null` es un estado legítimo, no un error: el `/mind` aún no ha respondido, o
 * el owner mira el panel desde el chat y la consulta falló. En ese caso se dice
 * que no hay estado; NUNCA se pintan diales a cero, que se leerían como «el
 * córtex está plano» en vez de como «no lo sé».
 */

import { Info } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import {
  driveToPercent,
  padToPercent,
  type CortexDrives,
  type CortexMind,
  type PadDimension,
} from "@/lib/cortex";
import { honestNote } from "@/lib/cortex-curiosity";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";

/** Las cuatro dimensiones del dial, en orden fijo. */
const PAD_DIMENSIONS: {
  key: PadDimension;
  /** Clave del diccionario (el rótulo NO se escribe aquí). */
  labelKey: "valence" | "arousal" | "dominance" | "intensity";
  testid: string;
  /** Una dimensión bipolar [-1,1] marca su punto neutro (0) en el centro. */
  bipolar: boolean;
}[] = [
  { key: "valence", labelKey: "valence", testid: "pad-valence", bipolar: true },
  { key: "arousal", labelKey: "arousal", testid: "pad-arousal", bipolar: false },
  { key: "dominance", labelKey: "dominance", testid: "pad-dominance", bipolar: true },
  { key: "intensity", labelKey: "intensity", testid: "pad-intensity", bipolar: false },
];

const DRIVE_DIMENSIONS: {
  key: keyof CortexDrives;
  labelKey: "curiosity" | "bonding" | "coherence" | "competence";
}[] = [
  { key: "curiosity", labelKey: "curiosity" },
  { key: "bonding", labelKey: "bonding" },
  { key: "coherence", labelKey: "coherence" },
  { key: "competence", labelKey: "competence" },
];

export function MindPanel({
  mind,
  /**
   * `2` reparte diales y drives en dos columnas (la pantalla completa); `1` los
   * apila (la columna estrecha del chat). Es la ÚNICA diferencia entre los dos
   * montajes: el contenido y el aviso honesto son idénticos a propósito.
   */
  columns = 2,
}: {
  mind: CortexMind | null;
  columns?: 1 | 2;
}) {
  return (
    <div className="flex flex-col gap-4" data-testid="cortex-mind-panel">
      <HonestyBanner mind={mind} />
      {mind ? (
        <div className={columns === 2 ? "grid gap-6 lg:grid-cols-2" : "flex flex-col gap-4"}>
          <PadCard mind={mind} />
          <DrivesCard mind={mind} />
        </div>
      ) : (
        <EmptyMindNote />
      )}
    </div>
  );
}

/**
 * Aviso de honestidad — NO removible (ADR 0075 §6).
 *
 * El texto preferente es el que redacta el backend en el bloque `honesty` de
 * `/mind`, que ya viaja bilingüe; `honestNote` elige la cara del idioma activo y
 * cae a la otra si la pedida viene vacía. Con las dos vacías (o sin estado
 * todavía) entra el respaldo del diccionario, para que el aviso NUNCA quede en
 * blanco mientras se pinta afecto.
 */
function HonestyBanner({ mind }: { mind: CortexMind | null }) {
  const lang = useLangOptional();
  const t = useT("cortexMind");
  const tFallback = useT("cortexCuriosity");
  const note = honestNote(mind?.honesty ?? {}, lang) || tFallback("honestyFallback");

  return (
    <div
      className="border-border bg-muted text-muted-foreground flex items-start gap-2 rounded-lg border px-4 py-3 text-sm"
      role="note"
      data-testid="cortex-mind-honesty"
    >
      <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <p>
        <span className="text-foreground font-medium">{t("honestyLabel")}</span> {note}{" "}
        {t("honestyTail")}
      </p>
    </div>
  );
}

function EmptyMindNote() {
  const t = useT("cortexMind");
  return (
    <p className="text-muted-foreground text-sm" data-testid="cortex-mind-panel-empty">
      {t("noState")}
    </p>
  );
}

function PadCard({ mind }: { mind: CortexMind }) {
  const t = useT("cortexMind");
  return (
    <Card>
      <CardContent className="space-y-4 pt-5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-muted-foreground text-xs uppercase tracking-wider">{t("padTitle")}</p>
          <span
            className="bg-primary/10 text-primary inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold"
            data-testid="mood-label"
          >
            {/* Dato del backend: se pinta crudo, no se traduce. */}
            {mind.mood_label || "—"}
          </span>
        </div>
        <div className="space-y-3">
          {PAD_DIMENSIONS.map((d) => (
            <Gauge
              key={d.key}
              label={t(d.labelKey)}
              testid={d.testid}
              value={mind[d.key]}
              percent={padToPercent(d.key, mind[d.key])}
              bipolar={d.bipolar}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function DrivesCard({ mind }: { mind: CortexMind }) {
  const t = useT("cortexMind");
  return (
    <Card>
      <CardContent className="space-y-4 pt-5">
        <p className="text-muted-foreground text-xs uppercase tracking-wider">{t("drivesTitle")}</p>
        <div className="space-y-3" data-testid="drives">
          {DRIVE_DIMENSIONS.map((d) => (
            <Gauge
              key={d.key}
              label={t(d.labelKey)}
              testid={`drive-${d.key}`}
              value={mind.drives[d.key]}
              percent={driveToPercent(mind.drives[d.key])}
              bipolar={false}
              tone="info"
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * Un dial: rótulo, valor numérico y barra.
 *
 * La barra lleva su propio `data-testid` (`{testid}-bar`) porque su ANCHURA es
 * el dato: un dial que pinta siempre lo mismo miente sobre el estado del córtex,
 * y sin un asidero estable el test sólo podría comprobar el número.
 */
function Gauge({
  label,
  testid,
  value,
  percent,
  bipolar,
  tone = "primary",
}: {
  label: string;
  testid: string;
  value: number;
  percent: number;
  bipolar: boolean;
  tone?: "primary" | "info";
}) {
  const fill = tone === "info" ? "bg-info" : "bg-primary";
  return (
    <div data-testid={testid}>
      <div className="mb-1 flex items-center justify-between text-sm">
        <span>{label}</span>
        <span className="text-muted-foreground tabular-nums">{value.toFixed(2)}</span>
      </div>
      <div className="bg-muted relative h-2.5 w-full overflow-hidden rounded-full">
        {/* Punto neutro (0) de una dimensión bipolar, marca visual al 50%. */}
        {bipolar ? (
          <span
            aria-hidden="true"
            className="bg-border absolute inset-y-0 left-1/2 w-px -translate-x-1/2"
          />
        ) : null}
        <div
          data-testid={`${testid}-bar`}
          className={`${fill} absolute inset-y-0 left-0 rounded-full transition-[width] duration-300`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}
