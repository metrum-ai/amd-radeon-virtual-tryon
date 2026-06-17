# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""HTTP client for the persistent FASHN try-on server.

Sends person frames to fashn_server instances over localhost HTTP
using base64-encoded JSON.  Validates each output against the
input to detect frames where FASHN did not apply the garment.
"""

import base64
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from PIL import Image

_FAIL_MAE_THRESHOLD = 10.0
_VALIDATE_SIZE = (256, 256)


def _assert_http_url(url: str) -> None:
    """Raise ValueError if *url* does not use http or https."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"URL must use http or https scheme, got {parsed.scheme!r}: {url}"
        )


def _validate_tryon(in_path: str, out_path: str) -> bool:
    """Return True if the try-on looks different from the input.

    Both images are resized to a fixed size so the comparison is
    robust to FASHN's internal resolution changes.

    Args:
        in_path: Original frame path.
        out_path: FASHN output frame path.

    Returns:
        True if the garment appears to have been applied.

    """
    inp = np.array(
        Image.open(in_path).convert("RGB").resize(_VALIDATE_SIZE),
        dtype=np.float32,
    )
    out = np.array(
        Image.open(out_path).convert("RGB").resize(_VALIDATE_SIZE),
        dtype=np.float32,
    )
    return float(np.mean(np.abs(inp - out))) >= _FAIL_MAE_THRESHOLD


