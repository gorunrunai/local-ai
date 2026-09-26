"""Image normalization: decode any common format, fix orientation, strip metadata, downscale."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageOps

try:  # HEIC/HEIF support (iPhone photos)
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

Image.MAX_IMAGE_PIXELS = 200_000_000  # guard against decompression bombs, allow big screenshots


@dataclass
class ImageInfo:
    path: str
    width: int
    height: int
    orig_width: int
    orig_height: int
    format: str
    source_format: str | None
    exif_stripped: bool

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_image(src: Path, dest_dir: Path, max_side: int, strip_exif: bool = True,
                    prefer_png: bool = False, stem: str = "image") -> ImageInfo:
    """Write a model-ready copy of `src` into dest_dir and return its info.

    * EXIF orientation is applied to pixels first, so stripping metadata never rotates.
    * With strip_exif, pixels are copied into a fresh image: no EXIF/GPS/XMP/ICC text survive.
    * PNG is kept for screenshots/graphics (sharp text); photos become JPEG q=90.
    """
    with Image.open(src) as im:
        source_format = im.format
        im.load()
        orig_w, orig_h = im.size
        im = ImageOps.exif_transpose(im)
        if getattr(im, "n_frames", 1) > 1:  # animated GIF/WebP: first frame
            im.seek(0)
        has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA" if has_alpha else "RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        if strip_exif:
            clean = Image.new(im.mode, im.size)  # fresh image: empty .info, no metadata
            clean.paste(im)
            im = clean
        use_png = prefer_png or has_alpha or source_format in ("PNG", "GIF", "BMP")
        dest_dir.mkdir(parents=True, exist_ok=True)
        if use_png:
            out = dest_dir / f"{stem}.png"
            im.save(out, format="PNG", optimize=False)
            fmt = "png"
        else:
            out = dest_dir / f"{stem}.jpg"
            im.convert("RGB").save(out, format="JPEG", quality=90, exif=b"" if strip_exif else
                                   im.info.get("exif", b""))
            fmt = "jpeg"
        return ImageInfo(path=str(out), width=im.size[0], height=im.size[1], orig_width=orig_w,
                         orig_height=orig_h, format=fmt, source_format=source_format,
                         exif_stripped=strip_exif)


def data_url(path: str | Path) -> str:
    p = Path(path)
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def image_part(path: str | Path) -> dict:
    return {"type": "image_url", "image_url": {"url": data_url(path)}}
