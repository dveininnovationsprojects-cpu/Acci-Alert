"""The head-turn login finds the face and the turn in every picture, but matches the face on a few pictures only."""

import base64

import cv2
import numpy as np
import pytest

from backend import app as server

CLIP = [0.0] * 6 + [-0.30] * 6 + [0.30] * 6 + [0.03] * 6          # 24 pictures: straight, right, left, straight


def frame_data():
    ok, buf = cv2.imencode(".jpg", np.full((120, 160, 3), 128, np.uint8))
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


@pytest.fixture()
def fast(monkeypatch):
    """A fake face model: every picture has one face, the head turn follows CLIP, the match is a set answer."""
    calls = {"detect": 0, "signature": 0, "match": ("Arun", 0.80)}
    it = iter(CLIP)
    face = np.array([20, 20, 100, 100] + [0.0] * 11, dtype=np.float32)

    def faces(_frame):
        calls["detect"] += 1
        return [face]

    def signature(_frame, _face):
        calls["signature"] += 1
        return np.zeros(128, np.float32)

    monkeypatch.setattr(server.face_sface, "models_ready", True)
    monkeypatch.setattr(server.face_sface, "gallery", {"Arun": np.zeros((1, 128), np.float32)})
    monkeypatch.setattr(server.face_sface, "_faces", faces)
    monkeypatch.setattr(server.face_sface, "_signature", signature)
    monkeypatch.setattr(server.face_sface, "_best_match", lambda sig: calls["match"])
    monkeypatch.setattr(server.headturn, "yaw_ratio", lambda row: next(it))
    return calls


def login(client, n=len(CLIP)):
    token = client.post("/api/auth/challenge").get_json()["token"]
    return client.post("/api/authenticate", json={"frames": [frame_data()] * n, "challenge": token}).get_json()


def test_only_a_few_pictures_go_through_the_face_match(fast):
    res = login(server.app.test_client())
    assert res["authorized"] is True and res["driver"] == "Arun" and res["ignition"] == "UNLOCKED"
    assert fast["detect"] == len(CLIP)                      # every picture is searched for the face and the turn
    assert 0 < fast["signature"] <= server.AUTH_MATCH_FRAMES  # but the slow match runs on a few only
    assert res["head_turn"] == {"right": True, "left": True, "straight": True}


def test_a_face_that_does_not_match_stays_locked(fast):
    fast["match"] = ("Arun", 0.10)                          # below the SFace threshold
    res = login(server.app.test_client())
    assert res["authorized"] is False and res["ignition"] == "LOCKED" and res["driver"] == "Unknown"


def test_a_photo_without_a_head_turn_stays_locked(fast, monkeypatch):
    it = iter([0.0] * len(CLIP))
    monkeypatch.setattr(server.headturn, "yaw_ratio", lambda row: next(it))
    res = login(server.app.test_client())
    assert res["authorized"] is False and res["ignition"] == "LOCKED"


def test_face_presence_does_not_run_the_face_match(fast):
    res = server.app.test_client().post("/api/face-present", json={"frame": frame_data()}).get_json()
    assert res["faces"] == 1 and fast["signature"] == 0
