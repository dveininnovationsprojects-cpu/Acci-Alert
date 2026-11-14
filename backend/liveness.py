"""Liveness check: a blink or small head movement shows the face is a real person, not a photo.
"""

import time
from typing import Tuple, Optional, List
import cv2
import numpy as np

try:
    from . import config
    from .face_detector import FaceDetector
except ImportError:
    from backend import config
    from backend.face_detector import FaceDetector


class LivenessDetector:
    """Detects driver liveness using eye-blink tracking and facial motion analysis."""

    def __init__(self, detector: Optional[FaceDetector] = None):
        self.detector = detector or FaceDetector()
        self.reset()

    def reset(self):
        """Resets the internal tracking state for a new authentication session."""
        self.start_time = time.time()
        self.consecutive_eyes_closed_frames = 0
        self.eyes_previously_open = False
        self.blink_count = 0
        self.motion_scores: List[float] = []
        self.prev_face_gray: Optional[np.ndarray] = None
        self.is_passed = False
        self.status_message = "Please blink to verify driver liveness..."

    def update(self, frame: np.ndarray, face_roi: np.ndarray, face_box: Tuple[int, int, int, int]) -> Tuple[bool, str]:
        """
        Processes a single frame for liveness cues.
        Returns:
            (liveness_passed: bool, status_message: str)
        """
        if self.is_passed:
            return True, "Liveness verified!"

        elapsed = time.time() - self.start_time
        if elapsed > config.LIVENESS_TIMEOUT_SECONDS:
            # If motion was good but no explicit blink was caught within timeout,
            # evaluate average motion to decide if alive or static spoof
            avg_motion = np.mean(self.motion_scores) if self.motion_scores else 0.0
            if avg_motion >= config.LIVENESS_MOTION_THRESHOLD and self.blink_count >= 1:
                self.is_passed = True
                self.status_message = "Liveness verified!"
                return True, self.status_message
            else:
                self.status_message = "Liveness check timed out. Move naturally and try again."
                return False, self.status_message

        # 1. Micro-motion / anti-static photo check
        gray_roi = face_roi if len(face_roi.shape) == 2 else cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY)
        if self.prev_face_gray is not None and self.prev_face_gray.shape == gray_roi.shape:
            diff = cv2.absdiff(gray_roi, self.prev_face_gray)
            motion_score = float(np.mean(diff))
            self.motion_scores.append(motion_score)
            if len(self.motion_scores) > 30:
                self.motion_scores.pop(0)
        self.prev_face_gray = gray_roi.copy()

        # 2. Eye blink detection
        eyes = self.detector.detect_eyes_in_face(gray_roi)
        eyes_detected = len(eyes)

        if eyes_detected >= 1:
            if self.consecutive_eyes_closed_frames >= 1 and self.eyes_previously_open:
                # Eyes transitioned: Open -> Closed -> Open (A completed blink)
                self.blink_count += 1
                self.consecutive_eyes_closed_frames = 0
            self.eyes_previously_open = True
            self.consecutive_eyes_closed_frames = 0
        else:
            # Eyes not detected in ROI (likely closed during blink)
            if self.eyes_previously_open:
                self.consecutive_eyes_closed_frames += 1

        # Check pass condition
        avg_motion = np.mean(self.motion_scores) if self.motion_scores else 0.0
        if self.blink_count >= config.LIVENESS_BLINK_EYES_MIN and avg_motion >= (config.LIVENESS_MOTION_THRESHOLD * 0.5):
            self.is_passed = True
            self.status_message = "Liveness verified!"
            return True, self.status_message

        # Status text for feedback
        if self.blink_count == 0:
            self.status_message = f"Liveness check: Please blink naturally ({int(config.LIVENESS_TIMEOUT_SECONDS - elapsed)}s left)"
        else:
            self.status_message = "Blink detected! Verifying motion..."

        return False, self.status_message

    # ---- simple movement check used by the face login (works with any face detector)
    def check_motion(self, box, frame_w: int, frame_h: int, now: Optional[float] = None) -> bool:
        """True when the face has moved a little over the last few seconds.

        A photo held still in front of the camera does not move, a real person does.
        The result stays valid for a few seconds so the lock does not flicker.
        """
        now = time.time() if now is None else now
        if now < getattr(self, "live_until", 0.0):
            self._remember(box, frame_w, frame_h, now)
            return True

        self._remember(box, frame_w, frame_h, now)
        if len(self.moves) < 3:
            return False
        xs = [m[1] for m in self.moves]
        ys = [m[2] for m in self.moves]
        ws = [m[3] for m in self.moves]
        spread = max(max(xs) - min(xs), max(ys) - min(ys), max(ws) - min(ws))
        if spread >= config.LIVENESS_MOVE_FRACTION:
            self.live_until = now + config.LIVENESS_HOLD_SECONDS
            return True
        return False

    def _remember(self, box, frame_w: int, frame_h: int, now: float):
        x, y, w, h = box
        if not hasattr(self, "moves"):
            self.moves = []
        self.moves = [m for m in self.moves if now - m[0] <= config.LIVENESS_WINDOW_SECONDS]
        self.moves.append((now, (x + w / 2) / frame_w, (y + h / 2) / frame_h, w / frame_w))

    def forget_motion(self):
        """Call when no face is in view, so the next person starts a fresh check."""
        self.moves = []
        self.live_until = 0.0

