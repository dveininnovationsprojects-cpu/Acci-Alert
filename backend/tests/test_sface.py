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


def test_identify_needs_models(tmp_path):
    rec = SFaceRecognizer(detector_path=tmp_path / "none.onnx", model_path=tmp_path / "none2.onnx",
                          gallery_path=tmp_path / "gallery.npz")
    with pytest.raises(RuntimeError, match="get_models"):
        rec.identify(np.zeros((480, 640, 3), np.uint8))


@pytest.mark.skipif(not MODELS_PRESENT, reason="run: python -m backend.tools.get_models")
def test_identify_with_empty_gallery_accepts_nobody(recognizer):
    result = recognizer.identify(np.full((480, 640, 3), 90, np.uint8))
    assert result["authorized"] is False
    assert result["face_count"] == 0


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


# ---------------------------------------------------------------- liveness (movement check)
def test_still_face_is_not_live():
    from backend.liveness import LivenessDetector
    live = LivenessDetector()
    box = (200, 120, 160, 200)
    assert [live.check_motion(box, 640, 480, now=t) for t in (0, 1, 2, 3, 4)] == [False] * 5


def test_moving_face_is_live_and_stays_live_briefly():
    from backend.liveness import LivenessDetector
    live = LivenessDetector()
    results = [live.check_motion((200 + dx, 120, 160, 200), 640, 480, now=t) for t, dx in enumerate((0, 6, 14, 14))]
    assert results[-1] is True
    assert live.check_motion((214, 120, 160, 200), 640, 480, now=5) is True   # still inside the hold time
    live.forget_motion()
    assert live.check_motion((214, 120, 160, 200), 640, 480, now=6) is False


# ---------------------------------------------------------------- /api/verify
BLANK_JPEG = "data:image/jpeg;base64," + __import__("base64").b64encode(
    __import__("cv2").imencode(".jpg", np.full((480, 640, 3), 90, np.uint8))[1].tobytes()).decode()


def test_verify_without_face_stays_locked():
    from backend.app import app
    data = app.test_client().post("/api/verify", json={"image": BLANK_JPEG}).get_json()
    assert data["status"] == "success"
    assert data["authorized"] is False
    assert data["face_count"] == 0
    assert data["box"] is None
    assert data["message"]


def test_verify_rejects_bad_image():
    from backend.app import app
    assert app.test_client().post("/api/verify", json={"image": "not-an-image"}).status_code == 400


def test_verify_unlocks_only_after_movement_and_logs_once(monkeypatch):
    from backend import app as app_module
    from backend.liveness import LivenessDetector

    boxes = iter([(200, 120, 160, 200), (206, 120, 160, 200), (216, 120, 160, 200), (216, 120, 160, 200)])
    monkeypatch.setattr(app_module, "recognise", lambda frame: (1, next(boxes), "Arun", True, 97.0, ""))
    monkeypatch.setattr(app_module, "liveness_detector", LivenessDetector())
    events, commands = [], []
    monkeypatch.setattr(app_module.database, "add_event", lambda **kw: events.append(kw["event_type"]) or 0)
    monkeypatch.setattr(app_module, "send_unified_command", lambda *a, **k: commands.append(a))
    monkeypatch.setattr(app_module.state, "last_face_result", None, raising=False)

    client = app_module.app.test_client()
    replies = [client.post("/api/verify", json={"image": BLANK_JPEG}).get_json() for _ in range(4)]

    assert [r["authorized"] for r in replies] == [False, False, True, True]
    assert replies[0]["message"] == "Move your head slightly."
    assert replies[2]["driver"] == "Arun"
    assert events == ["UNAUTHORISED", "AUTH_OK"]   # one entry per change, not one per frame
    assert len(commands) == 2
