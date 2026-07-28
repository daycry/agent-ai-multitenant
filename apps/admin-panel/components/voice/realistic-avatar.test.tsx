// @vitest-environment jsdom
// Avatar afectivo del córtex (Córtex F5 · C2) — el test de render que el plan
// exigía y nunca se escribió.
//
// Por qué existe: `RealisticAvatar` es el avatar VIVO de la videollamada
// (`components/cortex/cortex-voice-call.tsx` lo monta; `CortexAvatar` de
// components/cortex/ es un duplicado muerto que nadie importa). Sin test de
// render, nada impedía que una refactorización del SVG dejara el `affect` sin
// efecto: el frame llegaría, el estado se actualizaría y la cara seguiría
// idéntica. Estos tests fijan el ÚNICO contrato observable — el afecto tiene
// que verse en el DOM — y la degradación a neutro cuando no hay frame.
//
// AVISO SOBRE LA DUPLICACIÓN (hueco C1, defecto conocido, NO se arregla aquí):
// este componente NO llama a `avatarStyleFromAffect` (lib/cortex.ts), sino que
// reimplementa el mapeo afecto→visual inline (`auraHue` :100, sway :154). Por
// eso los asserts de abajo son de DIRECCIÓN y RANGO, no de valores exactos:
// deben seguir en verde tanto con la copia inline de hoy como cuando el avatar
// pase a usar la función pura (ambos mapeos son monótonos en la valencia y
// coinciden en la duración del sway).

import { cleanup, render } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { RealisticAvatar } from "@/components/voice/realistic-avatar";
import { avatarStyleFromAffect } from "@/lib/cortex";

afterEach(cleanup);

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
    // El acoplamiento con `avatarStyleFromAffect` se asserta SÓLO en el sway
    // porque es la única dimensión en la que la copia inline del componente y
    // la función pura de lib/cortex.ts coinciden hoy (hueco C1). Cuando C1 se
    // cierre y el avatar llame a la función pura, este assert sigue verde.
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
