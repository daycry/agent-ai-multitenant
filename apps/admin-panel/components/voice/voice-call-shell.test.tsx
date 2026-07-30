// @vitest-environment jsdom
// Videollamada de voz (shell compartida) — pins de la lógica que arregló el
// diagnóstico 2026-07-09:
//   - el AudioContext se crea y REANUDA en el click de conectar (gesto);
//   - la voz elegida (persistida) NO la pisa el frame `ready` del servidor,
//     y se re-anuncia con un frame config;
//   - sin elección previa, se adopta el default del servidor;
//   - cambiar de voz persiste en localStorage y notifica al servidor;
//   - los frames `error` y el cierre-with-reason son VISIBLES.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/ws", () => ({
  wsUrl: (p: string) => `ws://test${p}`,
}));

import { VoiceCallShell } from "@/components/voice/voice-call-shell";

// ---------------------------------------------------------------------------
// Mocks del entorno de medios (jsdom no trae WebSocket funcional ni WebAudio)
// ---------------------------------------------------------------------------
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static OPEN = 1;
  url: string;
  readyState = 1; // OPEN
  binaryType = "";
  sent: string[] = [];
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((e: { reason: string }) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.onclose?.({ reason: "" });
  }
  emit(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

class FakeAudioContext {
  static created = 0;
  static resumed = 0;
  state = "suspended";
  constructor() {
    FakeAudioContext.created += 1;
  }
  async resume() {
    FakeAudioContext.resumed += 1;
    this.state = "running";
  }
  async close() {}
  createMediaElementSource() {
    return { connect: () => ({}) };
  }
  createMediaStreamSource() {
    return { connect: () => ({}) };
  }
  createAnalyser() {
    return {
      fftSize: 0,
      frequencyBinCount: 4,
      connect: () => ({}),
      getByteFrequencyData: () => {},
    };
  }
}

function mount(extra: Partial<React.ComponentProps<typeof VoiceCallShell>> = {}) {
  return render(
    <VoiceCallShell
      wsPath="/ws/assistant/voice"
      title="Aria"
      storageKey="test.voice"
      defaultVoice="ef_dora"
      testidPrefix="voice"
      renderAvatar={() => <div data-testid="fake-avatar" />}
      onClose={() => {}}
      {...extra}
    />,
  );
}

async function connect(): Promise<FakeWebSocket> {
  fireEvent.click(screen.getByTestId("voice-connect"));
  await waitFor(() => expect(FakeWebSocket.instances.length).toBeGreaterThan(0));
  const ws = FakeWebSocket.instances.at(-1)!;
  return ws;
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  FakeAudioContext.created = 0;
  FakeAudioContext.resumed = 0;
  vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
  vi.stubGlobal("AudioContext", FakeAudioContext as unknown as typeof AudioContext);
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("VoiceCallShell — fixes del diagnóstico de voz", () => {
  it("crea y reanuda el AudioContext en el click de conectar (gesto de usuario)", async () => {
    mount();
    await connect();
    expect(FakeAudioContext.created).toBe(1);
    expect(FakeAudioContext.resumed).toBe(1);
  });

  it("la voz persistida MANDA sobre el ready del servidor y se re-anuncia", async () => {
    window.localStorage.setItem("test.voice", "em_alex");
    mount();
    const ws = await connect();
    ws.emit({ type: "ready", voice: "af_heart" });
    await waitFor(() => {
      const select = screen.getByTestId("voice-select") as HTMLSelectElement;
      expect(select.value).toBe("em_alex");
    });
    // Re-anuncia SU voz al servidor (config) en cuanto el ready la contradice.
    const configs = ws.sent.map((s) => JSON.parse(s)).filter((f) => f.type === "config");
    expect(configs.some((f) => f.voice === "em_alex")).toBe(true);
  });

  it("sin elección previa adopta el default del servidor", async () => {
    mount();
    const ws = await connect();
    ws.emit({ type: "ready", voice: "em_alex" });
    await waitFor(() => {
      const select = screen.getByTestId("voice-select") as HTMLSelectElement;
      expect(select.value).toBe("em_alex");
    });
  });

  it("cambiar de voz persiste y notifica al servidor", async () => {
    mount();
    const ws = await connect();
    ws.emit({ type: "ready", voice: "ef_dora" });
    await waitFor(() => expect(screen.getByTestId("voice-select")).toBeTruthy());
    fireEvent.change(screen.getByTestId("voice-select"), { target: { value: "em_alex" } });
    expect(window.localStorage.getItem("test.voice")).toBe("em_alex");
    const configs = ws.sent.map((s) => JSON.parse(s)).filter((f) => f.type === "config");
    expect(configs.at(-1)?.voice).toBe("em_alex");
  });

  it("un frame error es visible para el usuario", async () => {
    mount();
    const ws = await connect();
    ws.emit({ type: "ready", voice: "ef_dora" });
    ws.emit({ type: "error", detail: "voice turn failed: STT caído" });
    await waitFor(() =>
      expect(screen.getByTestId("voice-error").textContent).toContain("STT caído"),
    );
  });

  it("el cierre del socket con motivo muestra el motivo (no un corte mudo)", async () => {
    mount();
    const ws = await connect();
    ws.emit({ type: "ready", voice: "ef_dora" });
    ws.onclose?.({ reason: "assistant no habilitado para este tenant" });
    await waitFor(() =>
      expect(screen.getByTestId("voice-error").textContent).toContain("assistant no habilitado"),
    );
  });

  it("los subtítulos en vivo muestran transcript y respuesta", async () => {
    mount();
    const ws = await connect();
    ws.emit({ type: "ready", voice: "ef_dora" });
    ws.emit({ type: "transcript", text: "hola" });
    ws.emit({ type: "answer", text: "¡Hola! ¿En qué te ayudo?" });
    await waitFor(() => {
      expect(screen.getByTestId("voice-transcript").textContent).toContain("hola");
      expect(screen.getByTestId("voice-answer").textContent).toContain("¿En qué te ayudo?");
    });
  });

  it("el frame thinking (turno largo) muestra el estado Pensando", async () => {
    mount();
    const ws = await connect();
    ws.emit({ type: "ready", voice: "ef_dora" });
    ws.emit({ type: "transcript", text: "una pregunta larga" });
    ws.emit({ type: "thinking" });
    await waitFor(() =>
      expect(screen.getByTestId("voice-status").textContent).toContain("Pensando"),
    );
  });
});
