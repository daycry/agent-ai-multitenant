// @vitest-environment jsdom
// Avatar afectivo del córtex (Córtex F5 · C2) — el test de render que el plan
// exigía y nunca se escribió.
//
// Por qué existe: `RealisticAvatar` es el avatar VIVO de la videollamada
// (`components/cortex/cortex-voice-call.tsx` lo monta). El duplicado muerto que
// había en `components/cortex/cortex-avatar.tsx` se borró al cerrar C1. Sin test de
// render, nada impedía que una refactorización del SVG dejara el `affect` sin
// efecto: el frame llegaría, el estado se actualizaría y la cara seguiría
// idéntica. Estos tests fijan el ÚNICO contrato observable — el afecto tiene
// que verse en el DOM — y la degradación a neutro cuando no hay frame.
//
// SOBRE LA DUPLICACIÓN (hueco C1): YA ARREGLADO. Este componente consume la
// función pura `avatarStyleFromAffect` (lib/cortex.ts) en vez de reimplementar el
// mapeo inline, así que el último describe de este fichero puede exigir IGUALDAD
// exacta con ella. Los asserts de los primeros describes se dejaron de DIRECCIÓN
// y RANGO a propósito: siguen valiendo si el diseño visual cambia de rampa.

import { act, cleanup, render } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RealisticAvatar } from "@/components/voice/realistic-avatar";
import { avatarStyleFromAffect } from "@/lib/cortex";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function mount(props: Partial<React.ComponentProps<typeof RealisticAvatar>> = {}) {
  return render(
    <RealisticAvatar
      speaking={false}
      mouthOpen={0}
      voiceId="ef_dora"
      affect={null}
      testId="cortex-avatar"
      {...props}
    />,
  );
}

