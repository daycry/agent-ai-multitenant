"use client";

/**
 * Modo voz del córtex del System Owner (Córtex F5, ADR 0073 voz + 0075 afecto).
 *
 * CLON parametrizado del modo voz del asistente (`components/assistant/voice-call.tsx`):
 * misma pila de captura (getUserMedia + MediaRecorder), mismo transporte por-turno
 * (envía `config{voice,audio_mime}` + audio binario + `eot`; reproduce el audio TTS
 * devuelto) y el mismo lip-sync por amplitud vía Web Audio. Cambia SÓLO tres cosas:
 *
 *   1. **WS** = `/ws/owner/cortex/voice` (no `/ws/assistant/voice`).
 *   2. **Gate** = System Owner (el padre lo monta sólo si `isSystemOwner`; el
 *      backend `_is_db_system_owner` es la barrera real, ADR 0074 — esto es UX).
 *   3. **Afecto** = procesa el frame `{type:'affect', valence, arousal, …}` que el
 *      server emite tras el `answer`, y se lo pasa al avatar para que mapee
 *      color/expresión/sway (función pura `avatarStyleFromAffect`).
 *
 * El "cerebro" (córtex) + STT/TTS corren en el servidor (Docker); este componente
 * es captura + reproducción + el selector de voz + el avatar afectivo. El bucle de
 * audio completo necesita los servicios `stt`/`tts` arrancados y un micrófono real,
 * así que se verifica en el navegador, no en CI.
 *
 * Honestidad de producto (ADR 0075 §6): el avatar refleja un afecto SIMULADO y
 * determinista, NO sentimientos reales — la página lo rotula.
 */

import { useEffect, useRef, useState } from "react";
import { Loader2, Mic, Phone, PhoneOff, Square } from "lucide-react";

import { CortexAvatar } from "@/components/cortex/cortex-avatar";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { parseVoiceAffectFrame, type CortexVoiceAffectFrame } from "@/lib/cortex";
import { renderPlanDraft } from "@/lib/plan-draft-md";
import { wsUrl } from "@/lib/ws";

type Status = "idle" | "connecting" | "ready" | "recording" | "thinking" | "speaking" | "error";

/** Voces Kokoro curadas (M/F, ES + EN). El servidor valida el id (allowlist). */
const VOICES: { id: string; label: string }[] = [
  { id: "af_heart", label: "Femenina · EN-US (Heart)" },
  { id: "am_michael", label: "Masculina · EN-US (Michael)" },
  { id: "bf_emma", label: "Femenina · EN-GB (Emma)" },
  { id: "bm_george", label: "Masculina · EN-GB (George)" },
  { id: "ef_dora", label: "Femenina · ES (Dora)" },
  { id: "em_alex", label: "Masculina · ES (Alex)" },
];

const STATUS_LABEL: Record<Status, string> = {
  idle: "Desconectado",
  connecting: "Conectando…",
  ready: "Listo — mantén pulsado para hablar",
  recording: "Grabando…",
  thinking: "Pensando…",
  speaking: "Hablando…",
  error: "Error",
};

function asString(v: unknown): string {
  return typeof v === "string" ? v : "";
}

