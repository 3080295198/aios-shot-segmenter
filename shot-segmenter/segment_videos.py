from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpeg", ".mpg"}


@dataclass
class DetectionParameters:
    scene_threshold: float = 0.20
    adaptive_floor: float = 0.14
    adaptive_delta: float = 0.045
    adaptive_mad_factor: float = 3.5
    merge_gap_seconds: float = 0.18
    edge_ignore_seconds: float = 0.20
    flash_similarity_threshold: float = 0.20
    analysis_width: int = 180


@dataclass
class Fingerprint:
    histogram: np.ndarray
    hash_bits: np.ndarray
    thumbnail: np.ndarray


@dataclass
class FrameScore:
    frame_index: int
    time_seconds: float
    combined: float
    histogram: float
    hash_distance: float
    pixel_distance: float
    local_median: float
    local_threshold: float
    method: str


@dataclass
class Shot:
    video_id: str
    shot_index: int
    start_frame: int
    end_frame: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    preview_frame: int
    preview_time_seconds: float
    preview_file: str
    boundary_image: str | None
    boundary_score: float | None
    boundary_method: str | None
    confidence: str
    needs_review: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch segment videos into shots with timestamps and preview images."
    )
    parser.add_argument(
        "input",
        help="A video file or a directory containing videos.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="shot-results",
        help="Output directory (default: shot-results).",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively discover videos when input is a directory.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing per-video result directories.",
    )
    parser.add_argument(
        "--scene-threshold",
        type=float,
        default=DetectionParameters.scene_threshold,
        help="Fixed cut-score threshold (default: 0.20).",
    )
    parser.add_argument(
        "--adaptive-floor",
        type=float,
        default=DetectionParameters.adaptive_floor,
        help="Minimum score for adaptive detection (default: 0.14).",
    )
    parser.add_argument(
        "--merge-gap",
        type=float,
        default=DetectionParameters.merge_gap_seconds,
        help="Merge detections closer than this many seconds (default: 0.18).",
    )
    parser.add_argument(
        "--ignore-edge",
        type=float,
        default=DetectionParameters.edge_ignore_seconds,
        help="Ignore cuts this close to video start/end (default: 0.20).",
    )
    parser.add_argument(
        "--analysis-width",
        type=int,
        default=DetectionParameters.analysis_width,
        help="Frame width used for scoring; aspect ratio is preserved (default: 180).",
    )
    parser.add_argument(
        "--ffprobe",
        default="ffprobe",
        help="ffprobe executable name or absolute path.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print one summary line per video and the final summary.",
    )
    return parser.parse_args()


def validate_parameters(parameters: DetectionParameters) -> None:
    if not 0 < parameters.scene_threshold < 1:
        raise ValueError("scene_threshold must be between 0 and 1")
    if not 0 < parameters.adaptive_floor < 1:
        raise ValueError("adaptive_floor must be between 0 and 1")
    if parameters.adaptive_floor >= parameters.scene_threshold:
        raise ValueError("adaptive_floor must be lower than scene_threshold")
    if parameters.merge_gap_seconds <= 0:
        raise ValueError("merge_gap_seconds must be positive")
    if parameters.analysis_width < 64:
        raise ValueError("analysis_width must be at least 64")


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return slug or "video"


def unique_video_id(path: Path, used: set[str]) -> str:
    base = safe_slug(path.stem)
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def discover_videos(source: Path, recursive: bool) -> list[Path]:
    if source.is_file():
        return [source]
    if not source.is_dir():
        raise FileNotFoundError(f"Input does not exist: {source}")

    iterator: Iterable[Path]
    if recursive:
        iterator = source.rglob("*")
    else:
        iterator = source.iterdir()
    return sorted(
        (
            path
            for path in iterator
            if path.is_file()
            and path.suffix.casefold() in VIDEO_EXTENSIONS
            and not path.name.startswith(".")
        ),
        key=lambda path: str(path).casefold(),
    )


