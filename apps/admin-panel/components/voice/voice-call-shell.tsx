"use client";

/**
 * Videollamada de voz — la experiencia compartida del asistente y del córtex.
 *
 * Un overlay a pantalla completa con estética de llamada (escena oscura con
 * profundidad, tile del participante con anillo de estado, subtítulos en vivo,
 * barra de controles flotante y contador de llamada), sobre el MISMO protocolo
 * WS por-turno de siempre (`config{voice,audio_mime}` + audio binario + `eot`).
 *
 * Arregla de raíz los fallos del modo voz original (diagnóstico 2026-07-09):
 *
 *   1. **El audio TTS no sonaba**: `createMediaElementSource` re-rutea TODO el
 *      audio por el grafo de Web Audio, y el `AudioContext` se creaba en un
 *      callback de red → nacía `suspended` (política de autoplay) → silencio
 *      total. Ahora el contexto se crea y RESUME en el click de «Iniciar
 *      llamada» (gesto de usuario) y, si aun así no está `running`, el audio
 *      se reproduce por la salida normal SIN engancharlo al grafo (lip-sync
 *      sintético): oír la respuesta nunca depende de Web Audio.
 *   2. **Push-to-talk frágil**: `onMouseLeave` cortaba la grabación al sacar
 *      el cursor del botón. Ahora usa pointer events con `setPointerCapture`:
 *      sueltes donde sueltes, el turno se envía entero.
 *   3. **La voz elegida no se respetaba**: el frame `ready` del servidor
 *      pisaba la elección del usuario. Ahora la elección (persistida en
 *      localStorage) manda: solo se adopta la del servidor si el usuario no
 *      ha elegido nunca, y al conectar se re-anuncia la elegida.
 *   4. **Errores tragados**: fallos de reproducción/WS ahora se muestran; el
 *      cierre del socket enseña su motivo (el backend manda además un frame
 *      `error` con el diagnóstico completo antes de cerrar).
 *   5. **Fuga de micrófono**: si cuelgas mientras `getUserMedia` resuelve, el
 *      stream huérfano se apaga al llegar.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Loader2, Mic, PhoneOff, Video } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { wsUrl } from "@/lib/ws";

export type CallStatus =
  | "lobby"
  | "connecting"
  | "ready"
  | "recording"
  | "thinking"
  | "speaking"
  | "error";

export type VoiceOption = {
  id: string;
  /** «Mujer», «Hombre» — el criterio que pidió el operador. */
  gender: "Mujer" | "Hombre";
  language: string;
  name: string;
};

/** Voces Kokoro curadas (el servidor valida el id contra su allowlist). */
export const VOICE_OPTIONS: VoiceOption[] = [
  { id: "ef_dora", gender: "Mujer", language: "Español", name: "Dora" },
  { id: "em_alex", gender: "Hombre", language: "Español", name: "Alex" },
  { id: "af_heart", gender: "Mujer", language: "English (US)", name: "Heart" },
  { id: "am_michael", gender: "Hombre", language: "English (US)", name: "Michael" },
  { id: "bf_emma", gender: "Mujer", language: "English (UK)", name: "Emma" },
  { id: "bm_george", gender: "Hombre", language: "English (UK)", name: "George" },
];

const VOICE_IDS = new Set(VOICE_OPTIONS.map((v) => v.id));

const STATUS_LABEL: Record<CallStatus, string> = {
  lobby: "Listo para llamar",
  connecting: "Conectando…",
  ready: "En llamada — mantén pulsado el micro para hablar",
  recording: "Escuchándote…",
  thinking: "Pensando…",
  speaking: "Hablando…",
  error: "Error",
};

const RING_CLASS: Record<CallStatus, string> = {
  lobby: "ring-white/15",
  connecting: "ring-amber-400/70 animate-pulse",
  ready: "ring-sky-400/60",
  recording: "ring-rose-500/90",
  thinking: "ring-violet-400/80 animate-pulse",
  speaking: "ring-emerald-400/90",
  error: "ring-rose-600/80",
};