def post_tryon(
    frame_path: str,
    garment_path: str,
    server_url: str,
    out_path: str,
    category: str = "tops",
    photo_type: str = "model",
    timesteps: int = 30,
    seed: int = 42,
    guidance_scale: float = 1.5,
    skip_cfg_last_n_steps: int = 1,
    segmentation_free: bool = True,
    preserve_identity: bool = True,
) -> str:
    """POST one frame to the FASHN server and save the result.

    Args:
        frame_path: Input person image path.
        garment_path: Garment image path.
        server_url: Base URL of the fashn-server instance.
        out_path: Where to save the PNG result.
        category: Garment category.
        photo_type: 'flat-lay' or 'model'.
        timesteps: Diffusion steps.
        seed: Random seed.
        guidance_scale: Classifier-free guidance strength.
        skip_cfg_last_n_steps: Final steps to run without CFG.
        segmentation_free: Whether to use FASHN maskless inference.
        preserve_identity: Restore original face/hair/hands after FASHN.

    Returns:
        ``out_path`` after saving.

    """
    with open(frame_path, "rb") as f:
        person_b64 = base64.b64encode(f.read()).decode()
    with open(garment_path, "rb") as f:
        garment_b64 = base64.b64encode(f.read()).decode()

    payload = json.dumps(
        {
            "person_b64": person_b64,
            "garment_b64": garment_b64,
            "category": category,
            "photo_type": photo_type,
            "timesteps": timesteps,
            "seed": seed,
            "guidance_scale": guidance_scale,
            "skip_cfg_last_n_steps": skip_cfg_last_n_steps,
            "segmentation_free": segmentation_free,
            "preserve_identity": preserve_identity,
        }
    ).encode()

    tryon_url = f"{server_url.rstrip('/')}/tryon"
    _assert_http_url(tryon_url)
    req = urllib.request.Request(
        tryon_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:  # nosec B310
        png_bytes = resp.read()

    Image.open(__import__("io").BytesIO(png_bytes)).save(out_path)
    return out_path


def _prepare_bottom_garment(
    garment_path: str,
    parser: "object",
    detected_pt: str,
    out_path: str,
) -> "tuple[str, str]":
    """Preprocess a bottom garment image, returning ``(photo_type, path)``.

    Two cases are handled:

    **Crop close-up** (>25 % of the image is mislabelled ``top``/``dress`` by the
    parser — typical of tight hip-to-thigh shorts shots): FASHN's
    ``photo_type='model'`` fails on such crops because DWPose cannot fit a
    full-body pose and the parser confuses the dominant skin region with upper
    clothing.  This extracts just the garment fabric (``pants``/``skirt``/
    ``belt`` core labels, optionally extended by colour proximity for
    non-skin-toned garments) and saves it on a neutral-grey background so FASHN
    receives a clean ``flat-lay``.

    **Full-body model shot with visible hands** (``arms`` label > 1 % of image):
    FASHN runs its own parser on the garment photo when ``photo_type='model'``;
    any visible hands/arms in the photo can be misidentified as part of the
    garment and bleed into the rendered result.  Masking those pixels to grey
    before sending eliminates the artefact while leaving the rest of the photo
    intact for FASHN's extraction.

    Skin-toned garments (camel, beige — garment median colour falls within the
    YCrCb skin range) cannot use colour proximity to recover mislabelled fabric
    (the garment and skin look the same to the filter).  For these, only the
    confirmed core label pixels are kept (with morphological closing to fill
    small intra-garment gaps).

    Args:
        garment_path: Bottom garment image path.
        parser: A loaded ``FashnHumanParser`` instance.
        detected_pt: Photo type from :func:`detect_garment_photo_type`.
        out_path: Temp path for the cleaned garment PNG (only written when
            preprocessing is actually applied).

    Returns:
        ``(photo_type, garment_path)`` — either the originals (unchanged) or
        ``('flat-lay', out_path)`` / ``(detected_pt, out_path)`` for the
        cleaned version.
    """
    if detected_pt == "flat-lay":
        return "flat-lay", garment_path
    try:
        import cv2
        import numpy as np
        from PIL import Image

        _BOTTOM_CORE = [5, 6, 7]   # skirt, pants, belt — confirmed garment labels
        _MIN_CORE_FRAC = 0.02      # below this → can't reliably anchor garment colour
        _COLOUR_DIST = 65.0        # max RGB distance to garment median for recovery
        _CROP_THRESHOLD = 0.25     # top/dress fraction above this → crop close-up
        _ARMS_THRESHOLD = 0.003    # arms fraction above this → hands worth masking

        g = np.array(Image.open(garment_path).convert("RGB"))
        seg = parser.predict(g)

        upper_clothing_frac = float(np.isin(seg, [3, 4]).mean())
        arms_px = seg == 12
        arms_frac = float(arms_px.mean())
        is_crop = upper_clothing_frac > _CROP_THRESHOLD
        has_significant_arms = arms_frac > _ARMS_THRESHOLD

        if not is_crop and not has_significant_arms:
            return detected_pt, garment_path  # nothing to fix

        core = np.isin(seg, _BOTTOM_CORE)

        if not is_crop:
            # Full-body shot: just mask hands/arms, keep model photo_type so
            # FASHN performs its own garment extraction on the cleaned image.
            clean = g.copy()
            clean[arms_px] = 127
            Image.fromarray(clean).save(out_path)
            return detected_pt, out_path

        # ── Crop case: extract garment fabric onto grey background ────────────

        if float(core.mean()) < _MIN_CORE_FRAC:
            # Almost no confirmed garment pixels — can't anchor colour.
            # At minimum, strip the visible hands and pass as flat-lay.
            clean = g.copy()
            clean[arms_px] = 127
            Image.fromarray(clean).save(out_path)
            return "flat-lay", out_path

        garment_colour = np.median(g[core].reshape(-1, 3), axis=0)
        colour_dist = np.linalg.norm(g.astype(np.float32) - garment_colour, axis=2)

        # YCrCb skin-tone heuristic.
        img_ycrcb = cv2.cvtColor(g, cv2.COLOR_RGB2YCrCb)
        cr = img_ycrcb[:, :, 1].astype(np.float32)
        cb = img_ycrcb[:, :, 2].astype(np.float32)
        is_skin = (cr >= 133) & (cr <= 173) & (cb >= 77) & (cb <= 127)

        # Try colour recovery first: select all pixels that are near the garment
        # colour AND not bare skin, then keep the largest connected component.
        # For skin-toned garments (camel, beige) this yields very little because
        # most garment pixels are classified as skin; for clearly non-skin
        # garments (rust, olive, dark colours) it recovers the full shorts
        # silhouette even when the parser has mislabelled the dominant region as
        # top/dress.  Checking coverage after the fact (≥5 %) is more reliable
        # than testing the garment median colour, which can sit at the edge of
        # the skin range and misclassify borderline hues like rust orange.
        attempt_mask = ((colour_dist < _COLOUR_DIST) & ~is_skin).astype(np.uint8) * 255
        attempt_mask = cv2.morphologyEx(attempt_mask, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
        num_a, lbl_a, stats_a, _ = cv2.connectedComponentsWithStats(attempt_mask, 8)
        if num_a > 1:
            lg_a = 1 + int(np.argmax(stats_a[1:, cv2.CC_STAT_AREA]))
            attempt_mask = np.where(lbl_a == lg_a, 255, 0).astype(np.uint8)
        attempt_frac = float((attempt_mask > 0).mean())

        if attempt_frac >= 0.05:
            # Sufficient non-skin coverage — use the colour-recovered mask.
            mask = attempt_mask
        else:
            # Very skin-toned garment (e.g. camel paperbag): colour recovery
            # yields < 5% non-skin coverage because the fabric and the
            # surrounding skin share the same YCrCb range.  Falling back to
            # confirmed core label pixels only (pants/skirt/belt) produces a
            # near-empty waistband sliver — FASHN has nothing useful to render.
            # Instead, strip only the background (label 0) and arms/hands
            # (label 12) and keep everything else: the shorts fabric, the
            # model's thighs, and any other non-background detail.  This gives
            # FASHN a complete lower-body reference image from which it can
            # infer the shorts style and colour, exactly as it does in
            # ``photo_type='model'`` mode when the crop detection is bypassed.
            bg_px = seg == 0
            full = (~bg_px & ~arms_px).astype(np.uint8) * 255
            full = cv2.morphologyEx(full, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
            num_f, lbl_f, stats_f, _ = cv2.connectedComponentsWithStats(full, 8)
            if num_f > 1:
                lg_f = 1 + int(np.argmax(stats_f[1:, cv2.CC_STAT_AREA]))
                full = np.where(lbl_f == lg_f, 255, 0).astype(np.uint8)
            mask = full

        # Arms/hands are never part of the garment.
        mask[arms_px] = 0

        # Keep only the largest connected component.
        num, lbl, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        if num > 1:
            largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            mask = np.where(lbl == largest, 255, 0).astype(np.uint8)

        m = mask > 0
        if not m.any():
            # Extraction produced nothing — fall back to hand-stripped raw image.
            clean = g.copy()
            clean[arms_px] = 127
            Image.fromarray(clean).save(out_path)
            return "flat-lay", out_path

        clean = g.copy()
        clean[~m] = 127
        Image.fromarray(clean).save(out_path)
        return "flat-lay", out_path

    except Exception:  # pylint: disable=broad-except
        return detected_pt, garment_path  # preserve original behaviour on error


def _clothing_silhouette_fallback(
    g: "np.ndarray",
    seg: "np.ndarray",
    out_path: str,
) -> "str | None":
    """Last-resort garment image for pale/low-cut tops the parser reads as skin.

    When the strict rebuild is skin-dominated — e.g. a cream spaghetti-strap
    cami whose low-cut bust the parser labels as skin and whose lower drape it
    labels ``skirt`` — falling back to ``photo_type='model'`` makes FASHN
    extract a skin blob and render a cropped bralette.  Instead, build the full
    upper-clothing silhouette (``top``/``dress``/``skirt``/``scarf``) and keep
    the largest connected component.  Because the silhouette is taken directly
    from the parser's clothing labels (no colour/skin filtering, which is what
    fails on cream-on-skin), it recovers the contiguous garment body.

    Interior-hole filling: body-hugging garments (slip dresses, fitted tops)
    sit flush against skin, so the parser may label the waist/mid-section as
    skin, creating a grey gap inside the garment silhouette.  FASHN interprets
    this gap as an intentional design cutout and renders a midriff-baring look.
    Flood-filling from a padded exterior corner identifies which grey pixels are
    genuinely outside the garment (exterior background) vs. enclosed within it
    (interior skin gaps).  Only interior gaps are filled with the garment colour,
    preserving real openings like necklines and side slits that are accessible
    from the image exterior.

    This runs ONLY after the strict rebuild has already been rejected, so it
    cannot change the output of garments that the strict path handles.

    Returns ``out_path``, or ``None`` if too little clothing is present to be
    useful (caller then falls back to ``photo_type='model'``).
    """
    import cv2
    import numpy as np
    from PIL import Image

    _MIN_SILHOUETTE_FRACTION = 0.06  # < this => no real garment, use 'model'

    sil = np.isin(seg, [3, 4, 5, 10])  # top, dress, skirt, scarf
    if float(sil.mean()) < _MIN_SILHOUETTE_FRACTION:
        return None

    mask = sil.astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))

    # Vertical gap bridging: body-hugging garments (slip dresses, fitted tops)
    # sit flush against skin-toned fabric, so the parser often labels the
    # waist / mid-section as background, leaving a grey stripe between the
    # bodice and skirt.  FASHN interprets this as a midriff cutout and renders
    # a two-piece look.  A tall, narrow morphological close (300 px tall, 5 px
    # wide) closes vertical gaps within each column without widening the shape
    # or closing lateral openings like V-necks or side slits — the V-neck is a
    # horizontal gap so the narrow kernel does not bridge it, while the slit is
    # connected only at the hem where the column still has no upper dress pixel.
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((300, 5), np.uint8))

    # Largest CC: drop stray disconnected patches added by the vertical close.
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if num > 1:
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        mask = np.where(labels == largest, 255, 0).astype(np.uint8)

    # Interior-hole fill: any grey region now enclosed by the bridged silhouette
    # (e.g. the neckline V after the vertical close connects the shoulder straps
    # to the bodice) is filled with the garment colour.
    h, w = mask.shape
    padded = np.pad(mask, 1, mode="constant", constant_values=0)
    cv2.floodFill(padded, np.zeros((h + 4, w + 4), np.uint8), (0, 0), 128)
    flood_interior = padded[1 : h + 1, 1 : w + 1]
    interior_holes = (mask == 0) & (flood_interior == 0)
    if interior_holes.any():
        garment_colour = np.median(g[mask > 0].reshape(-1, 3), axis=0)
        fill_colour = np.clip(garment_colour, 0, 255).astype(np.uint8)
        mask[interior_holes] = 255
        g = g.copy()
        g[interior_holes] = fill_colour

    clean = g.copy()
    clean[mask == 0] = 127
    Image.fromarray(clean).save(out_path)
    return out_path


