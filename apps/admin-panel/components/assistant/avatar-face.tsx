"use client";

/**
 * Lightweight animated avatar for the voice mode (ADR 0073, voz F3 — MVP).
 *
 * A dependency-free SVG face whose mouth opens with the assistant's speech
 * amplitude (real lip-sync, the ADR's MVP approach), with idle blinking and a
 * subtle head sway while speaking — the "videollamada" feel without a heavy 3D
 * engine, GLB asset or GPU. A photorealistic 3D talking head (TalkingHead.js)
 * is a documented opt-in upgrade.
 *
 * Controlled + presentational: the parent feeds `speaking` + `mouthOpen` (0..1,
 * derived from a Web Audio analyser on the TTS audio).
 */

import { useEffect, useState } from "react";

export function AvatarFace({
  speaking,
  mouthOpen,
  label,
}: {
  speaking: boolean;
  mouthOpen: number; // 0 (closed) .. 1 (wide)
  label?: string;
}) {
  const [blink, setBlink] = useState(false);

  // Idle blink: brief eye close at randomised intervals.
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

  // Mouth opening height (px in the 120x120 viewBox). Clamp + a resting gap.
  const open = Math.max(0, Math.min(1, mouthOpen));
  const mouthRy = 2 + open * 13;
  const eyeRy = blink ? 0.6 : 5;

  return (
    <div
      data-testid="assistant-avatar"
      className="flex flex-col items-center gap-2"
      aria-hidden="true"
    >
      <div
        className="rounded-full border bg-gradient-to-b from-indigo-50 to-indigo-100 dark:from-slate-800 dark:to-slate-900"
        style={{
          width: 160,
          height: 160,
          // Subtle head sway only while speaking.
          animation: speaking ? "assistant-avatar-sway 2.8s ease-in-out infinite" : undefined,
        }}
      >
        <svg
          viewBox="0 0 120 120"
          width="160"
          height="160"
          role="img"
          aria-label={label ?? "Avatar"}
        >
          {/* Face */}
          <circle cx="60" cy="58" r="40" className="fill-indigo-200 dark:fill-slate-700" />
          {/* Eyes */}
          <ellipse
            cx="46"
            cy="50"
            rx="5"
            ry={eyeRy}
            className="fill-slate-700 dark:fill-slate-200"
          />
          <ellipse
            cx="74"
            cy="50"
            rx="5"
            ry={eyeRy}
            className="fill-slate-700 dark:fill-slate-200"
          />
          {/* Mouth — height tracks speech amplitude */}
          <ellipse
            cx="60"
            cy="74"
            rx="13"
            ry={mouthRy}
            className="fill-slate-700 dark:fill-slate-200"
            style={{ transition: "ry 60ms linear" }}
          />
        </svg>
      </div>
      {label ? <span className="text-muted-foreground text-xs">{label}</span> : null}
      <style jsx>{`
        @keyframes assistant-avatar-sway {
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
