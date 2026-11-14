"""Tests for the live-enrolment face recogniser (SFace) and its API."""

import numpy as np
import pytest

from backend import config
from backend.face_recognizer import SFaceRecognizer

MODELS_PRESENT = config.YUNET_FILE.exists() and config.SFACE_FILE.exists()


@pytest.fixture
def recognizer(tmp_path):
    return SFaceRecognizer(gallery_path=tmp_path / "gallery.npz")


def test_clean_name_removes_odd_characters():
    assert SFaceRecognizer.clean_name("  Arun <b>K</b> ") == "Arun bKb"


@pytest.mark.parametrize("bad", ["", "   ", "Unknown", "unknown", "<>"])
def test_clean_name_rejects_empty_and_unknown(bad):
    with pytest.raises(ValueError):
        SFaceRecognizer.clean_name(bad)


def test_not_ready_without_enrolled_driver(recognizer):
    assert recognizer.ready is False
    with pytest.raises(RuntimeError):
        recognizer.identify(np.zeros((480, 640, 3), np.uint8))


def test_gallery_is_saved_and_loaded(tmp_path):
    first = SFaceRecognizer(gallery_path=tmp_path / "gallery.npz")
    first.gallery["Arun"] = np.random.rand(12, 128).astype(np.float32)
    first._save_gallery()

    second = SFaceRecognizer(gallery_path=tmp_path / "gallery.npz")
    assert second.enrolled() == {"Arun": 12}


@pytest.mark.skipif(not MODELS_PRESENT, reason="run: python -m backend.tools.get_models")
def test_enrol_needs_enough_usable_frames(recognizer):
    blank = np.full((480, 640, 3), 90, np.uint8)
    with pytest.raises(ValueError, match="usable frames"):
        recognizer.enrol("Nobody", [blank] * 12)
    assert recognizer.enrolled() == {}


def test_enrol_without_models_gives_clear_error(tmp_path):
    rec = SFaceRecognizer(detector_path=tmp_path / "none.onnx", model_path=tmp_path / "none2.onnx",
                          gallery_path=tmp_path / "gallery.npz")
    with pytest.raises(RuntimeError, match="get_models"):
        rec.enrol("Arun", [np.zeros((480, 640, 3), np.uint8)])


def test_enrol_api_checks_input():
    from backend.app import app
    client = app.test_client()
    assert client.post("/api/enrol", json={"frames": ["x"]}).status_code == 400
    assert client.post("/api/enrol", json={"name": "Arun", "frames": []}).status_code == 400
    assert client.post("/api/enrol", json={"name": "Arun", "frames": ["x"] * 201}).status_code == 400


def test_enrolled_api_lists_engine_and_drivers():
    from backend.app import app
    data = app.test_client().get("/api/enrolled").get_json()
    assert data["engine"] in ("sface", "lbph")
    assert isinstance(data["drivers"], list)