def _prepare_garment_flatlay(
    garment_path: str,
    parser: "object",
    out_path: str,
) -> "str | None":
    """Rebuild a top garment as a clean flat-lay with sleeves intact.

    FASHN's ``create_garment_image`` keeps only ``top``/``dress``/``scarf``
    labelled pixels when ``photo_type='model'``.  The human parser labels
    puff, balloon, and off-shoulder sleeves as ``arms`` (skin), so those
    pixels are discarded and FASHN reproduces a cold-shoulder cutout that
    is not present in the source garment.  The ATR model also mislabels
    loose/oversized/flowy garment fabric as ``bag`` (large draped shapes
    confuse it), so those pixels are similarly lost.

    This rebuilds the garment as ``top``/``dress``/``scarf`` *plus* the
    genuinely-garment portions of the ``arms``/``bag`` (sleeves/loose
    fabric) and ``skirt``/``pants`` (long-top hem) regions, on a
    neutral-gray background, keeping only the largest connected component.
    A recovered pixel must be BOTH garment-coloured AND not bare skin — an
    earlier OR-skin rule swept whole skin regions (face/neck/arms) into the
    mask for pale/cream garments, producing a skin blob that FASHN rendered
    as a random generic top.  Two guards make the rebuild self-policing:
    if the confirmed garment core is negligible, or the final mask is still
    dominated by skin, the function returns ``None`` so the caller falls
    back to ``photo_type='model'`` (FASHN's own clean extraction) instead of
    feeding a garbage flat-lay.

    Args:
        garment_path: Source top garment image path.
        parser: A loaded ``FashnHumanParser`` instance.
        out_path: Where to write the rebuilt flat-lay garment PNG.

    Returns:
        ``out_path`` on success, or ``None`` if the garment region could not
        be segmented reliably (caller should fall back to ``photo_type=
        'model'`` with the original garment).

    """
    import cv2
    import numpy as np
    from PIL import Image

    _CORE_LABELS = [3, 4, 10]      # top, dress, scarf — confirmed garment fabric
    _MIN_CORE_FRACTION = 0.04      # below this the parser found ~no garment
    _COLOUR_DIST = 70.0            # max RGB distance to garment median to recover
    _MAX_SKIN_FRACTION = 0.35      # above this the rebuild is mostly skin -> unusable

    g = np.array(Image.open(garment_path).convert("RGB"))
    seg = parser.predict(g)

    # Confirmed garment fabric: top (3), dress (4), scarf (10).
    core = np.isin(seg, _CORE_LABELS)
    if float(core.mean()) < _MIN_CORE_FRACTION:
        # Tiny core — try the clothing silhouette before giving up to 'model'.
        return _clothing_silhouette_fallback(g, seg, out_path)

    garment_colour = np.median(g[core].reshape(-1, 3), axis=0)
    colour_dist = np.linalg.norm(g.astype(np.float32) - garment_colour, axis=2)

    # YCrCb skin-tone heuristic; bare skin is excluded from every recovery.
    img_ycrcb = cv2.cvtColor(g, cv2.COLOR_RGB2YCrCb)
    cr = img_ycrcb[:, :, 1].astype(np.float32)
    cb = img_ycrcb[:, :, 2].astype(np.float32)
    is_skin = (cr >= 133) & (cr <= 173) & (cb >= 77) & (cb <= 127)
    not_skin = ~is_skin
    near_colour = colour_dist < _COLOUR_DIST

    # Recover misclassified garment pixels from common mislabelling patterns,
    # but ONLY where the pixel is BOTH garment-coloured AND not bare skin:
    #   - 'arms' (12): puff/balloon/off-shoulder sleeve fabric.
    #   - 'bag' (8): loose/oversized/drapy fabric mistaken for a bag.
    sleeve_fabric = np.isin(seg, [12]) & near_colour & not_skin
    loose_fabric = np.isin(seg, [8]) & near_colour & not_skin

    # Lower hem of a long/fitted top mislabelled as 'skirt' (5) or 'pants' (6):
    # additionally require spatial adjacency to the core (21-px dilation) so the
    # model's actual trousers/skirt (physically below the garment) stay out.
    core_adjacent = cv2.dilate(core.astype(np.uint8) * 255, np.ones((21, 21), np.uint8)) > 0
    hem_fabric = np.isin(seg, [5, 6]) & near_colour & not_skin & core_adjacent

    mask = ((core | sleeve_fabric | loose_fabric | hem_fabric).astype(np.uint8)) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))

    # Keep only the largest connected component to drop stray mis-detections
    # (isolated background fragments, unconnected accessory blobs).
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if num > 1:
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        mask = np.where(labels == largest, 255, 0).astype(np.uint8)

    m = mask > 0
    if not m.any() or float(is_skin[m].mean()) > _MAX_SKIN_FRACTION:
        # Empty or skin-dominated strict rebuild (e.g. a low-cut cream cami the
        # parser reads as skin) — recover the full clothing silhouette instead
        # of handing FASHN a skin blob via the 'model' fallback.
        return _clothing_silhouette_fallback(g, seg, out_path)

    clean = g.copy()
    clean[~m] = 127
    Image.fromarray(clean).save(out_path)
    return out_path


