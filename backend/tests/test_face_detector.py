"""
Unit Tests for FaceDetector Module
"""

import numpy as np
import pytest
from backend.face_detector import FaceDetector, FaceDetectionResult
from backend import config


@pytest.fixture
def detector():
    return FaceDetector()


def test_detector_initialization(detector):
    assert detector.face_cascade is not None
    assert not detector.face_cascade.empty()
    assert detector.eye_cascade is not None
    assert not detector.eye_cascade.empty()


def test_detect_faces_empty_frame(detector):
    empty = np.zeros((0, 0), dtype=np.uint8)
    res = detector.detect_faces(empty)
    assert res.status == "NO_FACE"
    assert res.face_count == 0
    assert len(res.rois) == 0


def test_detect_faces_blank_image(detector):
    # A completely blank gray image contains no faces
    blank = np.full((300, 300, 3), 128, dtype=np.uint8)
    res = detector.detect_faces(blank)
    assert res.status == "NO_FACE"
    assert res.face_count == 0


def test_detect_single_face(detector):
    # Load one of the generated training images
    test_img_path = config.DATASET_DIR / "Arun" / "01.jpg"
    assert test_img_path.exists(), "Dataset image must exist"
    import cv2
    img = cv2.imread(str(test_img_path))
    res = detector.detect_faces(img)
    assert res.face_count == 1
    assert res.status == "SINGLE_FACE"
    assert res.primary_roi is not None
    assert res.primary_roi.shape == config.FACE_TARGET_SIZE


def test_detect_multiple_faces(detector):
    import cv2
    # Create an image containing two faces side-by-side
    img1 = cv2.imread(str(config.DATASET_DIR / "Arun" / "01.jpg"))
    img2 = cv2.imread(str(config.DATASET_DIR / "Kumar" / "01.jpg"))
    assert img1 is not None and img2 is not None

    combined = np.hstack([img1, img2])
    res = detector.detect_faces(combined)
    assert res.face_count >= 2
    assert res.status == "MULTIPLE_FACES"

