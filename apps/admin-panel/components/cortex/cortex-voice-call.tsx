"use client";

/**
 * Modo voz del córtex del System Owner (Córtex F5, ADR 0073 + 0075) — ahora
 * una VIDEOLLAMADA a pantalla completa sobre la shell compartida.
 *
 * Igual que el asistente (`components/assistant/voice-call.tsx`) salvo tres
 * cosas: el WS del córtex, el gate de owner (lo aplica el padre al montar; la
 * barrera real es `_is_db_system_owner` en el backend) y el **frame afectivo**
 * `{type:'affect', valence, arousal, mood_label}` que el servidor emite tras
 * cada respuesta: el avatar lo mapea a aura/expresión/energía.
 *
 * Honestidad de producto (ADR 0075 §6): el afecto es un modelo computacional
 * determinista, NO sentimientos reales — el subtítulo de la llamada y la
 * etiqueta de mood lo rotulan como simulado.
 */

import { useState } from "react";

import { RealisticAvatar } from "@/components/voice/realistic-avatar";
import { VoiceCallShell } from "@/components/voice/voice-call-shell";
import { parseVoiceAffectFrame, type CortexVoiceAffectFrame } from "@/lib/cortex";

export function CortexVoiceCall({ onClose }: { onClose: () => void }) {
  // Último frame de afecto del WS; alimenta aura/expresión del avatar.
  const [affect, setAffect] = useState<CortexVoiceAffectFrame | null>(null);

  return (
    <VoiceCallShell
      wsPath="/ws/owner/cortex/voice"
      title="Córtex"
      subtitle="Afecto simulado (modelo computacional) — no son sentimientos reales"
      storageKey="agentic.voice.cortex"
      defaultVoice="ef_dora"
      testidPrefix="cortex-voice"
      onFrame={(frame) => {
        if (frame.type === "affect") {
          const parsed = parseVoiceAffectFrame(frame);
          if (parsed) setAffect(parsed);
        }
      }}
      renderAvatar={({ speaking, mouthOpen, voice }) => (
        <div className="flex flex-col items-center gap-2">
          <RealisticAvatar
            speaking={speaking}
            mouthOpen={mouthOpen}
            voiceId={voice}
            affect={
              affect
                ? {
                    valence: affect.valence,
                    arousal: affect.arousal,
                    mood_label: affect.mood_label,
                  }
                : null
            }
            testId="cortex-avatar"
          />
          {affect?.mood_label ? (
            <span
              className="inline-flex items-center rounded-full bg-white/10 px-3 py-1 text-xs font-medium text-slate-200"
              data-testid="cortex-avatar-mood"
              title="Etiqueta derivada del afecto simulado (ADR 0075)"
            >
              {affect.mood_label} · simulado
            </span>
          ) : null}
        </div>
      )}
      onClose={onClose}
    />
  );
}