def post_tryon_dual(
    frame_path: str,
    garment_top: str,
    garment_bottom: str,
    server_url: str,
    out_path: str,
    photo_type_top: str = "flat-lay",
    photo_type_bottom: str = "flat-lay",
    timesteps: int = 20,
    seed: int = 42,
    guidance_scale: float = 1.5,
    skip_cfg_last_n_steps: int = 1,
    parser: "object | None" = None,
) -> str:
    """Apply top garment then bottom garment via two sequential FASHN calls.

    Top-first ordering: Pass 1 applies the top garment to the original
    frame with ``segmentation_free=True`` (maskless) so the upper body is
    regenerated from the garment, fully replacing the original top — a
    masked pass would leave the original top visible through any opening in
    the new garment (V-necks, open shirts), bleeding a stray white band
    through.  Pass 2 applies the bottom garment with
    ``segmentation_free=False`` so only the lower body is masked and the
    freshly-applied top is preserved.  ``preserve_identity`` runs on the
    final pass only.

    Applying the top first (rather than the bottom) avoids long/loose
    garments overpainting an already-applied bottom, and lets the bottom
    pass cleanly mask only the lower body.

    The top garment is rebuilt as a clean flat-lay via
    :func:`_prepare_garment_flatlay` so puff/balloon/off-shoulder sleeves
    (which the parser mislabels as ``arms``) are not cut by FASHN's
    ``model`` garment extraction.  If that rebuild is unreliable the
    function falls back to ``photo_type='model'`` for the top.

    Args:
        frame_path: Input person image path.
        garment_top: Top garment image path.
        garment_bottom: Bottom garment image path.
        server_url: Base URL of the fashn-server instance.
        out_path: Where to save the final PNG result.
        photo_type_top: Photo type for the top garment.
        photo_type_bottom: Photo type for the bottom garment.
        timesteps: Diffusion steps per pass.
        seed: Random seed.
        guidance_scale: Classifier-free guidance strength.
        skip_cfg_last_n_steps: Final steps to run without CFG.
        parser: Optional pre-loaded ``FashnHumanParser`` instance.  When
            provided (e.g. reused across frames in a batch job) no ONNX
            session is loaded.  When ``None`` a new parser is created for
            this call only.

    Returns:
        ``out_path`` after saving.

    """
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        top_applied_path = tmp.name
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp2:
        garment_top_path = tmp2.name
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp3:
        garment_bottom_path = tmp3.name

    try:
        # Rebuild the top garment as a clean flat-lay so puff/balloon/
        # off-shoulder sleeves are preserved.  FASHN's 'model' garment
        # extraction discards sleeve fabric the parser labels as 'arms',
        # producing a cold-shoulder cutout; the rebuilt flat-lay keeps the
        # sleeves and is fed verbatim.  Falls back to 'model' photo type with
        # the original garment when the rebuild is unreliable (see
        # _prepare_garment_flatlay guards).  Skip rebuild when the garment is
        # already flat-lay: FASHN uses it verbatim and there is nothing to fix.
        from fashn_human_parser import FashnHumanParser

        _parser = parser if parser is not None else FashnHumanParser()
        garment_for_top = garment_top
        photo_type_for_top = photo_type_top
        if photo_type_top != "flat-lay":
            prepared = _prepare_garment_flatlay(
                garment_top, _parser, garment_top_path
            )
            if prepared is not None:
                garment_for_top = prepared
                photo_type_for_top = "flat-lay"

        # Pass 1: apply the top garment to the original frame.
        # segmentation_free=True (maskless) regenerates the upper body
        # conditioned on the garment, fully replacing the original top.  A
        # masked pass (segmentation_free=False) leaves the original garment
        # visible through any opening in the new top (V-necks, open shirts,
        # low necklines) — the demo person's white tee then bleeds through as
        # a stray white band.  Maskless avoids this; it is also what the
        # single-garment path uses, so the two paths render tops identically.
        # preserve_identity is deferred to the final pass.
        post_tryon(
            frame_path,
            garment_for_top,
            server_url,
            top_applied_path,
            category="tops",
            photo_type=photo_type_for_top,
            timesteps=timesteps,
            seed=seed,
            guidance_scale=guidance_scale,
            skip_cfg_last_n_steps=skip_cfg_last_n_steps,
            segmentation_free=True,
            preserve_identity=False,
        )

        # Preprocess the bottom garment: extract just the garment fabric for
        # crop close-ups (shorts) so hands and skin do not bleed into the
        # flat-lay reference, and mask arms/hands from full-body model shots
        # so FASHN's own extraction is not contaminated.
        effective_pt_bottom, effective_garment_bottom = _prepare_bottom_garment(
            garment_bottom, _parser, photo_type_bottom, garment_bottom_path
        )

        # Pass 2: apply the bottom garment to the top-applied intermediate.
        # segmentation_free=False masks only the lower body, so the freshly
        # applied top is left untouched.  preserve_identity=True restores
        # face/hair/hands on the final image.
        post_tryon(
            top_applied_path,
            effective_garment_bottom,
            server_url,
            out_path,
            category="bottoms",
            photo_type=effective_pt_bottom,
            timesteps=timesteps,
            seed=seed,
            guidance_scale=guidance_scale,
            skip_cfg_last_n_steps=skip_cfg_last_n_steps,
            segmentation_free=False,
            preserve_identity=True,
        )
    finally:
        for p in (top_applied_path, garment_top_path, garment_bottom_path):
            if os.path.exists(p):
                os.unlink(p)

    return out_path


