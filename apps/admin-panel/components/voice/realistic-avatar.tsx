"use client";

/**
 * Avatar v2 del modo voz — un rostro SVG "vivo", sin dependencias.
 *
 * Sustituye al círculo-con-boca del MVP por una cara con pelo, cejas, ojos con
 * iris/brillo y párpados, nariz, labios con dientes y rubor: lo bastante
 * expresiva para que la videollamada se sienta real sin motor 3D ni GPU.
 *
 *   - **Lip-sync real**: `mouthOpen` (0..1, del analyser de Web Audio) abre la
 *     boca y estrecha su anchura (vocal abierta) con dientes visibles.
 *   - **Vida en reposo**: parpadeo aleatorio, sacadas del iris (mirada que se
 *     mueve sutilmente), respiración (escala) y vaivén al hablar.
 *   - **La cara sigue a la VOZ elegida**: el prefijo Kokoro (xf_/xm_) decide el
 *     peinado y los rasgos (femenino/masculino) — cambiar de voz cambia a la
 *     persona que te habla.
 *   - **Afecto opcional (córtex, ADR 0075)**: valence→tono del aura y sonrisa,
 *     arousal→energía (cejas, rubor, velocidad del sway). SIEMPRE rotulado por
 *     el copy del contenedor como afecto SIMULADO, no sentimientos.
 */

import { useEffect, useState } from "react";

export type AvatarAffect = {
  valence: number; // -1..1
  arousal: number; // 0..1
  mood_label?: string;
};

