# Copyright Advanced Micro Devices, Inc.
# 
# SPDX-License-Identifier: MIT

"""Pose-diverse frame selection from video using DWPose.

Stage 1: Extract uniformly-spaced candidate frames from a video.
Stage 2: Run DWPose on candidates and select maximally-diverse, sharp,
         well-posed frames that include both front and back views.
"""

import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(
    0,
    str(Path(__file__).parent.parent / "fashn-vton-1.5" / "src"),
)
from fashn_vton.dwpose.dwpose import DWposeDetector

# Face y-spread thresholds for orientation classification.
_FACE_STD_Y_FRONT = 0.016
_FACE_STD_Y_BACK = 0.010
_FACE_OFFSET_THRESHOLD = 0.25
_MIN_BODY_VIS = 0.40
_MIN_BODY_VIS_SIDE = 0.25  # side/profile frames expose fewer keypoints
_MAX_SIDE_FRAMES = 2
_KP_WEIGHTS = np.array(
    [
        1.0,
        1.5,
        2.0,
        1.5,
        1.0,
        2.0,
        1.5,
        1.0,
        0.8,
        0.4,
        0.2,
        0.8,
        0.4,
        0.2,
    ],
    dtype=np.float64,
)
_MIN_TEMPORAL_GAP_FRAC = 0.05
_MAX_BACK_FRAMES = 1


def extract_frames(
    video_path: str,
    num_frames: int,
    out_dir: Path,
) -> float:
    """Extract uniformly-spaced candidate frames from a video.

    Args:
        video_path: Path to the input video file.
        num_frames: Number of frames to extract.
        out_dir: Directory to save extracted frames.

    Returns:
        Video frame rate (fps).

    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    indices = np.linspace(0, total - 1, num_frames, dtype=int)
    for i, idx in enumerate(indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ret, frame = cap.read()
        if ret:
            cv2.imwrite(str(out_dir / f"frame_{i:04d}.png"), frame)
    cap.release()
    return fps


def _image_sharpness(img: np.ndarray) -> float:
    """Return Laplacian variance — higher means sharper / less motion-blur."""
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def _frame_features(
    pose: dict,
    img: np.ndarray,
) -> tuple[np.ndarray, str, float, float, float]:
    """Extract pose features and orientation label from a single frame.

    Args:
        pose: DWPose output dict with bodies/faces/hands.
        img: BGR image array for sharpness scoring.

    Returns:
        Tuple of (weighted_kp_vec, orientation, overall_vis, sharpness,
        face_std_y) where orientation is 'front', 'back', or 'side'.

    """
    kp = pose["bodies"]["candidate"].copy()
    visible = (kp >= 0).all(axis=1)
    overall_vis = float(visible.sum()) / max(1, len(visible))

    face_kps = pose.get("faces")
    if face_kps is not None and len(face_kps) > 0:
        fkp = np.array(face_kps)[0]
        face_std_y = float(np.std(fkp[:, 1]))
        face_cx = float(np.mean(fkp[:, 0]))
    else:
        face_std_y = 0.0
        face_cx = 0.5

    both_shoulders = visible[2] and visible[5]
    if face_std_y >= _FACE_STD_Y_FRONT:
        if both_shoulders:
            shldr_mid = float((kp[2, 0] + kp[5, 0]) / 2)
            shldr_w = float(abs(kp[2, 0] - kp[5, 0]))
            face_off = abs(face_cx - shldr_mid) / max(shldr_w, 0.01)
        else:
            face_off = 1.0
        orientation = "front" if face_off <= _FACE_OFFSET_THRESHOLD else "side"
    elif face_std_y < _FACE_STD_Y_BACK and both_shoulders:
        orientation = "back"
    else:
        orientation = "side"

    kp_norm = kp.copy()
    kp_norm[kp_norm < 0] = 0.0
    if visible[1] and visible[8] and visible[11]:
        anchor = kp_norm[1]
        scale = (
            float(np.linalg.norm((kp_norm[8] + kp_norm[11]) / 2.0 - anchor))
            or 1.0
        )
        kp_norm = (kp_norm - anchor) / scale
    elif visible[1]:
        kp_norm = kp_norm - kp_norm[1]
    kp_norm[~visible] = 0.0

    n_kp = min(len(_KP_WEIGHTS), len(kp_norm))
    weights_2d = np.ones_like(kp_norm)
    weights_2d[:n_kp] = _KP_WEIGHTS[:n_kp, np.newaxis]
    kp_weighted = (kp_norm * weights_2d).flatten()

    return (
        kp_weighted,
        orientation,
        overall_vis,
        _image_sharpness(img),
        face_std_y,
    )


def _weighted_pose_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Return L2 distance between two weighted keypoint vectors."""
    return float(np.linalg.norm(a - b))


