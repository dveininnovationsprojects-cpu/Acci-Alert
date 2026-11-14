"""Trains the face model from the photos in dataset/<driver name>/.

Saves models/face_model.yml and models/labels.json, then updates the driver
list in the database. Run with:  python -m backend.train_model
"""

import time
from pathlib import Path
from typing import Dict, List
import cv2
import numpy as np

try:
    from . import config
    from .face_detector import FaceDetector
    from .face_recognizer import FaceRecognizer
    from .db_logger import DatabaseLogger
except ImportError:
    from backend import config
    from backend.face_detector import FaceDetector
    from backend.face_recognizer import FaceRecognizer
    from backend.db_logger import DatabaseLogger


def train_ai_model(dataset_dir: Path = config.DATASET_DIR) -> FaceRecognizer:
    print("=" * 60)
    print("AcciAlert - Training AI Face Recognition Model")
    print("=" * 60)

    if not dataset_dir.exists():
        raise FileNotFoundError(f"Dataset directory not found: {dataset_dir}")

    detector = FaceDetector()
    recognizer = FaceRecognizer()
    db_logger = DatabaseLogger()

    driver_folders = [d for d in dataset_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not driver_folders:
        raise ValueError(f"No driver folders found in {dataset_dir}. Run enrol_driver.py first.")

    names_map: Dict[int, str] = {}
    face_rois: List[np.ndarray] = []
    labels: List[int] = []

    print(f"[*] Found {len(driver_folders)} driver directories: {[d.name for d in driver_folders]}")

    start_time = time.time()
    for label_id, driver_folder in enumerate(sorted(driver_folders, key=lambda x: x.name)):
        driver_name = driver_folder.name
        names_map[label_id] = driver_name
        image_files = list(driver_folder.glob("*.jpg")) + list(driver_folder.glob("*.png")) + list(driver_folder.glob("*.jpeg"))

        driver_face_count = 0
        for img_path in image_files:
            img = cv2.imread(str(img_path))
            if img is None:
                continue

            det_result = detector.detect_faces(img)
            if det_result.face_count >= 1 and det_result.primary_roi is not None:
                face_rois.append(det_result.primary_roi)
                labels.append(label_id)
                driver_face_count += 1
            else:
                # If Haar missed due to tight crop, attempt direct resize of pre-cropped image
                gray = detector.preprocess_image(img)
                resized = cv2.resize(gray, config.FACE_TARGET_SIZE, interpolation=cv2.INTER_AREA)
                face_rois.append(resized)
                labels.append(label_id)
                driver_face_count += 1

        print(f"    - {driver_name}: Processed {driver_face_count} face images (Label ID: {label_id})")
        db_logger.register_enrolled_driver(driver_name, driver_face_count)

    if not face_rois:
        raise RuntimeError("No face images could be processed for training.")

    # Train recognizer
    recognizer.train(face_rois, labels, names_map)
    elapsed = time.time() - start_time

    print("-" * 60)
    print(f"[+] Successfully trained model on {len(face_rois)} total face samples in {elapsed:.2f}s!")
    print(f"[+] Output Model: {config.MODEL_FILE}")
    print(f"[+] Output Labels: {config.LABELS_FILE}")
    print(f"[+] Enrolled Drivers: {list(names_map.values())}")
    print("=" * 60)
    return recognizer


if __name__ == "__main__":
    train_ai_model()

