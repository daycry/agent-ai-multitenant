"use client";

/**
 * Modo voz del asistente personal (ADR 0073) — ahora una VIDEOLLAMADA a
 * pantalla completa.
 *
 * Toda la mecánica (WS por-turno, push-to-talk con pointer capture, playback
 * con AudioContext desbloqueado por gesto, voz persistida con selección
 * Mujer/Hombre por idioma, subtítulos y controles) vive en la shell
 * compartida `components/voice/voice-call-shell.tsx`; aquí solo se
 * parametriza el endpoint del asistente y su avatar.
 */

import { RealisticAvatar } from "@/components/voice/realistic-avatar";
import { VoiceCallShell } from "@/components/voice/voice-call-shell";

export function VoiceCall({ title = "Aria", onClose }: { title?: string; onClose: () => void }) {
  return (
    <VoiceCallShell
      wsPath="/ws/assistant/voice"
      title={title}
      subtitle="Asistente personal"
      storageKey="agentic.voice.assistant"
      defaultVoice="ef_dora"
      testidPrefix="voice"
      renderAvatar={({ speaking, mouthOpen, voice }) => (
        <RealisticAvatar
          speaking={speaking}
          mouthOpen={mouthOpen}
          voiceId={voice}
          testId="assistant-avatar"
        />
      )}
      onClose={onClose}
    />
  );
}