function isFemaleVoice(voiceId: string): boolean {
  // Convención Kokoro: segunda letra del prefijo = género (af_/bf_/ef_ = F).
  return /^[a-z]f_/i.test(voiceId);
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function RealisticAvatar({
  speaking,
  mouthOpen,
  voiceId,
  affect = null,
  size = 224,
  testId = "voice-avatar",
}: {
  speaking: boolean;
  mouthOpen: number; // 0..1 (amplitud del habla)
  voiceId: string;
  affect?: AvatarAffect | null;
  size?: number;
  testId?: string;
}) {
  const [blink, setBlink] = useState(false);
  const [gaze, setGaze] = useState(0); // sacadas: desplazamiento x del iris

  // Parpadeo aleatorio (con doble parpadeo ocasional, como una persona).
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      timer = setTimeout(
        () => {
          setBlink(true);
          setTimeout(() => setBlink(false), 110);
          if (Math.random() < 0.25) {
            setTimeout(() => setBlink(true), 250);
            setTimeout(() => setBlink(false), 360);
          }
          schedule();
        },
        2200 + Math.floor(Math.random() * 3000),
      );
    };
    schedule();
    return () => clearTimeout(timer);
  }, []);

  // Sacadas del iris: la mirada se desplaza sutilmente y vuelve al centro.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => {
      timer = setTimeout(
        () => {
          setGaze(Math.random() * 3.2 - 1.6);
          setTimeout(() => setGaze(0), 900 + Math.random() * 900);
          schedule();
        },
        3000 + Math.floor(Math.random() * 4000),
      );
    };
    schedule();
    return () => clearTimeout(timer);
  }, []);

  const female = isFemaleVoice(voiceId);
  const open = clamp(mouthOpen, 0, 1);
  const valence = clamp(affect?.valence ?? 0, -1, 1);
  const arousal = clamp(affect?.arousal ?? 0.3, 0, 1);

  // Aura: neutra (marca) sin afecto; con afecto, el tono sigue la valencia
  // (fría → cálida) y la opacidad la activación.
  const auraHue = affect ? 8 + (valence + 1) * 62 : 226; // -1→8 (rojizo), +1→132 (verde)
  const auraOpacity = 0.32 + arousal * 0.3 + (speaking ? 0.14 : 0);

  // Boca: alto por amplitud; el ancho se estrecha al abrir (forma de vocal).
  const mouthH = 1.6 + open * 15;
  const mouthW = 15 - open * 4.5;
  const smile = speaking || open > 0.06 ? 0 : -valence * 5; // comisuras en reposo
  const browLift = arousal * 2.6 + (speaking ? 1.2 : 0); // cejas con la energía
  const eyeOpen = blink ? 0.4 : 4.6;

  const skinTop = female ? "#ffe3d0" : "#f2cdae";
  const skinBottom = female ? "#f5c3a6" : "#dcae8a";
  const hairColor = female ? "#5b4232" : "#3d3244";
  const lipColor = female ? "#c96d6d" : "#a86868";

  return (
    <div
      data-testid={testId}
      data-voice-gender={female ? "female" : "male"}
      aria-hidden="true"
      className="relative flex flex-col items-center"
      style={{ width: size, height: size }}
    >
      <svg viewBox="0 0 200 200" width={size} height={size} role="img" aria-label="Avatar">
        <defs>
          <radialGradient id="va-aura" cx="50%" cy="45%" r="55%">
            <stop
              offset="0%"
              stopColor={`hsl(${auraHue.toFixed(0)} 85% 62%)`}
              stopOpacity={auraOpacity}
            />
            <stop offset="100%" stopColor={`hsl(${auraHue.toFixed(0)} 85% 62%)`} stopOpacity="0" />
          </radialGradient>
          <linearGradient id="va-skin" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={skinTop} />
            <stop offset="100%" stopColor={skinBottom} />
          </linearGradient>
          <clipPath id="va-mouth-clip">
            <ellipse cx="100" cy="132" rx={mouthW} ry={mouthH} />
          </clipPath>
        </defs>

        {/* Aura reactiva (marca o afecto) */}
        <circle cx="100" cy="96" r="92" fill="url(#va-aura)">
          {speaking ? (
            <animate attributeName="r" values="88;95;88" dur="1.6s" repeatCount="indefinite" />
          ) : null}
        </circle>

        {/* Grupo animado: respiración siempre; vaivén al hablar */}
        <g
          style={{
            transformOrigin: "100px 120px",
            animation: speaking
              ? `va-sway ${(3.4 - arousal * 1.6).toFixed(2)}s ease-in-out infinite`
              : "va-breathe 4.6s ease-in-out infinite",
          }}
        >
          {/* Cuello + hombros */}
          <rect x="88" y="146" width="24" height="22" rx="8" fill={skinBottom} />
          <path
            d="M 48 196 Q 100 158 152 196 L 152 200 L 48 200 Z"
            fill={female ? "#7c6dbb" : "#4c6a92"}
          />

          {/* Pelo trasero (solo femenino: melena) */}
          {female ? (
            <path
              d="M 52 96 Q 48 34 100 32 Q 152 34 148 96 Q 150 140 138 152 L 128 140 L 72 140 L 62 152 Q 50 140 52 96 Z"
              fill={hairColor}
            />
          ) : null}

          {/* Orejas */}
          <ellipse cx="55" cy="102" rx="7" ry="10" fill={skinBottom} />
          <ellipse cx="145" cy="102" rx="7" ry="10" fill={skinBottom} />

          {/* Cara */}
          <ellipse cx="100" cy="100" rx="44" ry="50" fill="url(#va-skin)" />
          {/* Sombra de mandíbula */}
          <path
            d="M 62 116 Q 100 152 138 116 Q 128 142 100 146 Q 72 142 62 116 Z"
            fill="#00000010"
          />

          {/* Pelo delantero */}
          {female ? (
            <path
              d="M 56 92 Q 54 40 100 38 Q 146 40 144 92 Q 132 66 116 60 Q 124 74 118 84 Q 104 60 84 62 Q 66 66 60 92 Z"
              fill={hairColor}
            />
          ) : (
            <path
              d="M 57 88 Q 56 44 100 40 Q 144 44 143 88 Q 138 62 118 56 Q 100 50 82 56 Q 62 62 57 88 Z"
              fill={hairColor}
            />
          )}

          {/* Cejas (suben con la energía) */}
          <path
            d={`M 70 ${78 - browLift} Q 79 ${74 - browLift} 88 ${78 - browLift}`}
            stroke={hairColor}
            strokeWidth="3.4"
            strokeLinecap="round"
            fill="none"
            style={{ transition: "d 240ms ease" }}
          />
          <path
            d={`M 112 ${78 - browLift} Q 121 ${74 - browLift} 130 ${78 - browLift}`}
            stroke={hairColor}
            strokeWidth="3.4"
            strokeLinecap="round"
            fill="none"
            style={{ transition: "d 240ms ease" }}
          />

          {/* Ojos: esclera + iris con sacadas + pupila + brillo + párpado */}
          {[79, 121].map((cx) => (
            <g key={cx}>
              <ellipse cx={cx} cy="92" rx="8.6" ry={eyeOpen} fill="#fff" />
              {!blink ? (
                <>
                  <circle cx={cx + gaze} cy="92" r="3.9" fill={female ? "#5a6f4e" : "#4e5a6f"} />
                  <circle cx={cx + gaze} cy="92" r="1.9" fill="#1d1d24" />
                  <circle cx={cx + gaze + 1.2} cy="90.8" r="0.9" fill="#ffffffd8" />
                </>
              ) : null}
              <path
                d={`M ${cx - 9} 88.5 Q ${cx} 85.5 ${cx + 9} 88.5`}
                stroke="#00000022"
                strokeWidth="1.4"
                fill="none"
              />
            </g>
          ))}

          {/* Pestañas (femenino) */}
          {female && !blink ? (
            <>
              <path
                d="M 70 88 L 67 85.6"
                stroke={hairColor}
                strokeWidth="1.4"
                strokeLinecap="round"
              />
              <path
                d="M 112.5 88 L 110 85.6"
                stroke={hairColor}
                strokeWidth="1.4"
                strokeLinecap="round"
              />
            </>
          ) : null}

          {/* Nariz */}
          <path
            d="M 100 100 Q 97 110 95 114 Q 100 118 105 114"
            stroke="#00000026"
            strokeWidth="2"
            fill="none"
            strokeLinecap="round"
          />

          {/* Rubor (crece con la activación) */}
          <ellipse
            cx="70"
            cy="112"
            rx="7.5"
            ry="4"
            fill="#e58a8a"
            opacity={0.14 + arousal * 0.24}
          />
          <ellipse
            cx="130"
            cy="112"
            rx="7.5"
            ry="4"
            fill="#e58a8a"
            opacity={0.14 + arousal * 0.24}
          />

          {/* Boca: interior + dientes + lengua (clip) y labios */}
          {open > 0.04 ? (
            <g>
              <ellipse cx="100" cy="132" rx={mouthW} ry={mouthH} fill="#5d2430" />
              <g clipPath="url(#va-mouth-clip)">
                <rect
                  x={100 - mouthW}
                  y={132 - mouthH}
                  width={mouthW * 2}
                  height="4.6"
                  fill="#fbf6ef"
                  rx="2"
                />
                <ellipse
                  cx="100"
                  cy={132 + mouthH}
                  rx={mouthW * 0.62}
                  ry={mouthH * 0.5}
                  fill="#b0524f"
                />
              </g>
              <ellipse
                cx="100"
                cy="132"
                rx={mouthW}
                ry={mouthH}
                fill="none"
                stroke={lipColor}
                strokeWidth="2.6"
              />
            </g>
          ) : (
            <path
              d={`M 86 132 Q 100 ${132 + smile} 114 132`}
              stroke={lipColor}
              strokeWidth="3.6"
              strokeLinecap="round"
              fill="none"
              style={{ transition: "d 320ms ease" }}
            />
          )}

          {/* Barba corta sutil (masculino) */}
          {!female ? (
            <path
              d="M 66 118 Q 100 156 134 118 Q 130 146 100 150 Q 70 146 66 118 Z"
              fill={hairColor}
              opacity="0.13"
            />
          ) : null}
        </g>
      </svg>

      <style jsx>{`
        @keyframes va-sway {
          0%,
          100% {
            transform: rotate(-1.6deg) translateY(0);
          }
          50% {
            transform: rotate(1.6deg) translateY(-2px);
          }
        }
        @keyframes va-breathe {
          0%,
          100% {
            transform: scale(1) translateY(0);
          }
          50% {
            transform: scale(1.012) translateY(-1.4px);
          }
        }
      `}</style>
    </div>
  );
}