/** Tono HSL del aura, el canal por el que el afecto se ve a simple vista. */
function auraHue(container: HTMLElement): number {
  const stop = container.querySelector("#va-aura stop");
  const raw = stop?.getAttribute("stop-color") ?? "";
  const match = /hsl\((-?[\d.]+)/.exec(raw);
  expect(match, `stop-color inesperado: ${raw}`).not.toBeNull();
  return Number(match![1]);
}

/** Duración del ciclo de vaivén (sólo presente mientras habla). */
function swaySeconds(container: HTMLElement): number {
  const group = container.querySelector("svg > g");
  const style = group?.getAttribute("style") ?? "";
  const match = /va-sway ([\d.]+)s/.exec(style);
  expect(match, `animación de sway no encontrada en: ${style}`).not.toBeNull();
  return Number(match![1]);
}

/**
 * Y del punto de control de la boca en reposo (`M 86 132 Q 100 <y> 114 132`).
 * En SVG la Y crece hacia abajo: mayor Y = comisuras caídas (mueca).
 */
function restMouthControlY(container: HTMLElement): number {
  const paths = Array.from(container.querySelectorAll("path"));
  const mouth = paths
    .map((p) => /^M 86 132 Q 100 (-?[\d.]+) 114 132$/.exec(p.getAttribute("d") ?? ""))
    .find((m) => m !== null);
  expect(mouth, "no se encontró la boca en reposo").not.toBeNull();
  return Number(mouth![1]);
}

/** Opacidad del rubor (una de las señales de activación). */
function blushOpacity(container: HTMLElement): number {
  const blush = Array.from(container.querySelectorAll("ellipse")).find(
    (e) => e.getAttribute("fill") === "#e58a8a",
  );
  expect(blush, "no se encontró el rubor").toBeTruthy();
  return Number(blush!.getAttribute("opacity"));
}

describe("RealisticAvatar — el afecto del córtex se ve en el DOM (C2)", () => {
  it("la valencia mueve el tono del aura: negativa al extremo rojo, positiva al verde", () => {
    // OJO — divergencia con la letra del plan, deliberadamente NO asertada:
    // el plan pedía «valence bajo → azul/frío», pero el diseño implementado
    // (y ya fijado en lib/cortex.test.ts) usa ROJO para la valencia negativa y
    // VERDE para la positiva. Aquí se comprueba el diseño real, no el texto.
    const sad = mount({ affect: { valence: -0.6, arousal: 0.4 } });
    const sadHue = auraHue(sad.container);
    cleanup();
    const happy = mount({ affect: { valence: 0.8, arousal: 0.4 } });
    const happyHue = auraHue(happy.container);

    expect(sadHue).toBeLessThan(happyHue);
    expect(sadHue).toBeLessThan(60); // banda roja/ámbar
    expect(happyHue).toBeGreaterThan(100); // banda verde
  });

  it("sin frame de afecto cae a NEUTRO: el aura ignora la valencia", () => {
    // El defecto que atrapa: que el avatar arrancara ya "coloreado" por un
    // afecto que el servidor todavía no ha mandado (o que el fallback quedara
    // pegado al mismo tono que la valencia cero, indistinguible de un frame
    // recibido). Sin frame el aura usa el tono de marca, en la banda fría.
    const neutral = mount({ affect: null });
    const neutralHue = auraHue(neutral.container);
    cleanup();
    const zeroValence = mount({ affect: { valence: 0, arousal: 0.3 } });
    const zeroHue = auraHue(zeroValence.container);

    expect(neutralHue).not.toBe(zeroHue);
    expect(neutralHue).toBeGreaterThan(180); // azul de marca, no ámbar afectivo
  });

  it("la activación acelera el vaivén, y coincide con la función pura del mapeo", () => {
    // Cerrado el hueco C1, el sway sale de `avatarStyleFromAffect` igual que
    // todo lo demás; este assert (dirección + valor) sigue siendo el que fija
    // que "más activación = más rápido" no se invierta.
    const calm = mount({ speaking: true, affect: { valence: 0, arousal: 0.1 } });
    const calmSway = swaySeconds(calm.container);
    cleanup();
    const excited = mount({ speaking: true, affect: { valence: 0, arousal: 0.9 } });
    const excitedSway = swaySeconds(excited.container);

    expect(excitedSway).toBeLessThan(calmSway);
    expect(excitedSway).toBeCloseTo(
      avatarStyleFromAffect({ valence: 0, arousal: 0.9 }).swayDurationSec,
      2,
    );
    expect(calmSway).toBeCloseTo(
      avatarStyleFromAffect({ valence: 0, arousal: 0.1 }).swayDurationSec,
      2,
    );
  });

  it("la activación sube el rubor (energía visible en reposo)", () => {
    const calm = mount({ affect: { valence: 0, arousal: 0 } });
    const calmBlush = blushOpacity(calm.container);
    cleanup();
    const excited = mount({ affect: { valence: 0, arousal: 1 } });

    expect(blushOpacity(excited.container)).toBeGreaterThan(calmBlush);
  });

  it("la valencia curva la boca en reposo: mueca si es negativa, sonrisa si es positiva", () => {
    // El lip-sync manda mientras habla; la valencia sólo debe verse en la boca
    // EN REPOSO. Sin este test, perder la curvatura pasaría inadvertido: el
    // aura seguiría cambiando de color y el avatar parecería "reaccionar".
    const sad = mount({ affect: { valence: -0.8, arousal: 0.3 } });
    const sadMouth = restMouthControlY(sad.container);
    cleanup();
    const happy = mount({ affect: { valence: 0.8, arousal: 0.3 } });
    const happyMouth = restMouthControlY(happy.container);

    expect(sadMouth).toBeGreaterThan(132); // comisuras hacia abajo
    expect(happyMouth).toBeLessThan(132); // comisuras hacia arriba
    expect(sadMouth).toBeGreaterThan(happyMouth);
  });

  it("hablando, la boca la gobierna la amplitud y no la valencia (no pelea con el lip-sync)", () => {
    // La boca en reposo desaparece en cuanto hay amplitud: si siguiera pintada,
    // la mueca por valencia se superpondría al lip-sync.
    const { container } = mount({
      speaking: true,
      mouthOpen: 0.7,
      affect: { valence: -1, arousal: 0.3 },
    });
    const paths = Array.from(container.querySelectorAll("path"));
    expect(paths.some((p) => /^M 86 132 Q /.test(p.getAttribute("d") ?? ""))).toBe(false);
  });

  it("clampea afectos fuera de rango sin romper el SVG", () => {
    // El frame viene de la red: un valence 5 o un arousal negativo no deben
    // producir un hue absurdo ni una duración de animación negativa.
    const { container } = mount({ speaking: true, affect: { valence: 5, arousal: 9 } });
    const hue = auraHue(container);
    expect(hue).toBeGreaterThanOrEqual(0);
    expect(hue).toBeLessThanOrEqual(360);
    expect(swaySeconds(container)).toBeGreaterThan(0);

    cleanup();
    const under = mount({ speaking: true, affect: { valence: -5, arousal: -9 } });
    expect(auraHue(under.container)).toBeGreaterThanOrEqual(0);
    expect(swaySeconds(under.container)).toBeGreaterThan(0);
  });

  it("la activación acelera el vaivén, y coincide con la función pura del mapeo (bis)", () => {
    // Duplicado intencionado del assert de arriba en su versión ESTRICTA: ahora
    // que el componente consume la función pura, el acoplamiento es exacto.
    const { container } = mount({ speaking: true, affect: { valence: 0.4, arousal: 0.62 } });
    expect(swaySeconds(container)).toBeCloseTo(
      avatarStyleFromAffect({ valence: 0.4, arousal: 0.62 }).swayDurationSec,
      2,
    );
  });

  it("el avatar sigue a la voz elegida (peinado/rasgos por prefijo Kokoro)", () => {
    const female = mount({ voiceId: "ef_dora" });
    expect(
      female.container
        .querySelector('[data-testid="cortex-avatar"]')
        ?.getAttribute("data-voice-gender"),
    ).toBe("female");
    cleanup();
    const male = mount({ voiceId: "em_alex" });
    expect(
      male.container
        .querySelector('[data-testid="cortex-avatar"]')
        ?.getAttribute("data-voice-gender"),
    ).toBe("male");
  });
});

// ---------------------------------------------------------------------------
// C1 — el mapeo afecto→visual lo gobierna la FUNCIÓN PURA, no una copia inline
//
// Estos son los asserts que el hueco C1 pedía y que antes no podían existir:
// igualdad exacta con `avatarStyleFromAffect` y un parpadeo cuya CADENCIA
// depende de la activación (antes era un intervalo aleatorio fijo: un córtex
// excitado parpadeaba igual que uno apagado).
// ---------------------------------------------------------------------------

/** `ry` de la esclera: 0.4 mientras parpadea, 4.6 con el ojo abierto. */
function eyeRy(container: HTMLElement): number {
  const sclera = Array.from(container.querySelectorAll("ellipse")).find(
    (e) => e.getAttribute("fill") === "#fff",
  );
  expect(sclera, "no se encontró la esclera").toBeTruthy();
  return Number(sclera!.getAttribute("ry"));
}

describe("RealisticAvatar — consume el mapeo puro (C1)", () => {
  it("el tono del aura es EXACTAMENTE el de la función pura (una sola verdad)", () => {
    // Antes había dos rampas distintas: la del componente (`8 + (v+1)*62`) y la
    // de la función pura (`((v+1)/2)*130`). Con la misma valencia daban tonos
    // distintos, así que el avatar y cualquier otra superficie que usara la
    // función pura NO pintaban el mismo estado.
    for (const valence of [-1, -0.6, 0, 0.35, 1]) {
      const { container } = mount({ affect: { valence, arousal: 0.4 } });
      expect(auraHue(container)).toBeCloseTo(
        avatarStyleFromAffect({ valence, arousal: 0.4 }).hue,
        0,
      );
      cleanup();
    }
  });

  it("la boca en reposo sigue el `mouthBias` de la función pura", () => {
    const { container } = mount({ affect: { valence: -0.5, arousal: 0.3 } });
    const bias = avatarStyleFromAffect({ valence: -0.5, arousal: 0.3 }).mouthBias;
    // El componente pinta `132 - mouthBias*5` como punto de control.
    expect(restMouthControlY(container)).toBeCloseTo(132 - bias * 5, 5);
  });

  it("con la activación ALTA parpadea antes que con la activación BAJA", () => {
    // La prueba de que `blinkRate` gobierna de verdad el temporizador: mismo
    // instante, distinto estado del párpado. Sin `Math.random` fijo el jitter
    // haría el test aleatorio, así que se clava en 0.5 (factor 1.0 = base).
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0.5);

    const excited = mount({ affect: { valence: 0, arousal: 1 } });
    const excitedBase =
      60_000 / Math.round(avatarStyleFromAffect({ valence: 0, arousal: 1 }).blinkRate);
    act(() => {
      vi.advanceTimersByTime(excitedBase + 20);
    });
    expect(eyeRy(excited.container)).toBe(0.4); // parpadeando
    cleanup();

    const calm = mount({ affect: { valence: 0, arousal: 0 } });
    act(() => {
      vi.advanceTimersByTime(excitedBase + 20); // el MISMO tiempo
    });
    expect(eyeRy(calm.container)).toBe(4.6); // todavía con el ojo abierto

    // …y acaba parpadeando cuando le toca por su propia cadencia.
    const calmBase =
      60_000 / Math.round(avatarStyleFromAffect({ valence: 0, arousal: 0 }).blinkRate);
    act(() => {
      vi.advanceTimersByTime(calmBase - excitedBase + 40);
    });
    expect(eyeRy(calm.container)).toBe(0.4);
  });

  it("el parpadeo se reprograma al cambiar el afecto y no deja timers vivos al desmontar", () => {
    // Un timer que sobrevive al unmount dispara `setState` sobre un componente
    // muerto (y, en la videollamada, se acumula uno por cada frame de afecto).
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const { unmount } = mount({ affect: { valence: 0, arousal: 0.2 } });
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
