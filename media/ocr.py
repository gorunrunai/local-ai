"""On-device OCR with Apple's Vision framework (VNRecognizeTextRequest) via PyObjC."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class OcrLine:
    text: str
    confidence: float
    x: float  # normalized, origin top-left
    y: float
    w: float
    h: float


@dataclass
class OcrResult:
    text: str
    lines: list[OcrLine] = field(default_factory=list)
    engine: str = "apple-vision"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> OcrResult:
        return cls(text=d["text"], lines=[OcrLine(**ln) for ln in d.get("lines", [])],
                   engine=d.get("engine", "apple-vision"))


def ocr_image(path: str | Path, languages: list[str] | None = None,
              language_correction: bool = False) -> OcrResult:
    """Recognize text; lines are returned in reading order (top-to-bottom, left-to-right).

    Language correction is off by default: it "fixes" code, paths and error messages.
    """
    import Vision
    from Foundation import NSURL

    url = NSURL.fileURLWithPath_(str(Path(path).resolve()))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(language_correction)
    if languages:
        req.setRecognitionLanguages_(languages)
    ok, err = handler.performRequests_error_([req], None)
    if not ok:
        raise RuntimeError(f"Vision OCR failed: {err}")
    lines: list[OcrLine] = []
    for obs in req.results() or []:
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        box = obs.boundingBox()  # normalized, origin bottom-left
        lines.append(OcrLine(text=str(cands[0].string()), confidence=float(cands[0].confidence()),
                             x=float(box.origin.x), y=float(1 - box.origin.y - box.size.height),
                             w=float(box.size.width), h=float(box.size.height)))
    return OcrResult(text=layout_text(lines), lines=lines)


def layout_text(lines: list[OcrLine]) -> str:
    """Group observations into visual rows so multi-column text keeps its structure."""
    rows: list[list[OcrLine]] = []
    for ln in sorted(lines, key=lambda l: (l.y, l.x)):
        if rows and abs(rows[-1][0].y - ln.y) < max(0.35 * ln.h, 0.004):
            rows[-1].append(ln)
        else:
            rows.append([ln])
    return "\n".join("    ".join(l.text for l in sorted(r, key=lambda l: l.x)) for r in rows)