export function CortexVoiceCall() {
  const [status, setStatus] = useState<Status>("idle");
  const [voice, setVoice] = useState("ef_dora");
  const [transcript, setTranscript] = useState("");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Último frame de afecto del córtex; alimenta el color/expresión del avatar.
  const [affect, setAffect] = useState<CortexVoiceAffectFrame | null>(null);

  const [mouthOpen, setMouthOpen] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);

  const stopStream = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  };

  const hangup = () => {
    if (recRef.current?.state === "recording") recRef.current.stop();
    stopStream();
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    void audioCtxRef.current?.close().catch(() => undefined);
    audioCtxRef.current = null;
    setMouthOpen(0);
    wsRef.current?.close();
    wsRef.current = null;
    setStatus("idle");
  };

  // Cierra el socket + mic al desmontar.
  useEffect(() => () => hangup(), []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleFrame = (raw: string) => {
    let frame: Record<string, unknown>;
    try {
      frame = JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return;
    }
    switch (frame.type) {
      case "ready":
        setStatus("ready");
        if (asString(frame.voice)) setVoice(asString(frame.voice));
        break;
      case "transcript":
        setTranscript(asString(frame.text));
        setAnswer("");
        break;
      case "answer":
        setAnswer(asString(frame.text));
        break;
      case "affect": {
        // El frame afectivo (plano) llega tras el `answer` y ANTES del audio: el
        // avatar mapea valence→color, arousal→energía/sway, y muestra el mood.
        const parsed = parseVoiceAffectFrame(frame);
        if (parsed) setAffect(parsed);
        break;
      }
      case "turn_end":
        setStatus((s) => (s === "speaking" ? s : "ready"));
        break;
      case "error":
        setError(asString(frame.detail) || "error");
        setStatus("ready");
        break;
      default:
        break;
    }
  };

  const stopLipSync = () => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    setMouthOpen(0);
  };

  // Mueve la boca del avatar con la sonoridad del audio TTS (lip-sync por amplitud
  // real, MVP del ADR 0073) vía un analyser de Web Audio. Best-effort: sin Web
  // Audio el audio igual suena, sólo sin lip-sync.
  const attachLipSync = (audio: HTMLAudioElement) => {
    try {
      audioCtxRef.current ??= new AudioContext();
      const ctx = audioCtxRef.current;
      void ctx.resume();
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
        const avg = sum / data.length / 255; // 0..1
        setMouthOpen(Math.min(1, avg * 2.4));
        rafRef.current = requestAnimationFrame(tick);
      };
      tick();
    } catch {
      // Sin Web Audio → reproduce sin lip-sync.
    }
  };

  const playAudio = (buf: ArrayBuffer) => {
    const url = URL.createObjectURL(new Blob([buf], { type: "audio/mpeg" }));
    const audio = new Audio(url);
    setStatus("speaking");
    attachLipSync(audio);
    audio.onended = () => {
      URL.revokeObjectURL(url);
      stopLipSync();
      setStatus("ready");
    };
    void audio.play().catch(() => {
      stopLipSync();
      setStatus("ready");
    });
  };

  const connect = () => {
    setStatus("connecting");
    setError(null);
    const socket = new WebSocket(wsUrl("/ws/owner/cortex/voice"));
    socket.binaryType = "arraybuffer";
    socket.onmessage = (e: MessageEvent) => {
      if (typeof e.data === "string") handleFrame(e.data);
      else playAudio(e.data as ArrayBuffer);
    };
    socket.onerror = () => {
      setError("servicio de voz no disponible");
      setStatus("error");
    };
    socket.onclose = () => setStatus((s) => (s === "error" ? s : "idle"));
    wsRef.current = socket;
  };

  const startRecording = async () => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;
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
          // Anuncia el mime REAL (MediaRecorder emite webm/opus, no wav) para que
          // el server lo reenvíe a STT verbatim (fix compartido del content_type).
          const mime = (blob.type || rec.mimeType || "audio/webm").split(";")[0];
          sock.send(JSON.stringify({ type: "config", voice, audio_mime: mime }));
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
      setError("micrófono no disponible o permiso denegado");
      setStatus("ready");
    }
  };

  const stopRecording = () => {
    if (recRef.current?.state === "recording") recRef.current.stop();
  };

  const changeVoice = (v: string) => {
    setVoice(v);
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "config", voice: v }));
    }
  };

  const connected = status !== "idle" && status !== "connecting" && status !== "error";
  const canTalk = status === "ready";

  return (
    <div className="flex flex-col gap-3" data-testid="cortex-voice-call">
      <div className="flex items-center gap-2">
        {!connected ? (
          <Button
            onClick={connect}
            disabled={status === "connecting"}
            data-testid="cortex-voice-connect"
          >
            {status === "connecting" ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Phone className="mr-1 h-4 w-4" />
            )}
            Iniciar videollamada
          </Button>
        ) : (
          <Button variant="outline" onClick={hangup} data-testid="cortex-voice-hangup">
            <PhoneOff className="mr-1 h-4 w-4" />
            Colgar
          </Button>
        )}
        <Select
          value={voice}
          onChange={(e) => changeVoice(e.target.value)}
          disabled={!connected}
          aria-label="Voz"
          data-testid="cortex-voice-select"
          className="w-56"
        >
          {VOICES.map((v) => (
            <option key={v.id} value={v.id}>
              {v.label}
            </option>
          ))}
        </Select>
      </div>

      {connected ? (
        <CortexAvatar
          speaking={status === "speaking"}
          mouthOpen={mouthOpen}
          affect={affect}
          label="Córtex (afecto simulado)"
        />
      ) : null}

      {connected ? (
        <Button
          // Push-to-talk: mantén pulsado para grabar, suelta para enviar.
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onMouseLeave={stopRecording}
          onTouchStart={startRecording}
          onTouchEnd={stopRecording}
          disabled={!canTalk && status !== "recording"}
          variant={status === "recording" ? "destructive" : "default"}
          data-testid="cortex-voice-talk"
          className="w-fit"
        >
          {status === "recording" ? (
            <Square className="mr-1 h-4 w-4" />
          ) : (
            <Mic className="mr-1 h-4 w-4" />
          )}
          {status === "recording" ? "Suelta para enviar" : "Mantén pulsado para hablar"}
        </Button>
      ) : null}

      <p className="text-muted-foreground text-xs" data-testid="cortex-voice-status">
        {STATUS_LABEL[status]}
      </p>

      {transcript ? (
        <p className="text-sm">
          <span className="text-muted-foreground">Tú:</span> {transcript}
        </p>
      ) : null}
      {answer ? (
        <div className="text-sm" data-testid="cortex-voice-answer">
          <span className="text-muted-foreground">Córtex:</span> {renderPlanDraft(answer)}
        </div>
      ) : null}
      {error ? (
        <p className="text-destructive text-xs" data-testid="cortex-voice-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
