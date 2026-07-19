"use client";

import { useEffect, useState } from "react";
import { Pause, Play, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Barra de reproducción del Replay de runs (ADR 0119).
 *
 * Componente controlado y PURO respecto a los datos: recibe `total` pasos y
 * el `index` activo, y emite `onIndexChange` — la ficha de ejecución decide
 * qué significa cada paso (con lib/office/mapping) y cómo resaltarlo. El
 * play avanza un paso cada `800/speed` ms y se detiene solo al llegar al
 * final; el scrubber salta a cualquier punto en cualquier momento.
 */
export function ReplayBar({
  total,
  index,
  onIndexChange,
}: {
  total: number;
  index: number;
  onIndexChange: (next: number) => void;
}) {
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);

  useEffect(() => {
    if (!playing) return;
    if (index >= total - 1) {
      setPlaying(false);
      return;
    }
    const timer = setInterval(() => {
      onIndexChange(Math.min(index + 1, total - 1));
    }, 800 / speed);
    return () => clearInterval(timer);
  }, [playing, speed, index, total, onIndexChange]);

  if (total === 0) return null;

  return (
    <div
      className="bg-card sticky top-2 z-10 flex items-center gap-3 rounded-xl border p-3 shadow-sm"
      data-testid="replay-bar"
    >
      <Button
        size="sm"
        variant="outline"
        data-testid="replay-play"
        onClick={() => setPlaying((p) => !p)}
        aria-label={playing ? "Pausar" : "Reproducir"}
      >
        {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
      </Button>
      <Button
        size="sm"
        variant="ghost"
        data-testid="replay-restart"
        onClick={() => {
          onIndexChange(0);
          setPlaying(true);
        }}
        aria-label="Reiniciar"
      >
        <RotateCcw className="h-4 w-4" />
      </Button>
      <input
        type="range"
        min={0}
        max={Math.max(total - 1, 0)}
        value={index}
        data-testid="replay-scrubber"
        className="accent-primary flex-1"
        onChange={(e) => onIndexChange(Number(e.target.value))}
        aria-label="Posición del replay"
      />
      <span className="text-muted-foreground w-16 text-right font-mono text-xs">
        {index + 1}/{total}
      </span>
      <select
        className="bg-background rounded-md border px-1 py-0.5 text-xs"
        value={speed}
        data-testid="replay-speed"
        onChange={(e) => setSpeed(Number(e.target.value))}
        aria-label="Velocidad"
      >
        <option value={0.5}>0.5×</option>
        <option value={1}>1×</option>
        <option value={2}>2×</option>
        <option value={4}>4×</option>
      </select>
    </div>
  );
}
