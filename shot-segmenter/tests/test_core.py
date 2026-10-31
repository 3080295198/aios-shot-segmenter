from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from segment_videos import (  # noqa: E402
    DetectionParameters,
    Fingerprint,
    build_shots,
    merge_candidates,
    validate_parameters,
)


def fingerprint(
    histogram_value: float,
    hash_value: float,
    pixel_value: float,
) -> Fingerprint:
    return Fingerprint(
        histogram=np.asarray([histogram_value, 1.0 - histogram_value], dtype=np.float32),
        hash_bits=np.asarray([hash_value > 0.5] * 8),
        thumbnail=np.full((8, 8), int(pixel_value * 255), dtype=np.uint8),
    )


class TimelineTests(unittest.TestCase):
    def test_shot_timeline_is_continuous_and_covers_video(self) -> None:
        fps = 15.0
        shot_rows, boundary_frames = build_shots(
            video_id="test",
            video_dir=Path("unused"),
            fps=fps,
            frame_count=150,
            boundaries=[
                {"frame_index": 30, "combined": 0.5, "method": "fixed"},
                {"frame_index": 90, "combined": 0.4, "method": "adaptive"},
            ],
        )
        self.assertEqual(boundary_frames, [30, 90])
        self.assertEqual([row["start_seconds"] for row in shot_rows], [0.0, 2.0, 6.0])
        self.assertEqual([row["end_seconds"] for row in shot_rows], [2.0, 6.0, 10.0])
        self.assertAlmostEqual(
            sum(row["duration_seconds"] for row in shot_rows),
            shot_rows[-1]["end_seconds"],
        )

    def test_close_events_that_return_to_same_scene_are_suppressed(self) -> None:
        scene_a = fingerprint(0.95, 0.1, 0.1)
        scene_b = fingerprint(0.05, 0.9, 0.9)
        prints = [scene_a] * 10 + [scene_b, scene_b, scene_b, scene_b] + [scene_a] * 5
        candidates = [
            {"frame_index": 10, "combined": 0.7, "method": "fixed"},
            {"frame_index": 14, "combined": 0.6, "method": "fixed"},
        ]
        parameters = DetectionParameters(merge_gap_seconds=0.35)
        accepted = merge_candidates(candidates, prints, fps=15.0, parameters=parameters)
        self.assertEqual(accepted, [])

    def test_close_events_with_different_end_scene_keep_one_boundary(self) -> None:
        scene_a = fingerprint(0.95, 0.1, 0.1)
        scene_b = fingerprint(0.05, 0.9, 0.9)
        scene_c = fingerprint(0.45, 0.5, 0.5)
        prints = [scene_a] * 10 + [scene_b, scene_b, scene_b, scene_b] + [scene_c] * 5
        candidates = [
            {"frame_index": 10, "combined": 0.7, "method": "fixed"},
            {"frame_index": 14, "combined": 0.6, "method": "fixed"},
        ]
        accepted = merge_candidates(
            candidates,
            prints,
            fps=15.0,
            parameters=DetectionParameters(merge_gap_seconds=0.35),
        )
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["frame_index"], 10)
        self.assertEqual(accepted[0]["method"], "fixed-merged")

    def test_invalid_thresholds_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_parameters(
                DetectionParameters(scene_threshold=0.1, adaptive_floor=0.2)
            )


if __name__ == "__main__":
    unittest.main()
