"""
AcciAlert - Face Detection & Preprocessing Module
Module: AI Face Recognition & Driver Authorisation (Arsal)
"""

from typing import List, Tuple, Optional, Dict, Any
from pathlib import Path
import cv2
import numpy as np

try:
    from . import config
except ImportError:
    from backend import config


class FaceDetectionResult:
    """Encapsulates the output of face detection on a single frame."""
    def __init__(self,
                 status: str,
                 face_count: int,
                 boxes: List[Tuple[int, int, int, int]],
                 rois: List[np.ndarray],
                 primary_box: Optional[Tuple[int, int, int, int]] = None,
                 primary_roi: Optional[np.ndarray] = None):
        self.status = status                # 'NO_FACE', 'SINGLE_FACE', 'MULTIPLE_FACES'
        self.face_count = face_count
        self.boxes = boxes                  # [(x, y, w, h), ...]
        self.rois = rois                    # [aligned_gray_roi, ...]
        self.primary_box = primary_box      # Box of largest/primary face
        self.primary_roi = primary_roi      # Normalized ROI for recognition


class FaceDetector:
    """Robust face and eye detector with illumination normalization (CLAHE)."""

    def __init__(self,
                 face_cascade_path: Optional[Path] = None,
                 eye_cascade_path: Optional[Path] = None):
        self.face_cascade_path = str(face_cascade_path or config.HAAR_FACE_PATH)
        self.eye_cascade_path = str(eye_cascade_path or config.HAAR_EYE_PATH)

        self.face_cascade = cv2.CascadeClassifier(self.face_cascade_path)
        self.eye_cascade = cv2.CascadeClassifier(self.eye_cascade_path)

        if self.face_cascade.empty():
            raise FileNotFoundError(f"Failed to load Haar face cascade from: {self.face_cascade_path}")
        if self.eye_cascade.empty():
            raise FileNotFoundError(f"Failed to load Haar eye cascade from: {self.eye_cascade_path}")

        # CLAHE for lighting invariance (handles shadows, bright sunlight, dim car interior)
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    def preprocess_image(self, img: np.ndarray) -> np.ndarray:
        """Converts to grayscale and applies adaptive histogram equalization."""
        if len(img.shape) == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img.copy()
        equalized = self.clahe.apply(gray)
        return equalized

    def detect_faces(self, frame: np.ndarray) -> FaceDetectionResult:
        """
        Detects faces in the given camera frame.
        Handles zero faces, exactly one face, and multiple faces.
        """
        if frame is None or frame.size == 0:
            return FaceDetectionResult("NO_FACE", 0, [], [])

        gray = self.preprocess_image(frame)

        raw_faces = self.face_cascade.detectMultiScale(
            gray,
            scaleFactor=config.FACE_SCALE_FACTOR,
            minNeighbors=config.FACE_MIN_NEIGHBORS,
            minSize=config.FACE_MIN_SIZE,
            flags=cv2.CASCADE_SCALE_IMAGE
        )

        face_count = len(raw_faces)
        if face_count == 0:
            return FaceDetectionResult("NO_FACE", 0, [], [])

        # Sort faces by area (width * height) descending to get primary face first
        boxes = sorted([tuple(b) for b in raw_faces], key=lambda b: b[2] * b[3], reverse=True)

        rois = []
        for (x, y, w, h) in boxes:
            # Crop with slight padding if within bounds
            pad_x = int(w * 0.05)
            pad_y = int(h * 0.05)
            y1 = max(0, y - pad_y)
            y2 = min(gray.shape[0], y + h + pad_y)
            x1 = max(0, x - pad_x)
            x2 = min(gray.shape[1], x + w + pad_x)

            face_crop = gray[y1:y2, x1:x2]
            # Standardize face ROI size for LBPH recognition
            resized_roi = cv2.resize(face_crop, config.FACE_TARGET_SIZE, interpolation=cv2.INTER_AREA)
            rois.append(resized_roi)

        status = "SINGLE_FACE" if face_count == 1 else "MULTIPLE_FACES"
        return FaceDetectionResult(
            status=status,
            face_count=face_count,
            boxes=boxes,
            rois=rois,
            primary_box=boxes[0],
            primary_roi=rois[0]
        )

    def detect_eyes_in_face(self, face_roi: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """
        Detects eyes in the upper 60% of the face ROI to prevent false detections
        on nostrils or mouth.
        """
        if face_roi is None or face_roi.size == 0:
            return []

        h, w = face_roi.shape[:2]
        upper_face = face_roi[0:int(h * 0.60), :]

        eyes = self.eye_cascade.detectMultiScale(
            upper_face,
            scaleFactor=1.1,
            minNeighbors=4,
            minSize=(int(w * 0.12), int(h * 0.12))
        )
        return [tuple(e) for e in eyes]

