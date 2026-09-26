// Browser side of /api/voice: mic → 16 kHz PCM16 frames over WebSocket; reply audio → gapless playback.
import { getToken, wsUrl } from "../api/client";
import type { Artifact } from "../api/types";

export type VoiceState = "connecting" | "loading" | "listening" | "user_speaking" | "processing" | "thinking" | "speaking" | "closed";

export type VoiceEvent =
  | { type: "ready"; conversation_id: string; sample_rate: number; turn_detection: string }
  | { type: "state"; state: VoiceState }
  | { type: "loading"; step: string; message: string }
  | { type: "partial_transcript"; text: string }
  | { type: "final_transcript"; text: string; turn: number }
  | { type: "turn_started"; conversation_id: string; user_message_id: string; assistant_message_id: string }
  | { type: "assistant_delta"; text: string }
  | { type: "tool"; name: string; status: string }
  | { type: "audio"; seq: number; text: string; sample_rate: number; duration_s: number }
  | { type: "interrupted"; reason: string }
  | { type: "metrics"; end_of_speech_to_first_audio_ms?: number; final?: boolean; [k: string]: unknown }
  | { type: "turn_done"; status: string }
  | { type: "confirm"; id: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_done"; id: string; name: string; ok: boolean; files: { id: string; filename: string; mime: string }[] }
  | { type: "artifact"; artifact: Artifact }
  | { type: "error"; message: string };

const TARGET_RATE = 16000;
const FRAME = 512; // 32 ms at 16 kHz

const WORKLET = `
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("la-capture", Capture);`;

/** Streaming resampler (linear interpolation) from the device rate to 16 kHz. */
export class Resampler {
  private pos = 0;
  private last = 0;
  constructor(private readonly ratio: number) {}
  push(input: Float32Array): Float32Array {
    const out: number[] = [];
    // Positions are relative to input[0]; -1 refers to the last sample of the previous chunk.
    while (this.pos < input.length - 1) {
      const i = Math.floor(this.pos);
      const frac = this.pos - i;
      const a = i < 0 ? this.last : input[i];
      const b = input[i + 1];
      out.push(a + (b - a) * frac);
      this.pos += this.ratio;
    }
    this.pos -= input.length;
    this.last = input[input.length - 1] ?? this.last;
    return Float32Array.from(out);
  }
}

export function floatToPcm16(x: Float32Array): Int16Array {
  const out = new Int16Array(x.length);
  for (let i = 0; i < x.length; i++) out[i] = Math.max(-1, Math.min(1, x[i])) * 0x7fff;
  return out;
}

export interface VoiceClientOptions {
  mode: "conversation" | "dictation";
  conversationId?: string | null;
  voice?: string;
  speed?: number;
  sensitivity?: number;
  onEvent: (e: VoiceEvent) => void;
}

export class VoiceClient {
  private ws: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private micCtx: AudioContext | null = null;
  private playCtx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private pending = new Int16Array(0);
  private sources = new Set<AudioBufferSourceNode>();
  private nextStart = 0;
  private awaitingAudio: { sample_rate: number } | null = null;
  private turnDone = true;
  muted = false;
  micAnalyser: AnalyserNode | null = null;
  outAnalyser: AnalyserNode | null = null;

  constructor(private readonly opts: VoiceClientOptions) {}

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    // Conversation mode uses ONE audio context for the mic and the reply, with larger buffers
    // ("playback"). On phones a second context, or small buffers while the mic is on, make the
    // reply crackle; the extra few milliseconds of latency aren't noticeable.
    this.micCtx = new AudioContext(this.opts.mode === "conversation" ? { latencyHint: "playback" } : {});
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await this.micCtx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    const src = this.micCtx.createMediaStreamSource(this.stream);
    this.micAnalyser = this.micCtx.createAnalyser();
    this.micAnalyser.fftSize = 512;
    src.connect(this.micAnalyser);
    this.node = new AudioWorkletNode(this.micCtx, "la-capture");
    const resampler = new Resampler(this.micCtx.sampleRate / TARGET_RATE);
    this.node.port.onmessage = (e: MessageEvent<Float32Array>) => this.onMicChunk(resampler.push(e.data));
    src.connect(this.node);

    if (this.opts.mode === "conversation") {
      this.playCtx = this.micCtx;
      this.outAnalyser = this.playCtx.createAnalyser();
      this.outAnalyser.fftSize = 512;
      this.outAnalyser.connect(this.playCtx.destination);
    }

    const q = new URLSearchParams({ mode: this.opts.mode });
    if (this.opts.conversationId) q.set("conversation_id", this.opts.conversationId);
    if (this.opts.voice) q.set("voice", this.opts.voice);
    if (this.opts.speed) q.set("speed", String(this.opts.speed));
    if (this.opts.sensitivity != null) q.set("sensitivity", String(this.opts.sensitivity));
    const token = getToken();
    if (token) q.set("token", token);
    this.ws = new WebSocket(wsUrl(`/voice?${q}`));
    this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (m) => this.onMessage(m);
    this.ws.onclose = () => this.opts.onEvent({ type: "state", state: "closed" });
    await new Promise<void>((resolve, reject) => {
      this.ws!.onopen = () => resolve();
      this.ws!.onerror = () => reject(new Error("Could not connect to the voice service"));
    });
  }

  private onMicChunk(samples: Float32Array) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const pcm = floatToPcm16(this.muted ? new Float32Array(samples.length) : samples);
    const joined = new Int16Array(this.pending.length + pcm.length);
    joined.set(this.pending);
    joined.set(pcm, this.pending.length);
    const usable = joined.length - (joined.length % FRAME);
    if (usable) this.ws.send(joined.slice(0, usable).buffer);
    this.pending = joined.slice(usable);
  }

  private onMessage(m: MessageEvent) {
    if (typeof m.data !== "string") {
      if (this.awaitingAudio) this.play(new Int16Array(m.data as ArrayBuffer), this.awaitingAudio.sample_rate);
      this.awaitingAudio = null;
      return;
    }
    const ev = JSON.parse(m.data) as VoiceEvent;
    if (ev.type === "audio") this.awaitingAudio = { sample_rate: ev.sample_rate };
    if (ev.type === "interrupted") this.stopPlayback();
    if (ev.type === "turn_started") this.turnDone = false;
    if (ev.type === "turn_done") {
      this.turnDone = true;
      this.maybePlaybackDone();
    }
    this.opts.onEvent(ev);
  }

  private play(pcm: Int16Array, rate: number) {
    const ctx = this.playCtx;
    if (!ctx || !pcm.length) return;
    const buf = ctx.createBuffer(1, pcm.length, rate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 0x8000;
    // 4 ms fades at both ends, so a sentence never starts or stops on a click.
    const fade = Math.min(Math.floor(rate * 0.004), pcm.length >> 1);
    for (let i = 0; i < fade; i++) {
      ch[i] *= i / fade;
      ch[pcm.length - 1 - i] *= i / fade;
    }
    const node = ctx.createBufferSource();
    node.buffer = buf;
    node.connect(this.outAnalyser!);
    const at = Math.max(ctx.currentTime + 0.06, this.nextStart);
    node.start(at);
    this.nextStart = at + buf.duration;
    this.sources.add(node);
    node.onended = () => {
      this.sources.delete(node);
      this.maybePlaybackDone();
    };
  }

  private maybePlaybackDone() {
    if (this.turnDone && this.sources.size === 0 && this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "playback_done" }));
    }
  }

  stopPlayback() {
    for (const s of this.sources) {
      s.onended = null;
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    }
    this.sources.clear();
    this.nextStart = 0;
  }

  interrupt() {
    this.stopPlayback();
    this.ws?.send(JSON.stringify({ type: "interrupt" }));
  }

  /** Dictation: finalize the current utterance, then close. */
  finish() {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "stop" }));
  }

  close() {
    this.stopPlayback();
    try {
      this.ws?.close();
    } catch {
      /* ignore */
    }
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.micCtx?.close().catch(() => undefined);
    if (this.playCtx !== this.micCtx) this.playCtx?.close().catch(() => undefined);
    this.ws = null;
  }

  private static levelBuf = new Uint8Array(512);

  static level(an: AnalyserNode | null): number {
    if (!an) return 0;
    if (VoiceClient.levelBuf.length !== an.fftSize) VoiceClient.levelBuf = new Uint8Array(an.fftSize);
    const buf = VoiceClient.levelBuf;
    an.getByteTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += ((v - 128) / 128) ** 2;
    return Math.min(1, Math.sqrt(sum / buf.length) * 4);
  }
}
