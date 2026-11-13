"""
Unit Tests for FaceRecognizer Module
"""

import cv2
import pytest
from backend.face_recognizer import FaceRecognizer, RecognitionResult
from backend.face_detector import FaceDetector
from backend import config


@pytest.fixture
def recognizer():
    rec = FaceRecognizer()
    assert rec.is_trained, "FaceRecognizer must be trained for testing"
    return rec


@pytest.fixture
def detector():
    return FaceDetector()


def test_recognizer_loaded(recognizer):
    assert recognizer.is_trained
    assert len(recognizer.labels_map) >= 3
    assert "Arun" in recognizer.labels_map.values()
    assert "Kumar" in recognizer.labels_map.values()
    assert "Ravi" in recognizer.labels_map.values()


def test_recognize_authorised_driver(recognizer, detector):
    img = cv2.imread(str(config.DATASET_TEST_DIR / "Arun" / "test_01.jpg"))
    assert img is not None
    det = detector.detect_faces(img)
    assert det.face_count >= 1

    rec = recognizer.predict(det.primary_roi)
    assert rec.driver == "Arun"
    assert rec.is_authorised is True
    assert rec.confidence_distance < recognizer.threshold
    assert rec.similarity_pct > 60.0


def test_recognize_unknown_subject(recognizer, detector):
    folder = config.DATASET_TEST_DIR / "Unknown_1"
    assert folder.exists()
    tested = False
    for img_p in sorted(folder.glob("*.jpg")):
        img = cv2.imread(str(img_p))
        det = detector.detect_faces(img)
        if det.face_count >= 1:
            rec = recognizer.predict(det.primary_roi)
            assert rec.driver == "Unknown"
            assert rec.is_authorised is False
            assert rec.confidence_distance >= recognizer.threshold
            tested = True
            break
    assert tested, "At least one face in Unknown_1 should be tested"
