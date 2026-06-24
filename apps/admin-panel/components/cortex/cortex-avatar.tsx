"use client";

/**
 * Avatar reactivo al afecto del córtex (Córtex F5, ADR 0073 voz + 0075 afecto).
 *
 * Hermano del avatar del asistente (`components/assistant/avatar-face.tsx`): un
 * rostro SVG dependency-free con lip-sync por amplitud (`mouthOpen` 0..1) y un
 * parpadeo en reposo, PERO además **reacciona al estado afectivo** del córtex:
 *
 *   - el **color/tono** del rostro sigue la VALENCIA (rojo frío → verde cálido),
 *   - la **saturación + energía del "sway"** siguen la ACTIVACIÓN (arousal),
 *   - muestra la `mood_label` (etiqueta de mood SOLO-UI, bilingüe del backend).
 *
 * El mapeo afecto→estilo es la función PURA `avatarStyleFromAffect` (testeada en
 * `lib/cortex.test.ts`); aquí sólo se pinta.
 *
 * Honestidad de producto OBLIGATORIA (ADR 0075 §6): es el avatar de una mente
 * SIMULADA con afecto computacional determinista, NO sentimientos reales. El copy
 * que lo rodea (en la página) lo rotula; aquí el `mood_label` no se vende como
 * emoción real.
 */

import { useEffect, useState } from "react";

import { avatarStyleFromAffect, type CortexVoiceAffectFrame } from "@/lib/cortex";

export function CortexAvatar({
  speaking,
  mouthOpen,
  affect,
  label,
}: {
  speaking: boolean;
  mouthOpen: number; // 0 (cerrado) .. 1 (abierto)
  /** Último frame de afecto del WS de voz, o `null` (estado neutro). */
  affect: CortexVoiceAffectFrame | null;
  label?: string;
}) {
  const [blink, setBlink] = useState(false);

  // Parpadeo en reposo: cierre breve de ojos a intervalos aleatorios.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      timer = setTimeout(
        () => {
          setBlink(true);
          setTimeout(() => setBlink(false), 120);
          schedule();
        },
        2500 + Math.floor(Math.random() * 2500),
      );
    };
    schedule();
    return () => clearTimeout(timer);
  }, []);

  // Mapeo afecto→estilo (neutro si aún no hay frame: ámbar tibio, baja activación).
  const style = avatarStyleFromAffect(affect ?? { valence: 0, arousal: 0.3 });
  const faceFill = `hsl(${style.hue.toFixed(0)} ${style.saturation.toFixed(0)}% 78%)`;
  const featureFill = `hsl(${style.hue.toFixed(0)} ${Math.min(90, style.saturation + 5).toFixed(
    0,
  )}% 32%)`;

  // Apertura de boca (px en el viewBox 120x120). Clamp + hueco en reposo.
  const open = Math.max(0, Math.min(1, mouthOpen));
  const mouthRy = 2 + open * 13;
  const eyeRy = blink ? 0.6 : 5;

  // Curvatura de la boca por valencia: sonrisa con valencia positiva, mueca con
  // negativa (sólo cuando no está "hablando", para no pelear con el lip-sync).
  const valence = affect ? Math.min(1, Math.max(-1, affect.valence)) : 0;
  const smileCurve = (-valence * 7).toFixed(1); // +valence → comisuras arriba

  const moodLabel = affect?.mood_label?.trim();

  return (
    <div data-testid="cortex-avatar" className="flex flex-col items-center gap-2">
      <div
        className="rounded-full border bg-gradient-to-b from-slate-50 to-slate-100 dark:from-slate-800 dark:to-slate-900"
        style={{
          width: 160,
          height: 160,
          // Sway sólo al hablar; su velocidad sigue la activación (arousal).
          animation: speaking
            ? `cortex-avatar-sway ${style.swayDurationSec.toFixed(2)}s ease-in-out infinite`
            : undefined,
        }}
      >
        <svg
          viewBox="0 0 120 120"
          width="160"
          height="160"
          role="img"
          aria-label={moodLabel ? `Avatar del córtex (mood: ${moodLabel})` : "Avatar del córtex"}
        >
          {/* Rostro — el tono sigue la valencia, la saturación la activación. */}
          <circle
            cx="60"
            cy="58"
            r="40"
            style={{ fill: faceFill, transition: "fill 400ms linear" }}
          />
          {/* Ojos */}
          <ellipse cx="46" cy="50" rx="5" ry={eyeRy} style={{ fill: featureFill }} />
          <ellipse cx="74" cy="50" rx="5" ry={eyeRy} style={{ fill: featureFill }} />
          {/* Boca — alto = amplitud del habla; comisuras = valencia (en reposo). */}
          {speaking || open > 0.05 ? (
            <ellipse
              cx="60"
              cy="74"
              rx="13"
              ry={mouthRy}
              style={{ fill: featureFill, transition: "ry 60ms linear" }}
            />
          ) : (
            <path
              d={`M 47 74 Q 60 ${74 + Number(smileCurve)} 73 74`}
              fill="none"
              stroke={featureFill}
              strokeWidth={3}
              strokeLinecap="round"
              style={{ transition: "d 400ms linear" }}
            />
          )}
        </svg>
      </div>
      {moodLabel ? (
        <span
          className="bg-primary/10 text-primary inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold"
          data-testid="cortex-avatar-mood"
        >
          {moodLabel}
        </span>
      ) : null}
      {label ? <span className="text-muted-foreground text-xs">{label}</span> : null}
      <style jsx>{`
        @keyframes cortex-avatar-sway {
          0%,
          100% {
            transform: rotate(-2deg) translateY(0);
          }
          50% {
            transform: rotate(2deg) translateY(-2px);
          }
        }
      `}</style>
    </div>
  );
}