def detect_garment_photo_type(
    garment_path: str,
    dwpose: DWposeDetector,
) -> str:
    """Return 'model' if garment image contains a person, else 'flat-lay'.

    Args:
        garment_path: Path to the garment image file.
        dwpose: Initialised DWposeDetector instance.

    Returns:
        ``'model'`` or ``'flat-lay'``.

    """
    img = cv2.imread(garment_path)
    if img is None:
        return "flat-lay"
    pose = dwpose(img)
    _, _, overall_vis, _, _ = _frame_features(pose, img)
    result = "model" if overall_vis >= 0.25 else "flat-lay"
    print(
        f"  [photo-type] {Path(garment_path).name} → "
        f"{result} (body_vis={overall_vis:.2f})"
    )
    return result


def select_diverse_frames(
    candidate_dir: Path,
    dwpose: DWposeDetector,
    num_select: int,
) -> tuple[list[Path], list[str]]:
    """Select pose-diverse, sharp frames including front and back views.

    Selection pipeline:
      1. Classify each frame as front, back, or side.
      2. Keep front + back frames with sufficient body visibility and
         sharpness; side/transitional frames are excluded.
      3. Greedy farthest-point sampling on weighted keypoint vectors
         picks maximally diverse poses.
      4. Cap back-facing frames to _MAX_BACK_FRAMES.

    Args:
        candidate_dir: Directory containing candidate frame PNG files.
        dwpose: Initialised DWposeDetector instance.
        num_select: Number of frames to select.

    Returns:
        Tuple of (selected_paths, orientations) where orientations is a
        parallel list of 'front', 'back', or 'side' strings.

    """
    frames = sorted(candidate_dir.glob("frame_*.png"))
    n_candidates = len(frames)
    print(f"  Detecting poses on {n_candidates} candidates ...")

    kp_list: list[np.ndarray] = []
    orientation_list: list[str] = []
    overall_vis_list: list[float] = []
    sharpness_list: list[float] = []
    valid_frames: list[Path] = []

    for f in frames:
        img = cv2.imread(str(f))
        if img is None:
            continue
        pose = dwpose(img)
        kv, orient, ov, sh, _ = _frame_features(pose, img)
        kp_list.append(kv)
        orientation_list.append(orient)
        overall_vis_list.append(ov)
        sharpness_list.append(sh)
        valid_frames.append(f)

    if not valid_frames:
        raise RuntimeError(f"No readable frames in {candidate_dir}")

    sh_arr = np.array(sharpness_list)
    sh_min, sh_max = sh_arr.min(), sh_arr.max()
    sh_norm = (
        (sh_arr - sh_min) / (sh_max - sh_min)
        if sh_max > sh_min
        else np.ones_like(sh_arr)
    )

    sharp_threshold = float(np.percentile(sh_arr, 15))

    def _vis_ok(i: int) -> bool:
        """Return True if the frame has sufficient body visibility."""
        threshold = (
            _MIN_BODY_VIS_SIDE
            if orientation_list[i] == "side"
            else _MIN_BODY_VIS
        )
        return overall_vis_list[i] >= threshold

    usable = [
        i
        for i in range(len(valid_frames))
        if _vis_ok(i) and sharpness_list[i] >= sharp_threshold
    ]
    if len(usable) < num_select:
        usable = [i for i in range(len(valid_frames)) if _vis_ok(i)]
    if not usable:
        usable = list(range(len(valid_frames)))

    n_front = sum(1 for i in usable if orientation_list[i] == "front")
    n_back = sum(1 for i in usable if orientation_list[i] == "back")
    n_side = sum(1 for i in usable if orientation_list[i] == "side")
    print(
        f"  Usable pool: {len(usable)}/{len(valid_frames)} frames "
        f"({n_front} front, {n_back} back, {n_side} side)"
    )

    num_select = min(num_select, len(usable))
    min_gap = max(1, int(n_candidates * _MIN_TEMPORAL_GAP_FRAC))

    selected_idx: list[int] = []
    selected_kp: list[np.ndarray] = []

    def _too_close(candidate_idx: int) -> bool:
        return any(abs(candidate_idx - c) < min_gap for c in selected_idx)

    def _best_score(pool: list[int], gap_check: bool) -> int:
        best_i, best_score = -1, -1.0
        for i in pool:
            if gap_check and _too_close(i):
                continue
            min_d = min(
                _weighted_pose_distance(kp_list[i], s) for s in selected_kp
            )
            score = (
                0.60 * min_d + 0.25 * sh_norm[i] + 0.15 * overall_vis_list[i]
            )
            if score > best_score:
                best_score, best_i = score, i
        return best_i

    front_pool = [i for i in usable if orientation_list[i] == "front"]
    seed_pool = front_pool if front_pool else usable
    seed_idx = max(
        seed_pool,
        key=lambda i: 0.5 * overall_vis_list[i] + 0.5 * sh_norm[i],
    )
    selected_idx = [seed_idx]
    selected_kp = [kp_list[seed_idx]]

    remaining = list(set(usable) - {seed_idx})
    while len(selected_idx) < num_select and remaining:
        best_i = _best_score(remaining, gap_check=True)
        if best_i == -1:
            best_i = _best_score(remaining, gap_check=False)
        if best_i == -1:
            break
        selected_idx.append(best_i)
        selected_kp.append(kp_list[best_i])
        remaining.remove(best_i)

    def _evict_excess(
        selected_set: set[int],
        excess: list[int],
        preferred_orientations: list[str],
    ) -> set[int]:
        """Evict excess frames and replace with preferred-orientation frames."""
        for bad in excess:
            selected_set.discard(bad)
            replacements = [
                i
                for i in usable
                if orientation_list[i] in preferred_orientations
                and i not in selected_set
                and not any(abs(i - c) < min_gap for c in selected_set)
            ]
            if not replacements:
                replacements = [
                    i
                    for i in usable
                    if orientation_list[i] in preferred_orientations
                    and i not in selected_set
                ]
            if replacements:
                best_r = max(
                    replacements,
                    key=lambda i: 0.5 * sh_norm[i] + 0.5 * overall_vis_list[i],
                )
                selected_set.add(best_r)
        return selected_set

    selected_set = set(selected_idx)

    back_selected = [i for i in selected_idx if orientation_list[i] == "back"]
    if len(back_selected) > _MAX_BACK_FRAMES:
        best_back = max(
            back_selected,
            key=lambda i: 0.5 * sh_norm[i] + 0.5 * overall_vis_list[i],
        )
        evict_back = [i for i in back_selected if i != best_back]
        selected_set = _evict_excess(
            selected_set, evict_back, ["front", "side"]
        )

    side_selected = [i for i in selected_set if orientation_list[i] == "side"]
    if len(side_selected) > _MAX_SIDE_FRAMES:
        keep_sides = sorted(
            side_selected,
            key=lambda i: 0.5 * sh_norm[i] + 0.5 * overall_vis_list[i],
            reverse=True,
        )[:_MAX_SIDE_FRAMES]
        evict_side = [i for i in side_selected if i not in keep_sides]
        selected_set = _evict_excess(selected_set, evict_side, ["front"])

    selected_idx = list(selected_set)

    selected_idx.sort()
    labels = [
        f"{valid_frames[i].name}({orientation_list[i][0]})"
        for i in selected_idx
    ]
    print(f"  Selected frames: {labels}")
    return (
        [valid_frames[i] for i in selected_idx],
        [orientation_list[i] for i in selected_idx],
    )


def save_keyframes(
    selected: list[Path],
    keyframe_dir: Path,
) -> list[Path]:
    """Copy selected frames to the job keyframe directory.

    Args:
        selected: Selected frame paths from the candidate pool.
        keyframe_dir: Destination directory for saved keyframes.

    Returns:
        List of saved keyframe paths (in order).

    """
    keyframe_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for i, src in enumerate(selected):
        dst = keyframe_dir / f"frame_{i:04d}.png"
        shutil.copy(src, dst)
        saved.append(dst)
    return saved