def run_ffprobe(path: Path, executable: str) -> dict[str, Any]:
    command = [
        executable,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,nb_frames,duration",
        "-show_entries",
        "format=format_name,duration,size",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffprobe was not found. Install FFmpeg or pass --ffprobe PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffprobe timed out while reading metadata") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or "unknown ffprobe error"
        raise RuntimeError(f"ffprobe failed: {detail[:500]}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe returned invalid JSON") from exc

    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError("No video stream found")
    stream = streams[0]
    def parse_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    width = parse_int(stream.get("width"))
    height = parse_int(stream.get("height"))
    if width <= 0 or height <= 0:
        raise RuntimeError("Invalid video dimensions")

    fps_text = "0/0"
    fps = 0.0
    for candidate in (stream.get("avg_frame_rate"), stream.get("r_frame_rate")):
        if not candidate or candidate == "0/0":
            continue
        try:
            numerator, denominator = str(candidate).split("/", 1)
            parsed_fps = float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(parsed_fps) and parsed_fps > 0:
            fps = parsed_fps
            fps_text = str(candidate)
            break
    if not math.isfinite(fps) or fps <= 0:
        raise RuntimeError(f"Unsupported or invalid frame rate: {fps_text}")

    format_data = payload.get("format") or {}
    duration_text = stream.get("duration") or format_data.get("duration")
    try:
        duration = float(duration_text)
    except (TypeError, ValueError):
        duration = 0.0

    return {
        "codec": stream.get("codec_name"),
        "width": width,
        "height": height,
        "fps": fps,
        "fps_text": fps_text,
        "nb_frames": parse_int(stream.get("nb_frames")),
        "container_duration_seconds": duration,
        "format": format_data.get("format_name"),
        "file_size_bytes": path.stat().st_size,
    }


def make_fingerprint(frame: np.ndarray, target_width: int) -> Fingerprint:
    source_height, source_width = frame.shape[:2]
    target_height = max(
        32,
        int(round(target_width * source_height / source_width / 2.0) * 2),
    )
    small = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256]).flatten()
    histogram = histogram.astype(np.float32)
    histogram /= max(float(histogram.sum()), 1e-9)

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    hash_source = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    hash_bits = (hash_source[:, 1:] > hash_source[:, :-1]).flatten()
    thumbnail = cv2.resize(gray, (36, 64), interpolation=cv2.INTER_AREA)
    return Fingerprint(
        histogram=histogram,
        hash_bits=hash_bits,
        thumbnail=thumbnail,
    )


def compare_fingerprints(left: Fingerprint, right: Fingerprint) -> tuple[float, float, float, float]:
    histogram = 1.0 - float(np.minimum(left.histogram, right.histogram).sum())
    hash_distance = float(np.mean(left.hash_bits != right.hash_bits))
    pixel_distance = float(
        np.mean(
            np.abs(
                left.thumbnail.astype(np.float32)
                - right.thumbnail.astype(np.float32)
            )
        )
        / 255.0
    )
    combined = 0.60 * histogram + 0.20 * hash_distance + 0.20 * pixel_distance
    return combined, histogram, hash_distance, pixel_distance


def robust_local_stats(
    scores: list[dict[str, float]],
    index: int,
    radius: int = 20,
    exclusion: int = 3,
) -> tuple[float, float]:
    start = max(0, index - radius)
    end = min(len(scores), index + radius + 1)
    values = [
        scores[position]["combined"]
        for position in range(start, end)
        if abs(position - index) > exclusion
    ]
    if not values:
        return scores[index]["combined"], 0.0
    array = np.asarray(values, dtype=np.float32)
    median = float(np.median(array))
    mad = float(np.median(np.abs(array - median)))
    return median, mad