def run_fashn_distributed(
    frames: list[Path],
    garment: str,
    category: str,
    photo_type: str,
    server_urls: list[str],
    out_dir: Path,
    timesteps: int = 20,
    seed: int = 42,
) -> list[Path]:
    """Distribute frames across multiple FASHN servers in parallel threads.

    Frames are round-robin assigned to servers.  Each server processes
    its slice sequentially (one request at a time per server).

    Args:
        frames: Input frame paths in order.
        garment: Garment image path.
        category: Garment category.
        photo_type: Photo type ('flat-lay' or 'model').
        server_urls: List of FASHN server base URLs.
        out_dir: Directory to save output frames.
        timesteps: Diffusion steps.
        seed: Random seed.

    Returns:
        Output frame paths in the same order as ``frames``.

    """
    import concurrent.futures
    import os
    import tempfile

    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(frames)
    n_srv = len(server_urls)
    results: dict[int, Path] = {}

    # For model-photo tops/one-pieces, rebuild a clean flat-lay once (the
    # garment is identical for every frame) so puff/balloon/long sleeves the
    # parser mislabels as 'arms' are not cut by FASHN's model extraction —
    # the same cold-shoulder fix used in the dual path.  Falls back to the
    # original garment with 'model' photo type when the rebuild is unreliable.
    # For bottoms, preprocess once: extract garment for crop close-ups and
    # mask arms/hands for full-body shots (see _prepare_bottom_garment).
    effective_garment = garment
    effective_photo_type = photo_type
    prepared_path: "str | None" = None
    if category in ("tops", "one-pieces") and photo_type != "flat-lay":
        from fashn_human_parser import FashnHumanParser

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as gf:
            prepared_path = gf.name
        prepared = _prepare_garment_flatlay(
            garment, FashnHumanParser(), prepared_path
        )
        if prepared is not None:
            effective_garment = prepared
            effective_photo_type = "flat-lay"
    elif category == "bottoms" and photo_type != "flat-lay":
        # Clean the bottom garment image: extract shorts fabric for tight crops
        # (avoids hands/skin bleeding into the flat-lay reference) and mask
        # visible hands for full-body model shots (FASHN's own parser may
        # otherwise include them in the garment extraction).
        from fashn_human_parser import FashnHumanParser

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as gf:
            prepared_path = gf.name
        effective_photo_type, eff_garment = _prepare_bottom_garment(
            garment, FashnHumanParser(), photo_type, prepared_path
        )
        if eff_garment != garment:
            effective_garment = eff_garment

    def _process_slice(url: str, idxs: list[int]) -> None:
        for idx in idxs:
            out_path = str(out_dir / f"frame_{idx:04d}.png")
            post_tryon(
                str(frames[idx]),
                effective_garment,
                url,
                out_path,
                category=category,
                photo_type=effective_photo_type,
                timesteps=timesteps,
                seed=seed,
            )
            results[idx] = Path(out_path)

    slices = [
        (server_urls[i], list(range(i, n, n_srv)))
        for i in range(n_srv)
        if list(range(i, n, n_srv))
    ]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(slices)) as pool:
            futs = [pool.submit(_process_slice, url, idxs) for url, idxs in slices]
            for fut in futs:
                fut.result()
    finally:
        if prepared_path is not None and os.path.exists(prepared_path):
            os.unlink(prepared_path)

    return [results[i] for i in range(n)]


