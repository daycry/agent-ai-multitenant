// @vitest-environment jsdom
// Videollamada del córtex (Córtex F5 · C3) — el test vitest que el plan exigía
// y nunca se escribió: «al recibir un frame {type:'affect', valence:-0.6, ...}
// el componente actualiza el estado y renderiza el copy honesto; al recibir
// binario reproduce audio (mock)».
//
// Por qué existe: `components/voice/voice-call-shell.test.tsx` cubre el
// protocolo común (AudioContext, voz persistida, errores) pero NUNCA emite un
// frame `affect` ni un frame binario. Todo lo específico del córtex —el cableado
// del frame afectivo al avatar y el rótulo de honestidad del ADR 0075— quedaba
// sin red: se podía borrar el `onFrame` o el subtítulo y ningún test protestaba.

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/ws", () => ({
  wsUrl: (p: string) => `ws://test${p}`,
}));

import { CortexVoiceCall } from "@/components/cortex/cortex-voice-call";

// ---------------------------------------------------------------------------
// Dobles del entorno de medios (jsdom no trae WebSocket, WebAudio ni <audio>
// reproducible: HTMLMediaElement.play() está "not implemented" y devuelve
// undefined, así que sin este doble el `.catch()` de la shell revienta).
// ---------------------------------------------------------------------------
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static OPEN = 1;
  url: string;
  readyState = 1; // OPEN
  binaryType = "";
  sent: unknown[] = [];
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((e: { reason: string }) => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
  send(data: unknown) {
    this.sent.push(data);
  }
  close() {
    this.onclose?.({ reason: "" });
  }
  emit(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
  /** El TTS llega como binario puro, no como frame JSON. */
  emitAudio(buf: ArrayBuffer) {
    this.onmessage?.({ data: buf });
  }
}

class FakeAudioContext {
  state = "suspended";
  destination = {};
  async resume() {
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

class FakeAudio {
  static played: string[] = [];
  src: string;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(src: string) {
    this.src = src;
  }
  async play() {
    FakeAudio.played.push(this.src);
  }
}

function mount() {
  return render(<CortexVoiceCall onClose={() => {}} />);
}

async function connect(): Promise<FakeWebSocket> {
  fireEvent.click(screen.getByTestId("cortex-voice-connect"));
  await waitFor(() => expect(FakeWebSocket.instances.length).toBeGreaterThan(0));
  return FakeWebSocket.instances.at(-1)!;
}

/** Tono HSL del aura del avatar: el afecto hecho pixel. */
function auraHue(container: HTMLElement): number {
  const stop = container.querySelector("#va-aura stop");
  const match = /hsl\((-?[\d.]+)/.exec(stop?.getAttribute("stop-color") ?? "");
  expect(match, "no se encontró el aura del avatar").not.toBeNull();
  return Number(match![1]);
}

const AFFECT_FRAME = {
  type: "affect",
  valence: -0.6,
  arousal: 0.72,
  dominance: -0.2,
  intensity: 0.64,
  mood_label: "inquieto",
  drives: { curiosity: 0.8, bonding: 0.5, coherence: 0.3, competence: 0.6 },
};

beforeEach(() => {
  FakeWebSocket.instances = [];
  FakeAudio.played = [];
  vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
  vi.stubGlobal("AudioContext", FakeAudioContext as unknown as typeof AudioContext);
  vi.stubGlobal("Audio", FakeAudio as unknown as typeof Audio);
  URL.createObjectURL = () => "blob:cortex-test";
  URL.revokeObjectURL = () => {};
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("CortexVoiceCall — frame afectivo + copy honesto (C3)", () => {
  it("el aviso de honestidad es visible ANTES de cualquier frame y no se va", async () => {
    // ADR 0075 §6: el afecto es un modelo computacional, no sentimientos. El
    // rótulo tiene que ser PERSISTENTE — el defecto que atrapa es que sólo
    // apareciera junto a la etiqueta de mood (es decir, sólo después de que el
    // servidor mande un frame), dejando el arranque de la llamada sin aviso.
    mount();
    expect(screen.getByText(/no son sentimientos reales/i)).toBeTruthy();
    const ws = await connect();
    ws.emit({ type: "ready", voice: "ef_dora" });
    await waitFor(() =>
      expect(screen.getByTestId("cortex-voice-status").textContent).toContain("En llamada"),
    );
    expect(screen.getByText(/no son sentimientos reales/i)).toBeTruthy();
  });

  it("un frame affect con valencia negativa actualiza el estado y rotula el mood como simulado", async () => {
    const { container } = mount();
    const ws = await connect();
    ws.emit({ type: "ready", voice: "ef_dora" });
    const neutralHue = auraHue(container);

    ws.emit(AFFECT_FRAME);

    await waitFor(() => expect(screen.getByTestId("cortex-avatar-mood")).toBeTruthy());
    // El copy honesto acompaña SIEMPRE a la etiqueta: «inquieto · simulado».
    const mood = screen.getByTestId("cortex-avatar-mood").textContent ?? "";
    expect(mood).toContain("inquieto");
    expect(mood).toContain("simulado");
    // Y el estado llega de verdad al avatar: el aura abandona el neutro de
    // marca y cae en la banda de valencia negativa (rojo/ámbar).
    const sadHue = auraHue(container);
    expect(sadHue).not.toBe(neutralHue);
    expect(sadHue).toBeLessThan(60);
  });

  it("sin frame affect no hay etiqueta de mood (no se inventa un estado de ánimo)", async () => {
    mount();
    const ws = await connect();
    ws.emit({ type: "ready", voice: "ef_dora" });
    ws.emit({ type: "answer", text: "hola" });
    await waitFor(() => expect(screen.getByTestId("cortex-voice-answer")).toBeTruthy());
    expect(screen.queryByTestId("cortex-avatar-mood")).toBeNull();
  });

  it("un frame de telemetría anidado (con payload) no contamina el afecto de la llamada", async () => {
    // El WS de `/mind` manda `{type:'affect', payload:{...}}`; el de voz manda
    // los campos en raíz. Si el componente aceptara el anidado, el avatar
    // quedaría pintado con ceros (todos los campos ausentes → 0).
    mount();
    const ws = await connect();
    ws.emit({ type: "ready", voice: "ef_dora" });
    ws.emit({ type: "affect", payload: { valence: 0.9, mood_label: "eufórico" } });
    ws.emit({ type: "answer", text: "hola" });
    await waitFor(() => expect(screen.getByTestId("cortex-voice-answer")).toBeTruthy());
    expect(screen.queryByTestId("cortex-avatar-mood")).toBeNull();
  });

  it("el binario del TTS se reproduce y la llamada pasa a Hablando", async () => {
    mount();
    const ws = await connect();
    ws.emit({ type: "ready", voice: "ef_dora" });
    ws.emit({ type: "answer", text: "estoy aquí" });

    ws.emitAudio(new Uint8Array([1, 2, 3, 4]).buffer);

    await waitFor(() => expect(FakeAudio.played).toEqual(["blob:cortex-test"]));
    expect(screen.getByTestId("cortex-voice-status").textContent).toContain("Hablando");
  });

  it("conecta al WS del córtex (donde vive el gate de owner), no al del asistente", async () => {
    mount();
    const ws = await connect();
    expect(ws.url).toBe("ws://test/ws/owner/cortex/voice");
  });
});
