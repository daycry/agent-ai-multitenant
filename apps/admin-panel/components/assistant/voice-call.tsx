"use client";

/**
 * Voice mode for the personal assistant (ADR 0073, voz F1) — push-to-talk.
 *
 * Connects to the api-server WS `/ws/assistant/voice`, captures the microphone
 * (MediaRecorder), sends the utterance + an `eot` control frame, and plays the
 * synthesized answer (binary audio frame). The "brain" + STT/TTS run server-side
 * (Docker); this component is just capture + playback + the voice selector.
 *
 * F1 is push-to-talk and avatar-less; streaming/barge-in (F2) and the lip-sync
 * avatar (F3) build on top. The full audio loop needs the `stt`/`tts` services
 * running + a real microphone, so this is verified in the browser, not in CI.
 */

import { useEffect, useRef, useState } from "react";
import { Loader2, Mic, Phone, PhoneOff, Square } from "lucide-react";

import { AvatarFace } from "@/components/assistant/avatar-face";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { wsUrl } from "@/lib/ws";

type Status = "idle" | "connecting" | "ready" | "recording" | "thinking" | "speaking" | "error";

/** Curated Kokoro voices (M/F, ES + EN). The server validates the id. */
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

export function VoiceCall() {
  const [status, setStatus] = useState<Status>("idle");
  const [voice, setVoice] = useState("af_heart");
  const [transcript, setTranscript] = useState("");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState<string | null>(null);

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

  // Tear the socket + mic down when the component unmounts.
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

  // Drive the avatar's mouth from the TTS audio's loudness (real amplitude
  // lip-sync, ADR 0073 MVP) via a Web Audio analyser. Best-effort: if Web Audio
  // is unavailable the audio still plays, just without lip-sync.
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
      // No Web Audio → play without lip-sync.
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
    const socket = new WebSocket(wsUrl("/ws/assistant/voice"));
    socket.binaryType = "arraybuffer";
    socket.onmessage = (e: MessageEvent) => {
      if (typeof e.data === "string") handleFrame(e.data);
      else playAudio(e.data as ArrayBuffer);
    };
    socket.onerror = () => {
      setError("no se pudo conectar al servicio de voz");
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
          // Announce the REAL audio mime (MediaRecorder emits webm/opus, not wav)
          // so the server forwards it to STT verbatim — the shared content_type
          // fix; forcing wav server-side broke transcription (ADR 0073).
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
    <div className="flex flex-col gap-3" data-testid="assistant-voice-call">
      <div className="flex items-center gap-2">
        {!connected ? (
          <Button onClick={connect} disabled={status === "connecting"} data-testid="voice-connect">
            {status === "connecting" ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Phone className="mr-1 h-4 w-4" />
            )}
            Iniciar videollamada
          </Button>
        ) : (
          <Button variant="outline" onClick={hangup} data-testid="voice-hangup">
            <PhoneOff className="mr-1 h-4 w-4" />
            Colgar
          </Button>
        )}
        <Select
          value={voice}
          onChange={(e) => changeVoice(e.target.value)}
          disabled={!connected}
          aria-label="Voz"
          data-testid="voice-select"
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
        <AvatarFace
          speaking={status === "speaking"}
          mouthOpen={mouthOpen}
          label={VOICES.find((v) => v.id === voice)?.label.split(" · ")[0] ?? "Aria"}
        />
      ) : null}

      {connected ? (
        <Button
          // Push-to-talk: hold to record, release to send.
          onMouseDown={startRecording}
          onMouseUp={stopRecording}
          onMouseLeave={stopRecording}
          onTouchStart={startRecording}
          onTouchEnd={stopRecording}
          disabled={!canTalk && status !== "recording"}
          variant={status === "recording" ? "destructive" : "default"}
          data-testid="voice-talk"
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

      <p className="text-muted-foreground text-xs" data-testid="voice-status">
        {STATUS_LABEL[status]}
      </p>

      {transcript ? (
        <p className="text-sm">
          <span className="text-muted-foreground">Tú:</span> {transcript}
        </p>
      ) : null}
      {answer ? (
        <p className="text-sm">
          <span className="text-muted-foreground">Aria:</span> {answer}
        </p>
      ) : null}
      {error ? (
        <p className="text-destructive text-xs" data-testid="voice-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