def run_fashn_dual_distributed(
    frames: list[Path],
    garment_top: str,
    garment_bottom: str,
    photo_type_top: str,
    photo_type_bottom: str,
    server_urls: list[str],
    out_dir: Path,
    timesteps: int = 20,
    seed: int = 42,
) -> list[Path]:
    """Distribute dual-garment frames across multiple FASHN servers.

    Each server handles the full bottoms+tops sequence for its assigned
    frames so both passes share the same warm server connection.

    Args:
        frames: Input frame paths in order.
        garment_top: Top garment image path.
        garment_bottom: Bottom garment image path.
        photo_type_top: Photo type for the top garment.
        photo_type_bottom: Photo type for the bottom garment.
        server_urls: List of FASHN server base URLs.
        out_dir: Directory to save output frames.
        timesteps: Diffusion steps per pass.
        seed: Random seed.

    Returns:
        Output frame paths in the same order as ``frames``.

    """
    import concurrent.futures

    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(frames)
    n_srv = len(server_urls)
    results: dict[int, Path] = {}

    def _process_slice(url: str, idxs: list[int]) -> None:
        import os
        import tempfile

        from fashn_human_parser import FashnHumanParser

        # One ONNX session per worker thread — reused across all frames in
        # this slice so the model is not reloaded on every frame.
        worker_parser = FashnHumanParser()

        # Pre-prepare the garment flat-lay once per worker; the garment is
        # identical for every frame in the job.
        # Only run the parser for model-type garments — flat-lay images are
        # already clean product shots and must not be re-segmented (the parser
        # may still find partial "top" pixels on white backgrounds and would
        # fill the rest with grey, destroying the original garment).
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as gf:
            worker_garment_path = gf.name
        try:
            if photo_type_top != "flat-lay":
                prepared = _prepare_garment_flatlay(
                    garment_top, worker_parser, worker_garment_path
                )
                effective_garment = prepared if prepared is not None else garment_top
                effective_pt = "flat-lay" if prepared is not None else photo_type_top
            else:
                prepared = None
                effective_garment = garment_top
                effective_pt = "flat-lay"

            for idx in idxs:
                out_path = str(out_dir / f"frame_{idx:04d}.png")
                post_tryon_dual(
                    str(frames[idx]),
                    effective_garment,
                    garment_bottom,
                    url,
                    out_path,
                    photo_type_top=effective_pt,
                    photo_type_bottom=photo_type_bottom,
                    timesteps=timesteps,
                    seed=seed,
                    parser=worker_parser,
                )
                results[idx] = Path(out_path)
        finally:
            if os.path.exists(worker_garment_path):
                os.unlink(worker_garment_path)

    slices = [
        (server_urls[i], list(range(i, n, n_srv)))
        for i in range(n_srv)
        if list(range(i, n, n_srv))
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(slices)) as pool:
        futs = [pool.submit(_process_slice, url, idxs) for url, idxs in slices]
        for fut in futs:
            fut.result()

    return [results[i] for i in range(n)]


def wait_for_server(server_url: str, retries: int = 60) -> None:
    """Block until the server health endpoint responds.

    Args:
        server_url: Base URL of the fashn-server instance.
        retries: Number of 5-second attempts before raising.

    Raises:
        RuntimeError: If the server doesn't respond in time.

    """
    import time

    for i in range(retries):
        try:
            health_url = f"{server_url.rstrip('/')}/health"
            _assert_http_url(health_url)
            urllib.request.urlopen(health_url, timeout=5)  # nosec B310
            return
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            socket.timeout,
        ):
            if i == 0:
                print(f"  Waiting for {server_url} ...")
            time.sleep(5)
    raise RuntimeError(f"FASHN server at {server_url} did not become ready")
