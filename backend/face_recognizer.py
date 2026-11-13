"""
AcciAlert - Face Recognition Engine (LBPH)
Module: AI Face Recognition & Driver Authorisation (Arsal)

Features:
  - Local Binary Patterns Histograms (LBPH) Face Recognizer
  - Robust against lighting variations via local texture histograms
  - Dynamic thresholding: low distance = high match confidence
  - Persistence to YAML and JSON label dictionary
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np

try:
    from . import config
except ImportError:
    from backend import config


class RecognitionResult:
    """Encapsulates the prediction result for a face ROI."""
    def __init__(self,
                 driver: str,
                 is_authorised: bool,
                 confidence_distance: float,
                 similarity_pct: float,
                 threshold: float):
        self.driver = driver
        self.is_authorised = is_authorised
        self.confidence_distance = confidence_distance  # Raw LBPH distance (lower = better)
        self.similarity_pct = similarity_pct            # 0% - 100% human-readable score
        self.threshold = threshold

    def to_dict(self) -> Dict[str, any]:
        return {
            "driver": self.driver,
            "is_authorised": self.is_authorised,
            "confidence_distance": round(self.confidence_distance, 2),
            "similarity_pct": round(self.similarity_pct, 1),
            "threshold": self.threshold
        }


class FaceRecognizer:
    """Manages training, persistence, and prediction using OpenCV LBPH."""

    def __init__(self,
                 model_path: Optional[Path] = None,
                 labels_path: Optional[Path] = None,
                 threshold: float = config.MATCH_THRESHOLD):
        self.model_path = Path(model_path or config.MODEL_FILE)
        self.labels_path = Path(labels_path or config.LABELS_FILE)
        self.threshold = threshold

        self.recognizer = cv2.face.LBPHFaceRecognizer_create(
            radius=config.LBPH_RADIUS,
            neighbors=config.LBPH_NEIGHBORS,
            grid_x=config.LBPH_GRID_X,
            grid_y=config.LBPH_GRID_Y
        )
        self.labels_map: Dict[int, str] = {}  # {0: "Arun", 1: "Kumar", ...}
        self.is_trained = False

        if self.model_path.exists() and self.labels_path.exists():
            self.load()

    def train(self,
              face_rois: List[np.ndarray],
              labels: List[int],
              names_map: Dict[int, str]) -> None:
        """
        Trains the LBPH recognizer with the provided face images and labels.
        """
        if not face_rois or not labels or len(face_rois) != len(labels):
            raise ValueError("Training requires non-empty, matching lists of face_rois and labels.")

        print(f"[*] Training LBPH model on {len(face_rois)} face images across {len(names_map)} drivers...")
        # Ensure all images are uniform size and uint8 grayscale
        processed_rois = []
        for img in face_rois:
            if len(img.shape) == 3:
                img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            if img.shape != config.FACE_TARGET_SIZE:
                img = cv2.resize(img, config.FACE_TARGET_SIZE, interpolation=cv2.INTER_AREA)
            processed_rois.append(img.astype(np.uint8))

        labels_arr = np.array(labels, dtype=np.int32)
        self.recognizer.train(processed_rois, labels_arr)
        self.labels_map = names_map
        self.is_trained = True

        self.save()
        print(f"[+] LBPH model training completed and saved to: {self.model_path}")

    def save(self) -> None:
        """Persists the trained model and label dictionary to disk."""
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        self.recognizer.write(str(self.model_path))

        # Save labels dictionary as JSON
        with open(self.labels_path, "w", encoding="utf-8") as f:
            # Store keys as string for standard JSON format
            json.dump({str(k): v for k, v in self.labels_map.items()}, f, indent=2)

    def load(self) -> None:
        """Loads a previously saved model and label mapping from disk."""
        if not self.model_path.exists():
            raise FileNotFoundError(f"Model file not found: {self.model_path}")
        if not self.labels_path.exists():
            raise FileNotFoundError(f"Labels file not found: {self.labels_path}")

        self.recognizer.read(str(self.model_path))
        with open(self.labels_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            self.labels_map = {int(k): v for k, v in raw_data.items()}
        self.is_trained = True
        print(f"[+] Loaded face recognition model with {len(self.labels_map)} enrolled drivers: {list(self.labels_map.values())}")

    def predict(self, face_roi: np.ndarray) -> RecognitionResult:
        """
        Predicts driver identity from a face ROI.
        """
        if not self.is_trained:
            raise RuntimeError("FaceRecognizer is not trained yet. Please enrol drivers and train the model.")

        if len(face_roi.shape) == 3:
            face_roi = cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY)
        if face_roi.shape != config.FACE_TARGET_SIZE:
            face_roi = cv2.resize(face_roi, config.FACE_TARGET_SIZE, interpolation=cv2.INTER_AREA)

        label_id, distance = self.recognizer.predict(face_roi)

        # Distance is chi-square score: lower distance = closer match
        # Normalised similarity percentage (0 - 100%)
        # Distance of 0 is 100%; distance 120 is 0%
        similarity_pct = max(0.0, min(100.0, (1.0 - (distance / 120.0)) * 100.0))

        if distance < self.threshold and label_id in self.labels_map:
            driver_name = self.labels_map[label_id]
            is_authorised = True
        else:
            driver_name = "Unknown"
            is_authorised = False

        return RecognitionResult(
            driver=driver_name,
            is_authorised=is_authorised,
            confidence_distance=distance,
            similarity_pct=similarity_pct,
            threshold=self.threshold
        )

    def recognize(self, face_roi: np.ndarray) -> Dict[str, any]:
        """Convenience dictionary wrapper around predict()."""
        res = self.predict(face_roi)
        return {
            "driver": res.driver,
            "authorized": res.is_authorised,
            "distance": res.confidence_distance,
            "confidence": res.similarity_pct,
            "threshold": res.threshold
        }

