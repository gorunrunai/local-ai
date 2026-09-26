import { Camera, Circle, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { extFor, frameToBlob, startRecording, VIDEO_MIME, type Recording } from "../lib/media";
import { Modal } from "./Modal";

/** Live waveform drawn from a recording's level() — bars scroll right to left. */
export function Waveform({ level, className = "" }: { level: (() => number) | null; className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (!level) return;
    const levels: number[] = [];
    let raf = 0;
    const draw = () => {
      const c = ref.current;
      if (c) {
        const ctx = c.getContext("2d")!;
        const w = (c.width = c.clientWidth * devicePixelRatio);
        const h = (c.height = c.clientHeight * devicePixelRatio);
        levels.push(level());
        const bar = 3 * devicePixelRatio;
        const gap = 2 * devicePixelRatio;
        const n = Math.floor(w / (bar + gap));
        while (levels.length > n) levels.shift();
        ctx.clearRect(0, 0, w, h);
        ctx.fillStyle = getComputedStyle(c).color;
        levels.forEach((l, i) => {
          const bh = Math.max(bar, l * h);
          ctx.fillRect(w - (levels.length - i) * (bar + gap), (h - bh) / 2, bar, bh);
        });
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [level]);
  return <canvas ref={ref} className={`h-7 w-full text-accent ${className}`} aria-hidden="true" />;
}

export function useElapsed(since: number | null) {
  const [now, setNow] = useState(performance.now());
  useEffect(() => {
    if (since == null) return;
    const t = setInterval(() => setNow(performance.now()), 250);
    return () => clearInterval(t);
  }, [since]);
  return since == null ? 0 : Math.max(0, (now - since) / 1000);
}

/** Webcam: take a photo or record a short clip (max 60 s). */
export function CameraDialog({ onClose, onCapture }: { onClose: () => void; onCapture: (file: File, source: string) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [rec, setRec] = useState<Recording | null>(null);
  const [error, setError] = useState<string | null>(null);
  const elapsed = useElapsed(rec?.startedAt ?? null);

  useEffect(() => {
    let s: MediaStream | null = null;
    navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 } }, audio: true }).then((st) => {
      s = st;
      setStream(st);
      if (video.current) {
        video.current.srcObject = st;
        video.current.play().catch(() => undefined);
      }
    }, (e) => setError(e.name === "NotAllowedError" ? "Camera permission was denied. Allow it in System Settings → Privacy & Security → Camera." : String(e.message ?? e)));
    return () => s?.getTracks().forEach((t) => t.stop());
  }, []);

  useEffect(() => {
    if (rec && elapsed > 60) stopClip();
  });

  const photo = async () => {
    if (!video.current) return;
    const blob = await frameToBlob(video.current);
    onCapture(new File([blob], `photo-${Date.now()}.jpg`, { type: "image/jpeg" }), "camera");
    onClose();
  };
  const startClip = () => {
    if (!stream) return;
    // Record a clone so stopping the recorder doesn't kill the preview mid-way.
    setRec(startRecording(stream.clone(), VIDEO_MIME()));
  };
  const stopClip = async () => {
    if (!rec) return;
    const blob = await rec.stop();
    setRec(null);
    onCapture(new File([blob], `clip-${Date.now()}.${extFor(rec.mime, "webm")}`, { type: blob.type }), "camera");
    onClose();
  };

  return (
    <Modal title="Camera" onClose={() => { rec?.cancel(); onClose(); }}>
      {error ? <p className="text-sm text-danger">{error}</p> : (
        <div className="space-y-3">
          <video ref={video} muted playsInline className="w-full rounded-lg bg-black" />
          <div className="flex items-center justify-center gap-3">
            {!rec && <button type="button" onClick={photo} disabled={!stream} className="flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-on-accent disabled:opacity-50"><Camera size={16} /> Take photo</button>}
            {!rec ? (
              <button type="button" onClick={startClip} disabled={!stream} className="flex items-center gap-2 rounded-lg border border-line px-4 py-2 text-sm disabled:opacity-50"><Circle size={14} className="fill-danger text-danger" /> Record clip</button>
            ) : (
              <button type="button" onClick={stopClip} className="flex items-center gap-2 rounded-lg bg-danger px-4 py-2 text-sm font-medium text-white"><Square size={14} /> Stop · {elapsed.toFixed(0)}s</button>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
