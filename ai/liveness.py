"""
AcciAlert - Anti-Spoofing Liveness Detection Module
Module: AI Face Recognition & Driver Authorisation (Arsal)

Features:
  - Eye blink detection via temporal eye-state tracking (Open -> Closed -> Open)
  - Micro-motion detection across consecutive frames to reject static printed photos
  - Configurable timeout and interactive visual status messages
"""

import time
from typing import Tuple, Optional, List
import cv2
import numpy as np

from ai import config
from ai.face_detector import FaceDetector


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

