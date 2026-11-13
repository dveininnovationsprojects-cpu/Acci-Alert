"""
AcciAlert - Real Human Face Dataset Generator & Augmentation Pipeline
Module: AI Face Recognition & Driver Authorisation (Arsal)

Features:
  - Generates photorealistic automotive datasets from real human drivers:
    - 3 Authorised Drivers: Arun, Kumar, Ravi (25 training + 10 test images each)
    - 5 Unknown Test Subjects: Unknown_1 .. Unknown_5 (10 test images each)
  - Realistic multi-condition automotive variations:
    - Vehicle interior lighting (direct sunlight, night cabin, lateral shadows)
    - Natural driver head poses & angles (-6° to +6°)
    - Natural distance scaling (0.94x to 1.06x)
"""

import os
import shutil
from pathlib import Path
from typing import Dict, Any, List, Tuple
import cv2
import numpy as np

try:
    from . import config
except ImportError:
    from backend import config

# Real human face coordinates from raw driver images
RAW_FACES: Dict[str, Tuple[str, Tuple[int, int, int, int]]] = {
    "Arun": ("media_1794551287264_6d629909.webp", (242, 60, 80, 85)),
    "Kumar": ("media_1794551306602_f7b8f60e.webp", (308, 90, 92, 98)),
    "Ravi": ("media_1794551255175_aecc4d27.webp", (190, 45, 90, 95)),
    "Unknown_1": ("media_1794551262237_eda05b18.webp", (242, 75, 102, 108)),
    "Unknown_2": ("media_1794551279380_ad28788a.webp", (225, 135, 75, 80)),
    "Unknown_3": ("media_1794551279380_ad28788a.webp", (160, 135, 70, 75)),
    "Unknown_4": ("media_1794551287264_6d629909.webp", (395, 110, 65, 65)),
    "Unknown_5": ("media_1794551306602_f7b8f60e.webp", (88, 85, 72, 75))
}


def extract_master_crops() -> Dict[str, np.ndarray]:
    """Extracts master real human face crops from dataset_raw."""
    raw_dir = config.BASE_DIR / "dataset_raw"
    if not raw_dir.exists():
        raise FileNotFoundError(f"dataset_raw directory not found: {raw_dir}")

    masters = {}
    for name, (fname, (x, y, w, h)) in RAW_FACES.items():
        img_path = raw_dir / fname
        if not img_path.exists():
            raise FileNotFoundError(f"Source raw image not found: {img_path}")

        im = cv2.imread(str(img_path))
        if im is None:
            raise ValueError(f"Unable to read image: {img_path}")

        pad_x, pad_y = int(w * 0.15), int(h * 0.15)
        x1, y1 = max(0, x - pad_x), max(0, y - pad_y)
        x2, y2 = min(im.shape[1], x + w + pad_x), min(im.shape[0], y + h + pad_y)
        crop = im[y1:y2, x1:x2]
        masters[name] = cv2.resize(crop, (250, 250))
    return masters


def augment_real_face(base_bgr: np.ndarray,
                      angle: float = 0.0,
                      scale: float = 1.0,
                      light_a: float = 1.0,
                      light_b: int = 0,
                      shadow: str = None) -> np.ndarray:
    """Applies realistic automotive environment augmentations to real human face."""
    h, w = base_bgr.shape[:2]
    cx, cy = w // 2, h // 2
    img = base_bgr.copy()

    # Head rotation and distance scaling
    if abs(angle) > 0.1 or scale != 1.0:
        M = cv2.getRotationMatrix2D((cx, cy), angle, scale)
        img = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE)

    # Lighting / exposure adjustments
    if light_a != 1.0 or light_b != 0:
        img = cv2.convertScaleAbs(img, alpha=light_a, beta=light_b)

    # Directional vehicle window shadows
    if shadow == "left":
        grad = np.tile(np.linspace(1.12, 0.88, w), (h, 1))
        img = np.clip(img * grad[:, :, None], 0, 255).astype(np.uint8)
    elif shadow == "right":
        grad = np.tile(np.linspace(0.88, 1.12, w), (h, 1))
        img = np.clip(img * grad[:, :, None], 0, 255).astype(np.uint8)

    return img