def find_candidates(
    scores: list[dict[str, float]],
    fps: float,
    total_frames: int,
    parameters: DetectionParameters,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    edge_frames = int(round(parameters.edge_ignore_seconds * fps))
    for index in range(2, len(scores) - 2):
        frame_index = index + 1
        if frame_index <= edge_frames or total_frames - frame_index <= edge_frames:
            continue

        score = scores[index]
        local_median, local_mad = robust_local_stats(scores, index)
        local_threshold = max(
            parameters.adaptive_floor,
            local_median
            + parameters.adaptive_delta
            + parameters.adaptive_mad_factor * local_mad,
        )
        evidence = score["histogram"] >= 0.10 or (
            score["hash"] >= 0.34 and score["pixel"] >= 0.12
        )
        local_window = scores[index - 2 : index + 3]
        is_peak = score["combined"] >= max(item["combined"] for item in local_window)

        fixed_cut = score["combined"] >= parameters.scene_threshold
        adaptive_cut = (
            score["combined"] >= local_threshold
            and score["combined"] >= parameters.adaptive_floor
            and evidence
        )
        if is_peak and (fixed_cut or adaptive_cut):
            candidates.append(
                {
                    **score,
                    "frame_index": frame_index,
                    "time_seconds": frame_index / fps,
                    "local_median": local_median,
                    "local_threshold": local_threshold,
                    "method": "fixed" if fixed_cut else "adaptive",
                }
            )
    return candidates


def boundary_is_transient(
    fingerprints: list[Fingerprint],
    first_frame: int,
    last_frame: int,
    threshold: float,
) -> bool:
    before = max(0, first_frame - 1)
    after = min(len(fingerprints) - 1, last_frame)
    if before == after:
        return True
    combined, _, _, _ = compare_fingerprints(fingerprints[before], fingerprints[after])
    return combined < threshold


def merge_candidates(
    candidates: list[dict[str, Any]],
    fingerprints: list[Fingerprint],
    fps: float,
    parameters: DetectionParameters,
) -> list[dict[str, Any]]:
    if not candidates:
        return []

    gap_frames = max(1, int(round(parameters.merge_gap_seconds * fps)))
    clusters: list[list[dict[str, Any]]] = []
    for candidate in sorted(candidates, key=lambda item: item["frame_index"]):
        if (
            clusters
            and candidate["frame_index"] - clusters[-1][-1]["frame_index"] <= gap_frames
        ):
            clusters[-1].append(candidate)
        else:
            clusters.append([candidate])

    accepted: list[dict[str, Any]] = []
    for cluster in clusters:
        first_frame = int(cluster[0]["frame_index"])
        last_frame = int(cluster[-1]["frame_index"])
        is_transient = len(cluster) > 1 and boundary_is_transient(
            fingerprints,
            first_frame,
            last_frame,
            parameters.flash_similarity_threshold,
        )
        if is_transient:
            continue
        best = max(cluster, key=lambda item: item["combined"])
        best = dict(best)
        if len(cluster) > 1:
            best["method"] = f"{best['method']}-merged"
        accepted.append(best)
    return accepted


def confidence_for(boundary_score: float, method: str) -> str:
    if boundary_score >= 0.42:
        return "high"
    if boundary_score >= 0.22:
        return "medium"
    if "adaptive" in method:
        return "low"
    return "medium"


def detect_video(
    path: Path,
    metadata: dict[str, Any],
    parameters: DetectionParameters,
) -> tuple[list[Fingerprint], list[dict[str, float]], list[dict[str, Any]], int]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("OpenCV could not open the video")

    fingerprints: list[Fingerprint] = []
    scores: list[dict[str, float]] = []
    previous: Fingerprint | None = None
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            current = make_fingerprint(frame, parameters.analysis_width)
            if previous is not None:
                combined, histogram, hash_distance, pixel_distance = compare_fingerprints(
                    previous, current
                )
                scores.append(
                    {
                        "combined": combined,
                        "histogram": histogram,
                        "hash": hash_distance,
                        "pixel": pixel_distance,
                    }
                )
            fingerprints.append(current)
            previous = current
    finally:
        cap.release()

    frame_count = len(fingerprints)
    if frame_count == 0:
        raise RuntimeError("The video contains no decodable frames")
    if frame_count == 1:
        return fingerprints, scores, [], frame_count

    expected_frames = metadata.get("nb_frames", 0)
    if expected_frames and abs(frame_count - expected_frames) > 2:
        print(
            f"warning: metadata reports {expected_frames} frames, decoded {frame_count}",
            file=sys.stderr,
        )

    candidates = find_candidates(scores, metadata["fps"], frame_count, parameters)
    boundaries = merge_candidates(candidates, fingerprints, metadata["fps"], parameters)
    return fingerprints, scores, boundaries, frame_count


def shot_filename(index: int) -> str:
    return f"shot_{index:04d}.jpg"


def boundary_filename(index: int) -> str:
    return f"boundary_{index:04d}.jpg"


def add_label(
    image: np.ndarray,
    text: str,
    target_width: int | None = None,
) -> np.ndarray:
    height, width = image.shape[:2]
    if target_width is not None and width != target_width:
        scale = target_width / width
        image = cv2.resize(
            image,
            (target_width, int(round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        height, width = image.shape[:2]
    canvas = np.zeros((height + 32, width, 3), dtype=np.uint8)
    canvas[32:] = image
    cv2.putText(
        canvas,
        text,
        (8, 22),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return canvas


def save_shot_frames(
    path: Path,
    video_dir: Path,
    fps: float,
    frame_count: int,
    boundary_frames: list[int],
    shot_rows: list[dict[str, Any]],
) -> None:
    preview_dir = video_dir / "previews"
    boundary_dir = video_dir / "boundaries"
    preview_dir.mkdir(parents=True, exist_ok=True)
    boundary_dir.mkdir(parents=True, exist_ok=True)

    targets: dict[int, list[tuple[str, Path]]] = {}
    for row in shot_rows:
        targets.setdefault(row["preview_frame"], []).append(
            ("preview", video_dir / row["preview_file"])
        )
    for boundary_index, frame_index in enumerate(boundary_frames, start=1):
        before = max(0, frame_index - 1)
        after = min(frame_count - 1, frame_index)
        targets.setdefault(before, []).append(
            ("boundary_before", boundary_dir / f".{boundary_index:04d}_before.jpg")
        )
        targets.setdefault(after, []).append(
            ("boundary_after", boundary_dir / f".{boundary_index:04d}_after.jpg")
        )

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("OpenCV could not reopen the video for preview extraction")
    decoded = 0
    try:
        frame_index = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            for kind, destination in targets.get(frame_index, []):
                destination.parent.mkdir(parents=True, exist_ok=True)
                ok_write, encoded = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 94]
                )
                if not ok_write:
                    raise RuntimeError(f"Failed to encode {kind} at frame {frame_index}")
                destination.write_bytes(encoded.tobytes())
            frame_index += 1
        decoded = frame_index
    finally:
        cap.release()

    if decoded < frame_count:
        raise RuntimeError(
            f"Preview pass decoded {decoded} of {frame_count} frames"
        )

    for boundary_index in range(1, len(boundary_frames) + 1):
        before_path = boundary_dir / f".{boundary_index:04d}_before.jpg"
        after_path = boundary_dir / f".{boundary_index:04d}_after.jpg"
        before = cv2.imread(str(before_path))
        after = cv2.imread(str(after_path))
        if before is None or after is None:
            raise RuntimeError(
                f"Could not create boundary evidence for boundary {boundary_index}"
            )
        before_time = max(0, boundary_frames[boundary_index - 1] - 1) / fps
        after_time = min(frame_count - 1, boundary_frames[boundary_index - 1]) / fps
        before = add_label(before, f"BEFORE  {before_time:07.2f}s")
        after = add_label(after, f"AFTER   {after_time:07.2f}s")
        combined = np.hstack([before, after])
        output_path = boundary_dir / boundary_filename(boundary_index)
        if not cv2.imwrite(
            str(output_path), combined, [cv2.IMWRITE_JPEG_QUALITY, 92]
        ):
            raise RuntimeError(f"Could not write {output_path}")
        before_path.unlink(missing_ok=True)
        after_path.unlink(missing_ok=True)


def create_contact_sheet(video_dir: Path, shot_rows: list[dict[str, Any]]) -> str:
    thumb_width = 135
    thumb_height = 240
    label_height = 24
    columns = 6
    rows = max(1, math.ceil(len(shot_rows) / columns))
    sheet = np.full(
        (
            rows * (thumb_height + label_height + 8) + 8,
            columns * (thumb_width + 8) + 8,
            3,
        ),
        24,
        dtype=np.uint8,
    )
    for position, shot in enumerate(shot_rows):
        image = cv2.imread(str(video_dir / shot["preview_file"]))
        if image is None:
            raise RuntimeError(f"Preview is unreadable: {shot['preview_file']}")
        image = cv2.resize(
            image, (thumb_width, thumb_height), interpolation=cv2.INTER_AREA
        )
        row, column = divmod(position, columns)
        x = 8 + column * (thumb_width + 8)
        y = 8 + row * (thumb_height + label_height + 8)
        sheet[y : y + thumb_height, x : x + thumb_width] = image
        label = f"{shot['shot_index']:02d}  {shot['start_seconds']:6.2f}-{shot['end_seconds']:6.2f}s"
        cv2.putText(
            sheet,
            label,
            (x + 2, y + thumb_height + 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.43,
            (235, 235, 235),
            1,
            cv2.LINE_AA,
        )
    output = video_dir / "contact-sheet.jpg"
    if not cv2.imwrite(str(output), sheet, [cv2.IMWRITE_JPEG_QUALITY, 91]):
        raise RuntimeError("Could not write contact sheet")
    return output.name


def build_shots(
    video_id: str,
    video_dir: Path,
    fps: float,
    frame_count: int,
    boundaries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[int]]:
    boundary_frames = [int(item["frame_index"]) for item in boundaries]
    starts = [0] + boundary_frames
    ends = [frame - 1 for frame in boundary_frames] + [frame_count - 1]
    timeline_ends = boundary_frames + [frame_count]
    shot_rows: list[dict[str, Any]] = []

    for shot_index, (start_frame, end_frame, timeline_end) in enumerate(
        zip(starts, ends, timeline_ends), start=1
    ):
        boundary = boundaries[shot_index - 2] if shot_index > 1 else None
        start_seconds = start_frame / fps
        # Time intervals are half-open: [start, next boundary). Frame ranges
        # remain inclusive so preview extraction can address real frames.
        end_seconds = min(frame_count / fps, timeline_end / fps)
        preview_frame = int(round((start_frame + end_frame) / 2))
        preview_time = preview_frame / fps
        preview_file = f"previews/{shot_filename(shot_index)}"
        boundary_image = (
            f"boundaries/{boundary_filename(shot_index - 1)}"
            if boundary is not None
            else None
        )
        boundary_score = float(boundary["combined"]) if boundary else None
        boundary_method = str(boundary["method"]) if boundary else None
        duration = max(0.0, end_seconds - start_seconds)
        confidence = (
            confidence_for(boundary_score, boundary_method)
            if boundary is not None
            else "high"
        )
        shot_rows.append(
            asdict(
                Shot(
                    video_id=video_id,
                    shot_index=shot_index,
                    start_frame=start_frame,
                    end_frame=end_frame,
                    start_seconds=round(start_seconds, 6),
                    end_seconds=round(end_seconds, 6),
                    duration_seconds=round(duration, 6),
                    preview_frame=preview_frame,
                    preview_time_seconds=round(preview_time, 6),
                    preview_file=preview_file,
                    boundary_image=boundary_image,
                    boundary_score=round(boundary_score, 6)
                    if boundary_score is not None
                    else None,
                    boundary_method=boundary_method,
                    confidence=confidence,
                    needs_review=confidence == "low" or duration < 0.45,
                )
            )
        )
    return shot_rows, boundary_frames


def verify_video_result(
    video_dir: Path,
    fps: float,
    frame_count: int,
    shots: list[dict[str, Any]],
    boundaries: list[dict[str, Any]],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    total_duration = frame_count / fps

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    add("non_empty_timeline", bool(shots), f"{len(shots)} shot(s)")
    add(
        "starts_at_zero",
        bool(shots) and abs(shots[0]["start_seconds"]) < 1e-6,
        f"first start={shots[0]['start_seconds'] if shots else 'n/a'}",
    )
    add(
        "ends_at_video_end",
        bool(shots) and abs(shots[-1]["end_seconds"] - total_duration) < 1e-3,
        f"last end={shots[-1]['end_seconds'] if shots else 'n/a'}, video={total_duration:.6f}",
    )

    continuity_errors = []
    for previous, current in zip(shots, shots[1:]):
        if abs(previous["end_seconds"] - current["start_seconds"]) > 1e-5:
            continuity_errors.append(
                f"{previous['shot_index']}->{current['shot_index']}"
            )
    add(
        "continuous_timeline",
        not continuity_errors,
        "no gaps or overlaps"
        if not continuity_errors
        else f"errors: {', '.join(continuity_errors)}",
    )

    invalid_durations = [
        shot["shot_index"] for shot in shots if shot["duration_seconds"] <= 0
    ]
    add(
        "positive_durations",
        not invalid_durations,
        "all shots are positive"
        if not invalid_durations
        else f"invalid shots: {invalid_durations}",
    )

    unreadable_previews = []
    for shot in shots:
        image = cv2.imread(str(video_dir / shot["preview_file"]))
        if image is None or image.size == 0:
            unreadable_previews.append(shot["shot_index"])
    add(
        "readable_previews",
        not unreadable_previews,
        f"{len(shots) - len(unreadable_previews)}/{len(shots)} readable",
    )

    missing_boundary_images = [
        shot["shot_index"]
        for shot in shots[1:]
        if not shot["boundary_image"]
        or not (video_dir / shot["boundary_image"]).is_file()
    ]
    add(
        "boundary_evidence_complete",
        not missing_boundary_images,
        f"{len(boundaries)} boundary image(s) present"
        if not missing_boundary_images
        else f"missing for shots: {missing_boundary_images}",
    )

    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "shots_flagged_for_review": [
            shot["shot_index"] for shot in shots if shot["needs_review"]
        ],
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, shots: list[dict[str, Any]]) -> None:
    fieldnames = [
        "video_id",
        "source_file",
        "shot_index",
        "start_seconds",
        "end_seconds",
        "duration_seconds",
        "start_frame",
        "end_frame",
        "preview_time_seconds",
        "preview_file",
        "boundary_score",
        "boundary_method",
        "confidence",
        "needs_review",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for shot in shots:
            writer.writerow({name: shot.get(name) for name in fieldnames})


def process_one_video(
    path: Path,
    video_id: str,
    output_root: Path,
    parameters: DetectionParameters,
    ffprobe_executable: str,
    overwrite: bool,
    quiet: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    final_dir = output_root / video_id
    if final_dir.exists() and not overwrite:
        raise FileExistsError(
            f"Output directory already exists: {final_dir}. Use --overwrite."
        )

    partial_dir = output_root / f".partial-{video_id}-{uuid.uuid4().hex[:8]}"
    partial_dir.mkdir(parents=True, exist_ok=False)
    try:
        metadata = run_ffprobe(path, ffprobe_executable)
        fingerprints, raw_scores, boundaries, frame_count = detect_video(
            path, metadata, parameters
        )
        fps = float(metadata["fps"])
        shots, boundary_frames = build_shots(
            video_id, partial_dir, fps, frame_count, boundaries
        )
        save_shot_frames(
            path,
            partial_dir,
            fps,
            frame_count,
            boundary_frames,
            shots,
        )
        contact_sheet = create_contact_sheet(partial_dir, shots)
        validation = verify_video_result(
            partial_dir, fps, frame_count, shots, boundaries
        )
        if not validation["passed"]:
            failed_checks = [
                check["name"] for check in validation["checks"] if not check["passed"]
            ]
            raise RuntimeError(f"Result validation failed: {', '.join(failed_checks)}")

        combined_scores = [item["combined"] for item in raw_scores]
        score_summary = (
            {
                "median": round(float(np.median(combined_scores)), 6),
                "p90": round(float(np.quantile(combined_scores, 0.90)), 6),
                "p95": round(float(np.quantile(combined_scores, 0.95)), 6),
                "p99": round(float(np.quantile(combined_scores, 0.99)), 6),
            }
            if combined_scores
            else {}
        )
        video_payload = {
            "video": {
                "video_id": video_id,
                "source_file": path.name,
                "decoded_frame_count": frame_count,
                "video_duration_seconds": round(frame_count / fps, 6),
                **metadata,
            },
            "parameters": asdict(parameters),
            "score_summary": score_summary,
            "candidate_count_before_merge": len(
                find_candidates(raw_scores, fps, frame_count, parameters)
            ),
            "boundary_count": len(boundaries),
            "shot_count": len(shots),
            "contact_sheet": contact_sheet,
            "validation": validation,
            "shots": shots,
        }
        write_json(partial_dir / "shots.json", video_payload)
        write_csv(partial_dir / "shots.csv", shots)

        if final_dir.exists():
            shutil.rmtree(final_dir)
        partial_dir.replace(final_dir)
        elapsed = time.perf_counter() - started
        payload = {
            "video_id": video_id,
            "status": "success",
            "source_file": path.name,
            "shot_count": len(shots),
            "boundary_count": len(boundaries),
            "duration_seconds": round(frame_count / fps, 3),
            "processing_seconds": round(elapsed, 3),
            "result_dir": video_id,
            "contact_sheet": f"{video_id}/{contact_sheet}",
            "shots_flagged_for_review": validation["shots_flagged_for_review"],
        }
        if not quiet:
            print(
                f"[ok] {path.name}: {len(shots)} shots, "
                f"{len(validation['shots_flagged_for_review'])} flagged, "
                f"{elapsed:.2f}s"
            )
        return payload
    except Exception:
        shutil.rmtree(partial_dir, ignore_errors=True)
        raise


def video_rows_for_html(video: dict[str, Any], video_id: str) -> str:
    cards = []
    for row in video["shots"]:
        flag = " · review" if row["needs_review"] else ""
        boundary_link = (
            f'<a href="{html.escape(video_id)}/{html.escape(row["boundary_image"])}">切点前后</a>'
            if row.get("boundary_image")
            else "<span>视频起点</span>"
        )
        preview_path = html.escape(f'{video_id}/{row["preview_file"]}')
        cards.append(
            f"""
            <article class="shot">
              <img src="{preview_path}" loading="lazy" alt="Shot {row['shot_index']}">
              <div class="meta">
                <strong>#{row['shot_index']:03d}</strong>
                <span>{row['start_seconds']:.2f}–{row['end_seconds']:.2f}s</span>
                <span>{row['duration_seconds']:.2f}s · {row['confidence']}{flag}</span>
                {boundary_link}
              </div>
            </article>
            """
        )
    return "\n".join(cards)


def build_html_report(
    output_root: Path,
    parameters: DetectionParameters,
    successes: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    elapsed_seconds: float,
) -> None:
    sections = []
    for result in successes:
        video_id = result["video_id"]
        shots_path = output_root / video_id / "shots.json"
        video = json.loads(shots_path.read_text(encoding="utf-8"))
        sections.append(
            f"""
            <section>
              <div class="heading">
                <div>
                  <h2>{html.escape(result['source_file'])}</h2>
                  <p>{result['shot_count']} 个镜头 · {result['duration_seconds']:.2f} 秒 ·
                     {result['shots_flagged_for_review'] and len(result['shots_flagged_for_review']) or 0} 个建议复核</p>
                </div>
                <a href="{html.escape(result['contact_sheet'])}">打开总览图</a>
              </div>
              <div class="grid">
                {video_rows_for_html(video, video_id)}
              </div>
            </section>
            """
        )

    failure_html = ""
    if failures:
        items = "".join(
            f"<li><strong>{html.escape(item['source_file'])}</strong>："
            f"{html.escape(item['reason'])}</li>"
            for item in failures
        )
        failure_html = f'<section class="failure"><h2>处理失败</h2><ul>{items}</ul></section>'

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AIOS 镜头切分结果</title>
  <style>
    :root {{ color-scheme: dark; --bg:#101315; --panel:#191e21; --muted:#9aa6ad; --accent:#85d6bd; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; padding:32px; background:var(--bg); color:#f3f6f7; font:14px/1.55 system-ui,sans-serif; }}
    main {{ max-width:1440px; margin:auto; }}
    header {{ margin-bottom:30px; }}
    h1 {{ font-size:30px; margin:0 0 8px; }}
    h2 {{ margin:0 0 4px; font-size:20px; }}
    p {{ color:var(--muted); margin:0; }}
    a {{ color:var(--accent); text-decoration:none; }}
    section {{ margin:30px 0; padding:22px; background:var(--panel); border:1px solid #293136; border-radius:14px; }}
    .heading {{ display:flex; justify-content:space-between; gap:20px; align-items:center; margin-bottom:18px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(145px,1fr)); gap:12px; }}
    .shot {{ overflow:hidden; background:#0e1113; border-radius:9px; border:1px solid #2a3338; }}
    .shot img {{ display:block; width:100%; aspect-ratio:9/16; object-fit:cover; background:#171b1d; }}
    .meta {{ display:flex; flex-direction:column; padding:9px; gap:2px; }}
    .meta span {{ color:var(--muted); font-size:12px; }}
    .failure {{ border-color:#63383a; }}
    footer {{ color:var(--muted); margin:32px 0 8px; }}
    code {{ color:#d8e3e7; }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>AIOS 镜头切分结果</h1>
    <p>固定阈值 {parameters.scene_threshold} · 自适应下限 {parameters.adaptive_floor} ·
       合并间隔 {parameters.merge_gap_seconds}s · 总耗时 {elapsed_seconds:.2f}s</p>
  </header>
  {''.join(sections)}
  {failure_html}
  <footer>预览图取自镜头中点；每条切镜的记录均可通过“切点前后”人工复核。</footer>
</main>
</body>
</html>
"""
    (output_root / "report.html").write_text(document, encoding="utf-8")


def main() -> int:
    args = parse_args()
    parameters = DetectionParameters(
        scene_threshold=args.scene_threshold,
        adaptive_floor=args.adaptive_floor,
        merge_gap_seconds=args.merge_gap,
        edge_ignore_seconds=args.ignore_edge,
        analysis_width=args.analysis_width,
    )
    try:
        validate_parameters(parameters)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    source = Path(args.input).expanduser().resolve()
    output_root = Path(args.output).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    try:
        videos = discover_videos(source, args.recursive)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not videos:
        print(f"error: no supported videos found in {source}", file=sys.stderr)
        return 2

    run_started = time.perf_counter()
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    print(f"Found {len(videos)} video(s); output: {output_root}")
    for position, path in enumerate(videos, start=1):
        video_id = unique_video_id(path, used_ids)
        print(f"[{position}/{len(videos)}] {path.name}")
        try:
            success = process_one_video(
                path=path,
                video_id=video_id,
                output_root=output_root,
                parameters=parameters,
                ffprobe_executable=args.ffprobe,
                overwrite=args.overwrite,
                quiet=args.quiet,
            )
            successes.append(success)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            failures.append(
                {
                    "video_id": video_id,
                    "source_file": path.name,
                    "reason": reason,
                }
            )
            print(f"[failed] {path.name}: {reason}", file=sys.stderr)

    elapsed = time.perf_counter() - run_started
    all_shots = []
    for success in successes:
        payload = json.loads(
            (output_root / success["video_id"] / "shots.json").read_text(
                encoding="utf-8"
            )
        )
        for shot in payload["shots"]:
            all_shots.append(
                {
                    **shot,
                    "source_file": success["source_file"],
                }
            )

    write_json(output_root / "shots.json", {"shots": all_shots})
    write_csv(output_root / "shots.csv", all_shots)
    write_json(output_root / "failures.json", failures)
    manifest = {
        "schema_version": "1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "input_name": source.name,
        "output_name": output_root.name,
        "parameters": asdict(parameters),
        "summary": {
            "discovered": len(videos),
            "succeeded": len(successes),
            "failed": len(failures),
            "total_shots": len(all_shots),
            "elapsed_seconds": round(elapsed, 3),
        },
        "videos": successes,
        "failures": failures,
    }
    write_json(output_root / "manifest.json", manifest)
    build_html_report(output_root, parameters, successes, failures, elapsed)

    print(
        f"Done: {len(successes)} succeeded, {len(failures)} failed, "
        f"{len(all_shots)} shots in {elapsed:.2f}s"
    )
    print(f"Open: {(output_root / 'report.html').as_uri()}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
