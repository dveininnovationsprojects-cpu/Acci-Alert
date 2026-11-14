"""Recognises enrolled drivers.

FaceRecognizer      OpenCV LBPH model trained from photos (models/face_model.yml, models/labels.json)
SFaceRecognizer     pretrained OpenCV SFace model; drivers are enrolled live from the camera and
                    their face signatures are saved in data/face_gallery.npz
"""

import json
import re
import threading
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


class SFaceRecognizer:
    """Face login with OpenCV's pretrained YuNet (detector) and SFace (recogniser).

    The models are already trained on millions of faces, so a driver is enrolled just by
    saving face signatures from live camera frames. No retraining step is needed.
    """

    def __init__(self, detector_path=None, model_path=None, gallery_path=None, threshold=None):
        self.detector_path = Path(detector_path or config.YUNET_FILE)
        self.model_path = Path(model_path or config.SFACE_FILE)
        self.gallery_path = Path(gallery_path or config.FACE_GALLERY_FILE)
        self.threshold = config.SFACE_THRESHOLD if threshold is None else threshold
        # The OpenCV models are not safe to use from two threads at once, and the server answers
        # /api/verify and /api/enrol in different threads. One lock covers detection, signatures and matching.
        self.lock = threading.RLock()
        self.gallery: Dict[str, np.ndarray] = {}
        self.detector = None
        self.recognizer = None
        self.models_ready = False

        if self.detector_path.exists() and self.model_path.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.detector = cv2.FaceDetectorYN.create(
                    str(self.detector_path), "", (320, 320), config.SFACE_MIN_DETECT_SCORE, 0.3, 5000)
                self.recognizer = cv2.FaceRecognizerSF.create(str(self.model_path), "")
                self.models_ready = True
            except cv2.error as err:
                print(f"SFace models could not be loaded: {err}")
        self._load_gallery()

    @property
    def ready(self) -> bool:
        """True when the models are loaded and at least one driver is enrolled."""
        return self.models_ready and bool(self.gallery)

    def _load_gallery(self):
        if not self.gallery_path.exists():
            return
        data = np.load(self.gallery_path, allow_pickle=False)
        self.gallery = {name: data[name] for name in data.files}

    def _save_gallery(self):
        self.gallery_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(self.gallery_path, **self.gallery)

    def _faces(self, frame: np.ndarray):
        """Faces found in the frame that are big and clear enough to use."""
        h, w = frame.shape[:2]
        with self.lock:
            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(frame)
        if faces is None:
            return []
        return [f for f in faces if f[2] >= config.SFACE_MIN_FACE_PX and f[14] >= config.SFACE_MIN_DETECT_SCORE]

    def _signature(self, frame: np.ndarray, face) -> np.ndarray:
        with self.lock:
            return self.recognizer.feature(self.recognizer.alignCrop(frame, face))[0]

    def _best_match(self, sig: np.ndarray) -> Tuple[str, float]:
        best_name, best_score = "Unknown", -1.0
        for name, sigs in self.gallery.items():
            scores = [self.recognizer.match(sig.reshape(1, -1), s.reshape(1, -1), cv2.FaceRecognizerSF_FR_COSINE)
                      for s in sigs]
            top = float(np.mean(sorted(scores, reverse=True)[:3]))  # average of the 3 closest samples
            if top > best_score:
                best_name, best_score = name, top
        return best_name, best_score

    def identify(self, frame: np.ndarray) -> Dict[str, any]:
        """Look at one camera frame. A driver is accepted only if exactly one face is
        visible and its score reaches the threshold."""
        if not self.models_ready:
            raise RuntimeError("SFace models are missing. Run: python -m backend.tools.get_models")
        faces = self._faces(frame)
        if len(faces) != 1:
            return {"face_count": len(faces), "driver": "Unknown", "authorized": False, "score": 0.0, "box": None}
        face = faces[0]
        if not self.gallery:   # nobody enrolled yet: report where the face is, but accept nobody
            return {"face_count": 1, "driver": "Unknown", "authorized": False, "score": 0.0,
                    "box": [int(v) for v in face[:4]]}
        with self.lock:
            name, score = self._best_match(self._signature(frame, face))
        ok = score >= self.threshold
        return {"face_count": 1, "driver": name if ok else "Unknown", "authorized": ok,
                "score": round(score, 3), "box": [int(v) for v in face[:4]]}

    @staticmethod
    def clean_name(name: str) -> str:
        """Letters, digits, spaces, dots, dashes and underscores only, up to 40 characters."""
        name = re.sub(r"[^A-Za-z0-9 ._-]", "", str(name)).strip(" .")[:40].strip(" .")
        if not name or name.lower() == "unknown":
            raise ValueError("Enter a valid driver name.")
        return name

    def enrol(self, name: str, frames: List[np.ndarray], image_dir=None) -> Dict[str, any]:
        """Save face signatures for a driver from live camera frames.
        Frames with no face, several faces or a small face are skipped.
        If image_dir is given, the frames that were used are also saved there as JPEG files."""
        if not self.models_ready:
            raise RuntimeError("SFace models are missing. Run: python -m backend.tools.get_models")
        name = self.clean_name(name)
        sigs, used, skipped = [], [], 0
        for frame in frames:
            faces = self._faces(frame)
            if len(faces) != 1:
                skipped += 1
                continue
            sigs.append(self._signature(frame, faces[0]))
            used.append(frame)
        if len(sigs) < config.ENROL_MIN_FRAMES:
            raise ValueError(f"Only {len(sigs)} usable frames (need {config.ENROL_MIN_FRAMES}). "
                             "Face the camera alone, with good light, and try again.")
        with self.lock:
            old = self.gallery.get(name)
            self.gallery[name] = np.vstack([old, sigs]) if old is not None else np.array(sigs)
            self._save_gallery()
            total = len(self.gallery[name])
        saved = self._save_images(image_dir, used) if image_dir is not None else 0
        return {"name": name, "accepted": len(sigs), "skipped": skipped, "total_samples": total,
                "images_saved": saved}

    @staticmethod
    def _save_images(folder, frames: List[np.ndarray]) -> int:
        """Write frames as 01.jpg, 02.jpg ... continuing after any files already in the folder."""
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        number = len(list(folder.glob("*.jpg")))
        saved = 0
        for frame in frames:
            number += 1
            if cv2.imwrite(str(folder / f"{number:02d}.jpg"), frame):
                saved += 1
        return saved

    def enrolled(self) -> Dict[str, int]:
        """Driver name -> number of saved face signatures."""
        return {name: len(sigs) for name, sigs in self.gallery.items()}
