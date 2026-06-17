# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Crossfade slideshow assembly with styled tip card overlays.

Renders each try-on frame onto a branded canvas with an optional
floating styling-tip card, then encodes a crossfade slideshow via
ffmpeg piped rawvideo.
"""

import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from PIL import ImageFilter as _IF

# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

_FONT_PATHS: list[str] = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]


def _font(variant: str, size: int) -> ImageFont.FreeTypeFont:
    """Return a cached font by variant name and point size.

    Args:
        variant: One of ``'serif_bold'``, ``'serif'``, ``'sans_bold'``,
            or ``'sans'``.
        size: Point size.

    Returns:
        A ``FreeTypeFont`` instance.

    """
    key = (variant, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    prefs: dict[str, list[str]] = {
        "serif_bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        ],
        "serif": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        ],
        "sans_bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ],
        "sans": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ],
    }
    candidates = prefs.get(variant, []) + _FONT_PATHS
    for p in candidates:
        if Path(p).exists():
            f = ImageFont.truetype(p, size)
            _FONT_CACHE[key] = f
            return f
    f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _measure(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    """Return (width, height) of ``text`` rendered in ``font``."""
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


# ---------------------------------------------------------------------------
# Palette — dark editorial theme to match the dashboard dark UI
# ---------------------------------------------------------------------------

_BG_LEFT = (16, 14, 22)  # left rail — deep dark purple-black
_BG_RIGHT = (10, 9, 16)  # right rail — slightly deeper
_TEXT_MAIN = (230, 224, 240)  # near-white for masthead text
_TEXT_SUB = (140, 128, 160)  # muted lavender for subtitle
_BORDER = (50, 44, 64)  # subtle rail separator

# Tip-card accent colours (vivid enough to pop against dark canvas)
_ROSE_DEEP = (220, 100, 140)
_LAVEN_DEEP = (140, 110, 220)
_SAGE_DEEP = (80, 190, 140)

# Decorative geometry colours
_DECO_CIRCLE = (60, 50, 80)  # muted circle on left rail
_DECO_PILL = (28, 24, 38)  # pill shape on left rail
_DECO_RIGHT = (255, 255, 255)  # cross / dots on right rail

_CARD_ACCENTS = [_ROSE_DEEP, _LAVEN_DEEP, _SAGE_DEEP]

_CARD_POSITIONS = [
    None,
    "mid_right",
    "bottom_left",
    "top_right",
    "mid_left",
    "bottom_right",
]


def _infer_tip_category(tip: str) -> str:
    """Pick a category label from the tip text using keyword matching."""
    lower = tip.lower()
    if any(w in lower for w in ("layer", "blazer", "jacket", "coat")):
        return "LAYERING"
    if any(w in lower for w in ("pair", "match", "trousers", "jeans", "boot")):
        return "PAIRING"
    if any(w in lower for w in ("color", "colour", "hue", "tone", "shade")):
        return "COLOR"
    if any(w in lower for w in ("bag", "earring", "necklace", "belt", "watch")):
        return "ACCESSORY"
    if any(w in lower for w in ("texture", "fabric", "silk", "linen", "knit")):
        return "TEXTURE"
    if any(w in lower for w in ("office", "evening", "casual", "weekend")):
        return "OCCASION"
    if any(w in lower for w in ("tuck", "roll", "cuff", "hem")):
        return "FIT"
    return "STYLING"


def _render_floating_card(
    tip: str,
    category_label: str,
    accent: tuple[int, int, int],
    max_w: int,
) -> Image.Image:
    """Render a compact floating tip card as an RGBA image.

    Args:
        tip: Single tip string.
        category_label: Short category label (e.g. ``'LAYERING'``).
        accent: RGB accent colour for the category pill.
        max_w: Maximum card width in pixels.

    Returns:
        RGBA ``Image`` of the rendered card.

    """
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    pad = 14
    tip_sz = max(14, min(22, max_w // 12))
    lbl_sz = max(9, tip_sz // 2)
    tip_f = _font("sans_bold", tip_sz)
    lbl_f = _font("sans_bold", lbl_sz)
    text_w = max_w - pad * 2 - 30

    words = tip.split()
    lines: list[str] = []
    if words:
        cur = words[0]
        for w in words[1:]:
            trial = cur + " " + w
            if d0.textlength(trial, font=tip_f) <= text_w:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    if not lines:
        lines = [tip]

    line_h = _measure(d0, "Ag", tip_f)[1]
    lbl_h = _measure(d0, category_label, lbl_f)[1]
    pill_h = lbl_h + 8
    inner_h = pill_h + 8 + len(lines) * (line_h + 3) + pad * 2
    inner_w = min(
        max_w,
        max(
            int(d0.textlength(category_label, font=lbl_f)) + 20,
            max(int(d0.textlength(ln, font=tip_f)) for ln in lines) + 30,
        )
        + pad * 2,
    )

    buf = 8
    img = Image.new("RGBA", (inner_w + buf, inner_h + buf), (0, 0, 0, 0))

    shad = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shad)
    sd.rounded_rectangle(
        (buf // 2 + 2, buf // 2 + 3, inner_w + 2, inner_h + 3),
        radius=14,
        fill=(0, 0, 0, 22),
    )
    shad = shad.filter(_IF.GaussianBlur(5))
    img = Image.alpha_composite(img, shad)

    d = ImageDraw.Draw(img)
    bx, by = buf // 2, buf // 2

    d.rounded_rectangle(
        (bx, by, bx + inner_w, by + inner_h),
        radius=14,
        fill=(255, 255, 255, 240),
    )

    pill_w = int(d0.textlength(category_label, font=lbl_f)) + 16
    px, py = bx + pad, by + pad
    d.rounded_rectangle(
        (px, py, px + pill_w, py + pill_h),
        radius=pill_h // 2,
        fill=(*accent, 210),
    )
    d.text(
        (px + 8, py + 4),
        category_label,
        font=lbl_f,
        fill=(255, 255, 255, 255),
    )

    sx = bx + inner_w - pad - 10
    sy = by + pad + pill_h // 2
    spark_col = (*accent, 180)
    d.line((sx - 6, sy, sx + 6, sy), fill=spark_col, width=1)
    d.line((sx, sy - 6, sx, sy + 6), fill=spark_col, width=1)
    d.ellipse((sx - 2, sy - 2, sx + 2, sy + 2), fill=spark_col)
    for dx2, dy2 in [(-4, -4), (4, -4), (-4, 4), (4, 4)]:
        d.ellipse(
            (
                sx + dx2 - 1,
                sy + dy2 - 1,
                sx + dx2 + 1,
                sy + dy2 + 1,
            ),
            fill=spark_col,
        )

    ty = py + pill_h + 8
    for ln in lines:
        d.text((bx + pad, ty), ln, font=tip_f, fill=(30, 25, 40, 255))
        ty += line_h + 3

    return img


def render_frame_with_tips(
    frame_img: Image.Image,
    tips: list[str],
    category: str,
    slide_index: int = 0,
) -> Image.Image:
    """Compose a try-on frame onto a styled canvas with a tip card.

    Canvas height equals the source frame height (unchanged).  Canvas
    width is wider due to left/right rails.  Tip cards are positioned
    so they never overlap the subject's face (top ~20 % of frame).

    Args:
        frame_img: FASHN try-on output (any resolution).
        tips: Tip strings for this slide (one tip is shown per card).
        category: Garment category string (used for masthead label).
        slide_index: Zero-based slide number — controls card position.

    Returns:
        RGB ``Image`` of the composed frame ready for encoding.

    """
    src_w, src_h = frame_img.size
    # Slimmer rails keep the canvas closer to portrait and less wide.
    rail_l = max(72, src_w // 10)
    rail_r = max(56, src_w // 12)
    canvas_w = rail_l + src_w + rail_r
    canvas_h = src_h

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (*_BG_LEFT, 255))
    cd = ImageDraw.Draw(canvas)

    # ------------------------------------------------------------------
    # Left rail — dark background with decorative geometry
    # ------------------------------------------------------------------
    cd.rectangle((0, 0, rail_l, canvas_h), fill=_BG_LEFT)

    # Subtle glowing circle
    cr = int(rail_l * 0.55)
    cd.ellipse(
        (
            rail_l // 2 - cr,
            canvas_h // 6 - cr,
            rail_l // 2 + cr,
            canvas_h // 6 + cr,
        ),
        fill=(*_DECO_CIRCLE, 180),
    )
    # Pill shape
    pill_x, pill_y = 8, int(canvas_h * 0.45)
    pill_w2, pill_h2 = rail_l - 16, int(canvas_h * 0.15)
    cd.rounded_rectangle(
        (pill_x, pill_y, pill_x + pill_w2, pill_y + pill_h2),
        radius=pill_w2 // 2,
        fill=(*_DECO_PILL, 255),
        outline=(*_BORDER, 120),
        width=1,
    )

    # Thin separator between left rail and frame
    cd.rectangle((rail_l - 1, 0, rail_l, canvas_h), fill=(*_BORDER, 80))

    # ------------------------------------------------------------------
    # Right rail — slightly deeper dark
    # ------------------------------------------------------------------
    rx = rail_l + src_w
    cd.rectangle((rx, 0, canvas_w, canvas_h), fill=_BG_RIGHT)

    # Thin separator between frame and right rail
    cd.rectangle((rx, 0, rx + 1, canvas_h), fill=(*_BORDER, 80))

    cx_r = rx + rail_r // 2
    cy_r = int(canvas_h * 0.12)
    cross_col = (*_DECO_RIGHT, 40)
    cd.line((cx_r - 10, cy_r, cx_r + 10, cy_r), fill=cross_col, width=1)
    cd.line((cx_r, cy_r - 10, cx_r, cy_r + 10), fill=cross_col, width=1)

    dot_x = cx_r
    dot_start = int(canvas_h * 0.28)
    dot_end = int(canvas_h * 0.58)
    n_dots = 5
    for di in range(n_dots):
        dy = dot_start + di * (dot_end - dot_start) // max(1, n_dots - 1)
        d_r = 2
        cd.ellipse(
            (dot_x - d_r, dy - d_r, dot_x + d_r, dy + d_r),
            fill=(*_DECO_RIGHT, 50),
        )

    big_r = int(rail_r * 0.65)
    big_cx = rx + rail_r // 2
    big_cy = int(canvas_h * 0.78)
    cd.ellipse(
        (
            big_cx - big_r,
            big_cy - big_r,
            big_cx + big_r,
            big_cy + big_r,
        ),
        fill=(*_DECO_CIRCLE, 100),
    )

    # ------------------------------------------------------------------
    # Masthead text in left rail
    # ------------------------------------------------------------------
    mast_sz = max(14, rail_l // 5)
    sub_sz = max(7, rail_l // 10)
    mast_f = _font("serif_bold", mast_sz)
    sub_f = _font("serif", sub_sz)
    lbl_map = {
        "tops": "STYLE\nNOTES",
        "bottoms": "OUTFIT\nEDIT",
        "one-pieces": "LOOK\nBOOK",
        "both": "FULL\nLOOK",
    }
    ty = max(14, canvas_h // 30)
    for line in lbl_map.get(category, "STYLE\nNOTES").split("\n"):
        cd.text((8, ty), line, font=mast_f, fill=(*_TEXT_MAIN, 255))
        ty += mast_sz + 2
    cd.rectangle(
        (8, ty + 2, 8 + min(rail_l - 16, 60), ty + 3),
        fill=(*_ROSE_DEEP, 180),
    )
    cd.text((8, ty + 6), "AI TRY-ON", font=sub_f, fill=(*_TEXT_SUB, 200))

    # ------------------------------------------------------------------
    # Composite the try-on frame
    # ------------------------------------------------------------------
    canvas.alpha_composite(frame_img.convert("RGBA"), (rail_l, 0))

    # ------------------------------------------------------------------
    # Tip card
    # ------------------------------------------------------------------
    pos_key = _CARD_POSITIONS[slide_index % len(_CARD_POSITIONS)]
    if pos_key is not None and tips:
        tip_text = tips[slide_index % len(tips)]
        cat_label = _infer_tip_category(tip_text)
        accent = _CARD_ACCENTS[slide_index % len(_CARD_ACCENTS)]

        max_card_w = min(int(src_w * 0.45), 280)
        card = _render_floating_card(tip_text, cat_label, accent, max_card_w)

        margin = max(10, src_w // 32)
        face_safe_y = max(margin, int(src_h * 0.22)) + card.height

        if pos_key == "top_right":
            cx = rail_l + src_w - card.width - margin
            cy = face_safe_y
        elif pos_key == "mid_right":
            cx = rail_l + src_w - card.width - margin
            cy = (src_h - card.height) // 2
        elif pos_key == "bottom_left":
            cx = rail_l + margin
            cy = src_h - card.height - margin
        elif pos_key == "mid_left":
            cx = rail_l + margin
            cy = (src_h - card.height) // 2
        elif pos_key == "bottom_right":
            cx = rail_l + src_w - card.width - margin
            cy = src_h - card.height - margin
        else:
            cx = rail_l + src_w - card.width - margin
            cy = (src_h - card.height) // 3

        canvas.alpha_composite(card, (cx, cy))

    return canvas.convert("RGB")


def _crossfade(a: np.ndarray, b: np.ndarray, n: int) -> list[np.ndarray]:
    """Return ``n`` blend frames linearly interpolating between ``a`` and ``b``.

    Args:
        a: First frame as BGR uint8 array.
        b: Second frame as BGR uint8 array.
        n: Number of intermediate frames.

    Returns:
        List of ``n`` blended BGR uint8 arrays.

    """
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    alphas = np.linspace(0.0, 1.0, n + 2)[1:-1]
    return [(a * (1.0 - t) + b * t).astype(np.uint8) for t in alphas]


def _resize_cover(
    frame: np.ndarray, target_size: tuple[int, int]
) -> np.ndarray:
    """Resize without distortion, then center-crop to target size.

    Args:
        frame: BGR frame array.
        target_size: Target ``(width, height)``.

    Returns:
        BGR frame with exactly ``target_size`` dimensions.

    """
    target_w, target_h = target_size
    src_h, src_w = frame.shape[:2]
    scale = max(target_w / src_w, target_h / src_h)
    resized_w = max(target_w, int(round(src_w * scale)))
    resized_h = max(target_h, int(round(src_h * scale)))
    resized = cv2.resize(
        frame,
        (resized_w, resized_h),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
    )
    x0 = max(0, (resized_w - target_w) // 2)
    y0 = max(0, (resized_h - target_h) // 2)
    return resized[y0 : y0 + target_h, x0 : x0 + target_w]


def slideshow_duration_secs(num_frames: int, frame_duration: float) -> int:
    """Estimate total slideshow duration in whole seconds.

    Args:
        num_frames: Number of slides.
        frame_duration: Seconds each slide is held.

    Returns:
        Estimated duration in seconds (rounded up + 2s buffer).

    """
    return int(num_frames * frame_duration + max(0, num_frames - 1) * 0.5) + 2


def assemble_slideshow(
    tryon_paths: list[Path],
    category: str,
    tips_by_frame: list[list[str]],
    frame_duration: float,
    fps: float,
    out_path: str,
    target_size: tuple[int, int] | None = None,
) -> None:
    """Assemble crossfade slideshow encoded with ffmpeg H.264 (CRF 18).

    Args:
        tryon_paths: Ordered list of FASHN output frame paths.
        category: Garment category (used for masthead label).
        tips_by_frame: Per-frame tip strings; wrapped if shorter than frames.
        frame_duration: Seconds to hold each slide.
        fps: Output video frame rate.
        out_path: Output MP4 file path.
        target_size: Optional ``(width, height)`` for the encoded video.

    Raises:
        RuntimeError: If ffmpeg encoding fails.

    """
    fps_i = max(1, int(round(fps)))
    frames_per_slide = max(1, int(frame_duration * fps_i))
    transition_n = max(1, int(0.5 * fps_i))

    cards_raw: list[np.ndarray] = []
    for i, p in enumerate(tryon_paths):
        tips = tips_by_frame[i] if i < len(tips_by_frame) else tips_by_frame[-1]
        annotated = render_frame_with_tips(
            Image.open(str(p)).convert("RGB"),
            tips,
            category,
            slide_index=i,
        )
        card = np.array(annotated)[:, :, ::-1].copy()
        if target_size is not None:
            card = _resize_cover(card, target_size)
        cards_raw.append(card)

    h, w = cards_raw[0].shape[:2]
    cards: list[np.ndarray] = [
        cv2.resize(c, (w, h)) if c.shape[:2] != (h, w) else c for c in cards_raw
    ]

    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{w}x{h}",
        "-r",
        str(fps_i),
        "-i",
        "pipe:0",
        "-vcodec",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "slow",
        "-pix_fmt",
        "yuv420p",
        out_path,
    ]
    proc = subprocess.Popen(
        ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    if proc.stdin is None:
        proc.terminate()
        raise RuntimeError("ffmpeg stdin pipe unavailable — Popen failed to open stdin")

    for i, card in enumerate(cards):
        for _ in range(frames_per_slide):
            proc.stdin.write(card.tobytes())
        if i < len(cards) - 1:
            for blended in _crossfade(card, cards[i + 1], transition_n):
                proc.stdin.write(blended.tobytes())

    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg slideshow encoding failed")