function asString(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function formatDuration(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function VoiceCallShell({
  wsPath,
  title,
  subtitle,
  storageKey,
  defaultVoice = "ef_dora",
  testidPrefix,
  onFrame,
  renderAvatar,
  onClose,
}: {
  wsPath: string;
  /** Nombre del participante (Aria / Córtex). */
  title: string;
  /** Copy bajo el nombre (p.ej. la nota de honestidad del córtex). */
  subtitle?: string;
  /** Clave localStorage donde persiste la voz elegida. */
  storageKey: string;
  defaultVoice?: string;
  /** Prefijo de data-testid (compat: `voice` asistente, `cortex-voice` córtex). */
  testidPrefix: string;
  /** Interceptor de frames JSON extra (el córtex procesa `affect`). */
  onFrame?: (frame: Record<string, unknown>) => void;
  /** El avatar del participante (recibe speaking + mouthOpen + voz). */
  renderAvatar: (state: { speaking: boolean; mouthOpen: number; voice: string }) => ReactNode;
  /** Cerrar la videollamada (vuelve a la página). */
  onClose: () => void;
}) {
  const [status, setStatus] = useState<CallStatus>("lobby");
  const [voice, setVoiceState] = useState(defaultVoice);
  const [transcript, setTranscript] = useState("");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [mouthOpen, setMouthOpen] = useState(0);
  const [micLevel, setMicLevel] = useState(0);
  const [seconds, setSeconds] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const micRafRef = useRef<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const userChoseRef = useRef(false);
  const recordIntentRef = useRef(false);
  const disposedRef = useRef(false);

  // Rehidrata la voz elegida (persistida): la elección del usuario MANDA
  // sobre el default que anuncie el servidor en `ready`.
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(storageKey);
      if (stored && VOICE_IDS.has(stored)) {
        setVoiceState(stored);
        userChoseRef.current = true;
      }
    } catch {
      // sin localStorage (SSR/priv. estricta) → default
    }
  }, [storageKey]);

  const stopStream = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (micRafRef.current !== null) {
      cancelAnimationFrame(micRafRef.current);
      micRafRef.current = null;
    }
    setMicLevel(0);
  };

  const stopLipSync = () => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    setMouthOpen(0);
  };

  const hangup = () => {
    if (recRef.current?.state === "recording") recRef.current.stop();
    recordIntentRef.current = false;
    stopStream();
    stopLipSync();
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    void audioCtxRef.current?.close().catch(() => undefined);
    audioCtxRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    setStatus("lobby");
    setSeconds(0);
  };

  // Desmontaje: suelta socket, micro y contexto.
  useEffect(
    () => () => {
      disposedRef.current = true;
      hangup();
    },
    [], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const sendConfig = (v: string) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "config", voice: v }));
    }
  };

  const setVoice = (v: string) => {
    setVoiceState(v);
    userChoseRef.current = true;
    try {
      window.localStorage.setItem(storageKey, v);
    } catch {
      // best-effort
    }
    sendConfig(v);
  };

  const handleFrame = (raw: string) => {
    let frame: Record<string, unknown>;
    try {
      frame = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return;
    }
    onFrame?.(frame);
    switch (frame.type) {
      case "ready": {
        setStatus((s) => (s === "speaking" || s === "recording" ? s : "ready"));
        const serverVoice = asString(frame.voice);
        if (serverVoice && !userChoseRef.current && VOICE_IDS.has(serverVoice)) {
          // Sin elección del usuario: adopta el default del servidor.
          setVoiceState(serverVoice);
        } else if (userChoseRef.current && serverVoice && serverVoice !== voiceRef.current) {
          // El usuario YA eligió: su voz manda — re-anúnciala al servidor.
          sendConfig(voiceRef.current);
        }
        break;
      }
      case "transcript":
        setTranscript(asString(frame.text));
        setAnswer("");
        break;
      case "thinking":
        // El servidor transcribió y está llamando al cerebro (turno largo).
        setStatus((s) => (s === "recording" ? s : "thinking"));
        break;
      case "answer":
        setAnswer(asString(frame.text));
        break;
      case "turn_end":
        setStatus((s) => (s === "speaking" ? s : "ready"));
        break;
      case "error":
        setError(asString(frame.detail) || "error");
        setStatus((s) => (s === "lobby" || s === "connecting" ? "error" : "ready"));
        break;
      default:
        break;
    }
  };
  // La voz vigente accesible desde callbacks del WS sin re-suscribir.
  const voiceRef = useRef(voice);
  voiceRef.current = voice;

  // Lip-sync sintético cuando Web Audio no está disponible/running: una onda
  // suave mientras `speaking`, para que el avatar nunca quede congelado.
  const startSyntheticLips = () => {
    const t0 = performance.now();
    const tick = (t: number) => {
      const x = (t - t0) / 1000;
      setMouthOpen(0.25 + 0.22 * Math.abs(Math.sin(x * 6.1)) + 0.12 * Math.abs(Math.sin(x * 13.7)));
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  };

  const playAudio = (buf: ArrayBuffer) => {
    const url = URL.createObjectURL(new Blob([buf], { type: "audio/mpeg" }));
    const audio = new Audio(url);
    setStatus("speaking");
    const ctx = audioCtxRef.current;
    // SOLO enganchamos el elemento al grafo si el contexto está `running`:
    // createMediaElementSource re-rutea TODO el audio por el grafo y, con el
    // contexto suspendido, el resultado era silencio absoluto (bug original).
    if (ctx && ctx.state === "running") {
      try {
        const source = ctx.createMediaElementSource(audio);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        analyser.connect(ctx.destination);
        const data = new Uint8Array(analyser.frequencyBinCount);
        const tick = () => {
          analyser.getByteFrequencyData(data);
          let sum = 0;
          for (const v of data) sum += v;
          setMouthOpen(Math.min(1, (sum / data.length / 255) * 2.6));
          rafRef.current = requestAnimationFrame(tick);
        };
        tick();
      } catch {
        startSyntheticLips();
      }
    } else {
      startSyntheticLips();
    }
    const done = () => {
      URL.revokeObjectURL(url);
      stopLipSync();
      setStatus((s) => (s === "speaking" ? "ready" : s));
    };
    audio.onended = done;
    audio.onerror = () => {
      setError("No se pudo reproducir la respuesta de voz.");
      done();
    };
    void audio.play().catch(() => {
      setError("El navegador bloqueó la reproducción — pulsa «Iniciar llamada» de nuevo.");
      done();
    });
  };

  const connect = async () => {
    setStatus("connecting");
    setError(null);
    setTranscript("");
    setAnswer("");
    // GESTO DE USUARIO: crear y reanudar el AudioContext AQUÍ desbloquea el
    // autoplay para todo lo que suene después (fix del silencio total).
    try {
      audioCtxRef.current ??= new AudioContext();
      if (audioCtxRef.current.state === "suspended") await audioCtxRef.current.resume();
    } catch {
      audioCtxRef.current = null; // sin Web Audio: playback normal + labios sintéticos
    }
    const socket = new WebSocket(wsUrl(wsPath));
    socket.binaryType = "arraybuffer";
    socket.onmessage = (e: MessageEvent) => {
      if (typeof e.data === "string") handleFrame(e.data);
      else playAudio(e.data as ArrayBuffer);
    };
    socket.onerror = () => {
      setError("No se pudo conectar al servicio de voz.");
      setStatus("error");
    };
    socket.onclose = (ev: CloseEvent) => {
      setStatus((s) => {
        if (s === "error") return s;
        if (ev.reason) {
          setError(`Llamada finalizada: ${ev.reason}`);
          return "error";
        }
        return "lobby";
      });
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
    wsRef.current = socket;
    setSeconds(0);
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => setSeconds((s) => s + 1), 1000);
  };

  // Medidor del micrófono (tile propio) mientras grabas.
  const startMicMeter = (stream: MediaStream) => {
    const ctx = audioCtxRef.current;
    if (!ctx || ctx.state !== "running") return;
    try {
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 128;
      source.connect(analyser); // NO va a destination: el micro no debe sonar
      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteFrequencyData(data);
        let sum = 0;
        for (const v of data) sum += v;
        setMicLevel(Math.min(1, (sum / data.length / 255) * 3));
        micRafRef.current = requestAnimationFrame(tick);
      };
      tick();
    } catch {
      // sin medidor — la grabación sigue funcionando
    }
  };

  const startRecording = async () => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (recRef.current?.state === "recording") return;
    recordIntentRef.current = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      // Si el usuario soltó/colgó mientras se abría el micro, apágalo YA
      // (fix de la fuga de micrófono).
      if (!recordIntentRef.current || disposedRef.current || wsRef.current !== ws) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      streamRef.current = stream;
      startMicMeter(stream);
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (ev: BlobEvent) => {
        if (ev.data.size > 0) chunksRef.current.push(ev.data);
      };
      rec.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
        const buf = await blob.arrayBuffer();
        const sock = wsRef.current;
        if (sock && sock.readyState === WebSocket.OPEN && buf.byteLength > 0) {
          const mime = (blob.type || rec.mimeType || "audio/webm").split(";")[0];
          sock.send(JSON.stringify({ type: "config", voice: voiceRef.current, audio_mime: mime }));
          sock.send(buf);
          sock.send(JSON.stringify({ type: "eot" }));
          setStatus("thinking");
        } else {
          setStatus("ready");
        }
        stopStream();
      };
      recRef.current = rec;
      rec.start();
      setStatus("recording");
    } catch {
      recordIntentRef.current = false;
      setError("Micrófono no disponible o permiso denegado.");
      setStatus("ready");
    }
  };

  const stopRecording = () => {
    recordIntentRef.current = false;
    if (recRef.current?.state === "recording") recRef.current.stop();
  };

  const inCall = status !== "lobby" && status !== "connecting" && status !== "error";
  const speaking = status === "speaking";
  const activeVoice = VOICE_OPTIONS.find((v) => v.id === voice);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col overflow-hidden bg-[#0b1020] text-slate-100"
      data-testid={`${testidPrefix}-call`}
    >
      {/* Escena con profundidad (radiales suaves, sin assets externos) */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(60% 50% at 50% 30%, rgba(76,110,245,0.16) 0%, transparent 70%)," +
            "radial-gradient(45% 40% at 85% 80%, rgba(139,92,246,0.12) 0%, transparent 70%)," +
            "radial-gradient(40% 35% at 12% 78%, rgba(16,185,129,0.08) 0%, transparent 70%)",
        }}
      />

      {/* Cabecera: identidad + estado + duración */}
      <header className="relative z-10 flex items-center justify-between px-6 py-4">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{title}</p>
          {subtitle ? <p className="truncate text-xs text-slate-400">{subtitle}</p> : null}
        </div>
        <div className="flex items-center gap-3">
          <span
            className="rounded-full bg-white/10 px-3 py-1 text-xs font-medium"
            data-testid={`${testidPrefix}-status`}
          >
            {STATUS_LABEL[status]}
          </span>
          {inCall ? (
            <span className="font-mono text-xs tabular-nums text-slate-300">
              {formatDuration(seconds)}
            </span>
          ) : null}
        </div>
      </header>

      {/* Tile central del participante */}
      <main className="relative z-10 flex flex-1 flex-col items-center justify-center gap-5 px-6">
        <div
          className={cn(
            "relative flex flex-col items-center justify-center rounded-3xl bg-white/[0.04] p-8 shadow-2xl ring-4 backdrop-blur-sm transition-all",
            RING_CLASS[status],
          )}
        >
          {renderAvatar({ speaking, mouthOpen, voice })}
          <div className="mt-3 flex items-center gap-2 rounded-full bg-black/40 px-3 py-1">
            <span className="text-sm font-medium">{title}</span>
            {activeVoice ? (
              <span className="text-xs text-slate-400">
                {activeVoice.gender} · {activeVoice.language}
              </span>
            ) : null}
          </div>
        </div>

        {/* Subtítulos en vivo */}
        <div className="flex min-h-[72px] w-full max-w-2xl flex-col items-center gap-1.5">
          {transcript ? (
            <p
              className="max-w-full truncate rounded-2xl bg-white/8 px-4 py-1.5 text-sm text-slate-300"
              data-testid={`${testidPrefix}-transcript`}
            >
              <span className="mr-1.5 text-xs font-semibold text-slate-500">Tú</span>
              {transcript}
            </p>
          ) : null}
          {status === "thinking" ? (
            <p className="rounded-2xl bg-white/8 px-4 py-1.5 text-sm text-slate-400">
              <Loader2 className="mr-1.5 inline h-3.5 w-3.5 animate-spin" />
              {title} está pensando…
            </p>
          ) : null}
          {answer ? (
            <p
              className="max-h-24 w-fit max-w-full overflow-y-auto rounded-2xl bg-sky-500/15 px-4 py-1.5 text-sm"
              data-testid={`${testidPrefix}-answer`}
            >
              <span className="mr-1.5 text-xs font-semibold text-sky-300">{title}</span>
              {answer}
            </p>
          ) : null}
          {error ? (
            <p
              className="max-w-full rounded-2xl bg-rose-500/15 px-4 py-1.5 text-xs text-rose-200"
              data-testid={`${testidPrefix}-error`}
            >
              {error}
            </p>
          ) : null}
        </div>
      </main>

      {/* Tile propio (nivel de micro) */}
      {inCall ? (
        <div className="absolute bottom-28 right-6 z-10 flex h-24 w-36 flex-col items-center justify-center gap-1.5 rounded-2xl bg-white/[0.05] ring-1 ring-white/10 backdrop-blur-sm">
          <div className="flex h-8 items-end gap-1">
            {[0.5, 0.8, 1, 0.7, 0.45].map((f, i) => (
              <span
                key={i}
                className={cn(
                  "w-1.5 rounded-full transition-all duration-75",
                  status === "recording" ? "bg-rose-400" : "bg-slate-500/60",
                )}
                style={{ height: `${6 + micLevel * 26 * f}px` }}
              />
            ))}
          </div>
          <span className="text-xs text-slate-400">Tú</span>
        </div>
      ) : null}

      {/* Barra de controles flotante */}
      <footer className="relative z-10 flex items-center justify-center gap-3 px-6 pb-8">
        {!inCall ? (
          <>
            <Button
              size="lg"
              onClick={() => void connect()}
              disabled={status === "connecting"}
              data-testid={`${testidPrefix}-connect`}
              className="rounded-full bg-emerald-600 px-6 text-white hover:bg-emerald-500"
            >
              {status === "connecting" ? (
                <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              ) : (
                <Video className="mr-2 h-5 w-5" />
              )}
              Iniciar llamada
            </Button>
            <Button
              variant="outline"
              size="lg"
              onClick={onClose}
              data-testid={`${testidPrefix}-close`}
              className="rounded-full border-white/20 bg-white/5 text-slate-200 hover:bg-white/10"
            >
              Volver
            </Button>
          </>
        ) : (
          <>
            {/* Push-to-talk con pointer capture: soltar FUERA también envía. */}
            <Button
              size="lg"
              onPointerDown={(e) => {
                e.currentTarget.setPointerCapture(e.pointerId);
                void startRecording();
              }}
              onPointerUp={stopRecording}
              onPointerCancel={stopRecording}
              disabled={status === "thinking" || status === "speaking"}
              data-testid={`${testidPrefix}-talk`}
              className={cn(
                "h-16 w-16 rounded-full p-0 text-white shadow-lg transition-transform",
                status === "recording"
                  ? "scale-110 bg-rose-600 hover:bg-rose-600"
                  : "bg-sky-600 hover:bg-sky-500",
              )}
              aria-label={
                status === "recording" ? "Suelta para enviar" : "Mantén pulsado para hablar"
              }
            >
              <Mic className="h-7 w-7" />
            </Button>

            <select
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
              aria-label="Voz"
              data-testid={`${testidPrefix}-select`}
              className="h-11 rounded-full border border-white/15 bg-white/10 px-4 text-sm text-slate-100 outline-none backdrop-blur-sm [&>optgroup]:bg-slate-900 [&>option]:bg-slate-900"
            >
              {["Español", "English (US)", "English (UK)"].map((lang) => (
                <optgroup key={lang} label={lang}>
                  {VOICE_OPTIONS.filter((v) => v.language === lang).map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.gender} · {v.name}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>

            <Button
              size="lg"
              onClick={() => {
                hangup();
                onClose();
              }}
              data-testid={`${testidPrefix}-hangup`}
              className="h-16 w-16 rounded-full bg-rose-600 p-0 text-white shadow-lg hover:bg-rose-500"
              aria-label="Colgar"
            >
              <PhoneOff className="h-7 w-7" />
            </Button>
          </>
        )}
      </footer>
    </div>
  );
}
