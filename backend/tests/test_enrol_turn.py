"""Enrolling a driver needs the same head turn (right, left, straight), so a photo cannot be enrolled as a driver."""

import base64
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as server  # noqa: E402
import config  # noqa: E402

GOOD = [0.0] * 4 + [-0.3] * 5 + [0.3] * 5 + [0.02] * 5            # 19 pictures: straight, right, left, straight
ONLY_RIGHT = [0.0] * 4 + [-0.3] * 5 + [0.0] * 10
STILL = [0.0] * 19


def shot():
    ok, buf = cv2.imencode(".jpg", np.full((120, 160, 3), 128, np.uint8))
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


@pytest.fixture()
def enrol(monkeypatch):
    monkeypatch.setattr(config, "ENROL_REQUIRE_TURN", True)
    monkeypatch.setattr(server.face_sface, "models_ready", True, raising=False)
    saved = []

    def fake_enrol(name, frames, image_dir=None):
        saved.append((name, len(frames)))
        return {"name": name, "accepted": len(frames), "skipped": 0, "total_samples": len(frames), "images_saved": 0}
    monkeypatch.setattr(server.face_sface, "enrol", fake_enrol)
    monkeypatch.setattr(server.database, "save_driver_enrolment", lambda *a, **k: None)

    def post(yaws, challenge=True, name="Turny", license="TN-77 0001"):
        it = iter(yaws)
        monkeypatch.setattr(server.headturn, "yaw_ratio", lambda face: next(it))
        monkeypatch.setattr(server.face_sface, "_faces", lambda frame: [np.zeros(15)])
        client = server.app.test_client()
        body = {"name": name, "license": license, "frames": [shot()] * len(yaws)}
        if challenge is True:
            body["challenge"] = client.post("/api/auth/challenge").get_json()["token"]
        elif challenge:
            body["challenge"] = challenge
        return client.post("/api/enrol", json=body)
    post.saved = saved
    return post


def test_a_driver_who_turns_right_left_straight_is_enrolled_from_the_same_pictures(enrol):
    res = enrol(GOOD)
    assert res.status_code == 200 and res.get_json()["status"] == "success"
    assert enrol.saved == [("Turny", 19)]              # the clip itself is the enrolment: no separate 25 pictures


@pytest.mark.parametrize("yaws,missing", [(ONLY_RIGHT, "LEFT"), (STILL, "RIGHT")])
def test_an_incomplete_turn_is_refused_and_nothing_is_saved(enrol, yaws, missing):
    res = enrol(yaws)
    assert res.status_code == 400 and missing in res.get_json()["message"] and "Nothing was saved" in res.get_json()["message"]
    assert enrol.saved == []


@pytest.mark.parametrize("challenge", [False, "made-up"])
def test_a_request_is_required(enrol, challenge):
    res = enrol(GOOD, challenge=challenge)
    assert res.status_code == 400 and "expired" in res.get_json()["message"] and enrol.saved == []


def test_a_request_works_once_only(enrol, monkeypatch):
    client = server.app.test_client()
    token = client.post("/api/auth/challenge").get_json()["token"]
    assert enrol(GOOD, challenge=token).status_code == 200
    assert enrol(GOOD, challenge=token).status_code == 400


def test_too_many_pictures_are_refused(enrol):
    assert enrol([0.0] * (server.AUTH_MAX_FRAMES + 1)).status_code == 400


def test_pictures_without_a_single_face_cannot_pass(enrol, monkeypatch):
    res = enrol(GOOD)
    enrol.saved.clear()
    monkeypatch.setattr(server.face_sface, "_faces", lambda frame: [])
    client = server.app.test_client()
    body = {"name": "Turny", "license": "TN-77 0001", "frames": [shot()] * 19, "challenge": client.post("/api/auth/challenge").get_json()["token"]}
    assert client.post("/api/enrol", json=body).status_code == 400 and enrol.saved == []


def test_the_enrol_page_uses_the_head_turn_clip():
    js = (Path(server.FRONTEND_DIR) / "static" / "js" / "driver_auth.js").read_text(encoding="utf-8")
    assert "HeadTurn" in js and "challenge:" in js