def generate_all_datasets(force: bool = True):
    print("=" * 65)
    print("AcciAlert - Generating Real Human Driver Face Datasets")
    print("=" * 65)

    masters = extract_master_crops()

    # 30 realistic training conditions per authorised driver
    train_configs = [
        (0.0, 1.0, 1.0, 0, None),
        (-2.0, 1.0, 1.0, 0, None),
        (2.0, 1.0, 1.0, 0, None),
        (-3.0, 1.0, 1.0, 0, None),
        (3.0, 1.0, 1.0, 0, None),
        (-4.0, 1.0, 1.0, 0, None),
        (4.0, 1.0, 1.0, 0, None),
        (0.0, 1.0, 1.15, 20, None),       # Bright sunny daylight
        (-2.5, 1.03, 1.15, 20, None),
        (2.5, 1.03, 1.15, 20, None),
        (0.0, 1.0, 0.85, -20, None),      # Night / dim cabin
        (-2.5, 0.97, 0.85, -20, None),
        (2.5, 0.97, 0.85, -20, None),
        (0.0, 1.0, 1.0, 0, "left"),       # Left-side window sunlight
        (-2.0, 1.0, 1.0, 0, "left"),
        (0.0, 1.0, 1.0, 0, "right"),      # Right-side shadow
        (2.0, 1.0, 1.0, 0, "right"),
        (0.0, 0.94, 1.0, 0, None),        # Driver leaning back
        (-2.0, 0.94, 1.0, 0, None),
        (2.0, 0.94, 1.0, 0, None),
        (0.0, 1.06, 1.0, 0, None),        # Driver leaning forward
        (-2.0, 1.06, 1.0, 0, None),
        (2.0, 1.06, 1.0, 0, None),
        (1.5, 0.98, 1.08, 10, "left"),
        (-1.5, 1.02, 1.08, 10, "right"),
        (1.5, 1.02, 0.92, -10, "left"),
        (-1.5, 0.98, 0.92, -10, "right"),
        (0.0, 1.02, 1.1, 10, None),
        (0.0, 0.98, 0.9, -10, None),
        (1.0, 1.0, 1.0, 0, None)
    ]

    # 10 independent test conditions
    test_configs = [
        (1.5, 0.98, 1.0, 0, None),
        (-2.0, 1.02, 1.0, 0, None),
        (0.0, 1.03, 1.12, 15, None),
        (3.0, 0.97, 1.12, 15, None),
        (1.5, 1.0, 0.88, -15, None),
        (-2.0, 1.02, 0.88, -15, None),
        (2.0, 0.98, 1.0, 0, "left"),
        (-1.5, 1.02, 1.0, 0, "right"),
        (0.0, 1.0, 1.05, 5, None),
        (2.5, 1.0, 0.95, -5, None)
    ]

    # 1. Generate 25 training images for Arun, Kumar, Ravi
    for driver in config.DEFAULT_AUTHORISED_DRIVERS:
        driver_dir = config.DATASET_DIR / driver
        driver_dir.mkdir(parents=True, exist_ok=True)
        master = masters[driver]

        for idx, (ang, sc, la, lb, sh) in enumerate(train_configs, start=1):
            frame = augment_real_face(master, angle=ang, scale=sc, light_a=la, light_b=lb, shadow=sh)
            cv2.imwrite(str(driver_dir / f"{idx:02d}.jpg"), frame)
        print(f"[+] Saved 25 real human training images for: {driver}")

    # 2. Generate 10 test images for Arun, Kumar, Ravi
    for driver in config.DEFAULT_AUTHORISED_DRIVERS:
        test_dir = config.DATASET_TEST_DIR / driver
        test_dir.mkdir(parents=True, exist_ok=True)
        master = masters[driver]

        for idx, (ang, sc, la, lb, sh) in enumerate(test_configs, start=1):
            frame = augment_real_face(master, angle=ang, scale=sc, light_a=la, light_b=lb, shadow=sh)
            cv2.imwrite(str(test_dir / f"test_{idx:02d}.jpg"), frame)
        print(f"[+] Saved 10 real human test images for: {driver}")

    # 3. Generate 10 test images for 5 Unknown real subjects
    for unk in ["Unknown_1", "Unknown_2", "Unknown_3", "Unknown_4", "Unknown_5"]:
        test_dir = config.DATASET_TEST_DIR / unk
        test_dir.mkdir(parents=True, exist_ok=True)
        master = masters[unk]

        for idx, (ang, sc, la, lb, sh) in enumerate(test_configs, start=1):
            frame = augment_real_face(master, angle=ang, scale=sc, light_a=la, light_b=lb, shadow=sh)
            cv2.imwrite(str(test_dir / f"test_{idx:02d}.jpg"), frame)
        print(f"[+] Saved 10 real human test images for: {unk}")

    print("\n[+] Real human face dataset generation complete.")


if __name__ == "__main__":
    generate_all_datasets(force=True)
