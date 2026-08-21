// @vitest-environment jsdom

/**
 * La videollamada de voz, migrada al diccionario (plan prod-16, `task_prod16_04`).
 *
 * Entra con el módulo `assistant` porque es SU modo voz, aunque la shell la
 * comparta con el córtex. Lo que este fichero fija y ninguna de las dos guardas
 * podía ver: `VoiceOption.gender` era la unión de literales `"Mujer" | "Hombre"`
 * —castellano cableado en un TIPO—, así que el selector de voz decía
 * «Mujer · Dora» con el toggle en EN.
 *
 * Se afirma en el estado `lobby`, que es el único que no necesita WebSocket ni
 * micrófono; el resto de la mecánica ya está fijada en `voice-call-shell.test.tsx`.
 */

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/ws", () => ({ wsUrl: (p: string) => `ws://test${p}` }));

import { VoiceCallShell } from "@/components/voice/voice-call-shell";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

function renderIn(lang: "es" | "en") {
  window.localStorage.setItem(STORAGE_KEY, lang);
  return render(
    <LanguageProvider>
      <VoiceCallShell
        wsPath="/ws/assistant/voice"
        title="Aria"
        storageKey="test.voice"
        testidPrefix="voice"
        renderAvatar={() => <div data-testid="avatar" />}
        onClose={() => {}}
      />
    </LanguageProvider>,
  );
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("videollamada de voz en castellano", () => {
  it("rinde el estado de lobby y sus dos botones", () => {
    renderIn("es");

    expect(screen.getByTestId("voice-status").textContent).toBe("Listo para llamar");
    expect(screen.getByTestId("voice-connect").textContent).toContain("Iniciar llamada");
    expect(screen.getByTestId("voice-close").textContent).toContain("Volver");
  });
});

describe("videollamada de voz en inglés", () => {
  it("traduce el estado de lobby y sus dos botones", () => {
    renderIn("en");

    expect(screen.getByTestId("voice-status").textContent).toBe("Ready to call");
    expect(screen.getByTestId("voice-connect").textContent).toContain("Start call");
    expect(screen.getByTestId("voice-close").textContent).toContain("Back");

    expect(screen.queryByText("Listo para llamar")).toBeNull();
    expect(screen.queryByText("Iniciar llamada")).toBeNull();
  });
});
