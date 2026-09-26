// Browser media capture: microphone recording with levels, screen capture, camera.

export function pickMime(candidates: string[]): string | undefined {
  return candidates.find((m) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(m));
}

export const AUDIO_MIME = () => pickMime(["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus", "audio/webm"]);
export const VIDEO_MIME = () => pickMime(["video/mp4;codecs=avc1", "video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm", "video/mp4"]);

export function extFor(mime: string | undefined, fallback: string) {
  if (!mime) return fallback;
  if (mime.includes("mp4")) return mime.startsWith("audio") ? "m4a" : "mp4";
  if (mime.includes("ogg")) return "ogg";
  if (mime.includes("webm")) return "webm";
  return fallback;
}

export interface Recording {
  stop: () => Promise<Blob>;
  cancel: () => void;
  level: () => number; // 0..1 RMS level for the waveform
  mime: string | undefined;
  startedAt: number;
}

/** Start recording from a stream (mic or camera). Levels come from the audio track. */
export function startRecording(stream: MediaStream, mime: string | undefined): Recording {
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const chunks: Blob[] = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.start(250);
  let ctx: AudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  const buf = new Uint8Array(1024);
  if (stream.getAudioTracks().length) {
    ctx = new AudioContext();
    analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(analyser);
  }
  const cleanup = () => {
    stream.getTracks().forEach((t) => t.stop());
    ctx?.close().catch(() => undefined);
  };
  return {
    mime: rec.mimeType || mime,
    startedAt: performance.now(),
    level: () => {
      if (!analyser) return 0;
      analyser.getByteTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) sum += ((v - 128) / 128) ** 2;
      return Math.min(1, Math.sqrt(sum / buf.length) * 3.5);
    },
    stop: () =>
      new Promise<Blob>((resolve) => {
        rec.onstop = () => {
          cleanup();
          resolve(new Blob(chunks, { type: rec.mimeType || mime || "application/octet-stream" }));
        };
        if (rec.state !== "inactive") rec.stop();
        else rec.onstop(new Event("stop"));
      }),
    cancel: () => {
      rec.onstop = null;
      if (rec.state !== "inactive") rec.stop();
      cleanup();
    },
  };
}

export async function micStream(): Promise<MediaStream> {
  return navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
}

/** One PNG frame of a screen/window the user picks (browser screen-share picker). */
export async function captureScreen(): Promise<Blob> {
  const stream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 5 }, audio: false });
  try {
    const video = document.createElement("video");
    video.srcObject = stream;
    video.muted = true;
    await video.play();
    await new Promise((r) => setTimeout(r, 250)); // let the first real frame arrive
    return await frameToBlob(video, "image/png");
  } finally {
    stream.getTracks().forEach((t) => t.stop());
  }
}

export function frameToBlob(video: HTMLVideoElement, type = "image/jpeg", quality = 0.92): Promise<Blob> {
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d")!.drawImage(video, 0, 0);
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("capture failed"))), type, quality));
}

export function mediaSupport() {
  const md = typeof navigator !== "undefined" ? navigator.mediaDevices : undefined;
  return {
    mic: !!md?.getUserMedia && typeof MediaRecorder !== "undefined",
    camera: !!md?.getUserMedia,
    screen: !!md && "getDisplayMedia" in md,
  };
}
