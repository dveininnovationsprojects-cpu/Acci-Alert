"""Capture Image: an authorised person starts the car, anyone else does not, and every press is logged."""

import base64

import cv2
import numpy as np
import pytest

from backend import app as server


@pytest.fixture()
def client():
    return server.app.test_client()


def frame_data():
    ok, buf = cv2.imencode(".jpg", np.full((120, 160, 3), 128, np.uint8))
    assert ok
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


def face_events():
    found = server.database.get_events(limit=500, event_types=("AUTH_OK", "UNAUTHORISED DRIVER"))
    return found["events"] if isinstance(found, dict) and "events" in found else found


def press(client, capture):
    return client.post("/api/verify", json={"image": frame_data(), "liveness_check": False, "capture": capture}).get_json()


def test_authorised_person_unlocks_the_car(client, monkeypatch):
    monkeypatch.setattr(server, "recognise", lambda f: (1, [10, 10, 60, 60], "Arun", True, 91.0, ""))
    res = press(client, True)
    assert res["authorized"] is True
    assert res["ignition"] == "UNLOCKED"
    assert res["driver"] == "Arun"


def test_unknown_person_cannot_start_the_car(client, monkeypatch):
    monkeypatch.setattr(server, "recognise", lambda f: (1, [10, 10, 60, 60], "Unknown", False, 20.0, "Face not recognised."))
    res = press(client, True)
    assert res["authorized"] is False
    assert res["ignition"] == "LOCKED"
    assert res["driver"] == "Unknown"


def test_every_capture_is_logged_but_live_frames_log_only_changes(client, monkeypatch):
    monkeypatch.setattr(server, "recognise", lambda f: (1, [10, 10, 60, 60], "Unknown", False, 20.0, "Face not recognised."))
    press(client, False)                       # first result: a change, logged once
    start = len(face_events())
    press(client, False)
    press(client, False)
    assert len(face_events()) == start         # unchanged live frames are not logged again
    press(client, True)
    press(client, True)
    assert len(face_events()) == start + 2     # each press of Capture Image is a logged attempt


# ---- /api/authenticate: the only login check (no continuous monitoring). The driver turns the head right, left, straight.
# Head turn = where the nose sits between the eyes (0 = straight, negative = the driver's own right, positive = own left).

STRAIGHT, RIGHT, LEFT = 0.0, -0.30, 0.30
GOOD = [STRAIGHT] * 4 + [RIGHT] * 4 + [LEFT] * 4 + [0.03] * 4                    # 16 pictures: straight, right, left, straight
ONLY_RIGHT = [STRAIGHT] * 4 + [RIGHT] * 4 + [0.0] * 8
WRONG_ORDER = [STRAIGHT] * 4 + [LEFT] * 4 + [RIGHT] * 4 + [0.0] * 4              # left first, then right
NO_FINAL_STRAIGHT = [STRAIGHT] * 4 + [RIGHT] * 4 + [LEFT] * 8
STILL = [STRAIGHT] * 16
SMALL_MOVES = [0.0, 0.05, -0.06, 0.04] * 4                                        # picture jitter is not a turn


def sequence(results):
    """recognise() that returns the next prepared result on every call."""
    it = iter(results)
    return lambda frame: next(it)


def person(yaw, name="Arun", recognised=True, conf=91.0):
    return (1, [20, 20, 60, 60], name, recognised, conf, "", yaw)


def video(yaws, **who):
    """One prepared result per picture, for a person whose head turn follows the series."""
    return [person(y, **who) for y in yaws]


def authenticate(client, n=16, challenge=True, **extra):
    body = dict({"frames": [frame_data()] * n}, **extra)
    if challenge is True:
        body["challenge"] = client.post("/api/auth/challenge").get_json()["token"]
    elif challenge:
        body["challenge"] = challenge
    return client.post("/api/authenticate", json=body)


def test_the_request_asks_for_right_left_straight(client):
    asked = client.post("/api/auth/challenge").get_json()
    assert asked["steps"] == ["RIGHT", "LEFT", "STRAIGHT"] and asked["token"]
    assert "right" in asked["text"] and "left" in asked["text"] and "straight" in asked["text"]
    assert asked["turn_min"] == server.config.HEAD_TURN_MIN        # the page uses the same numbers as the server


def test_a_driver_who_turns_right_left_straight_unlocks_the_car(client, monkeypatch):
    monkeypatch.setattr(server, "recognise", sequence(video(GOOD)))
    before = len(face_events())
    res = authenticate(client).get_json()
    assert res["authorized"] is True and res["ignition"] == "UNLOCKED" and res["driver"] == "Arun"
    assert res["liveness"] == "PASSED" and res["frames_used"] == 16
    assert res["head_turn"] == {"right": True, "left": True, "straight": True}
    assert server.state.active_driver_auth == 1
    assert len(face_events()) == before + 1          # one login = one log entry, not one per frame


@pytest.mark.parametrize("yaws,missing", [
    (ONLY_RIGHT, "LEFT"), (WRONG_ORDER, "LEFT"), (NO_FINAL_STRAIGHT, "straight"), (STILL, "RIGHT"), (SMALL_MOVES, "RIGHT"),
])
def test_an_incomplete_turn_does_not_unlock(client, monkeypatch, yaws, missing):
    monkeypatch.setattr(server, "recognise", sequence(video(yaws)))
    res = authenticate(client).get_json()
    assert res["authorized"] is False and res["ignition"] == "LOCKED" and res["driver"] == "Unknown"
    assert missing.lower() in res["message"].lower()


def test_a_slow_start_is_judged_from_the_drivers_own_straight(client, monkeypatch):
    lean = -0.19                                     # some people sit slightly turned: measured from their own start
    monkeypatch.setattr(server, "recognise", sequence(video([y + lean for y in GOOD])))
    assert authenticate(client).get_json()["authorized"] is True


def test_the_page_cannot_switch_the_head_turn_off(client, monkeypatch):
    monkeypatch.setattr(server, "recognise", sequence(video(STILL)))
    res = authenticate(client, liveness_check=False).get_json()
    assert res["authorized"] is False and res["ignition"] == "LOCKED"


def test_without_head_data_the_check_cannot_pass(client, monkeypatch):
    monkeypatch.setattr(server, "recognise", sequence(video([None] * 16)))       # e.g. the old LBPH model
    assert authenticate(client).get_json()["authorized"] is False


def test_a_stranger_who_turns_correctly_cannot_start_the_car(client, monkeypatch):
    monkeypatch.setattr(server, "recognise", sequence(video(GOOD, name="Unknown", recognised=False, conf=20.0)))
    before = len(face_events())
    res = authenticate(client).get_json()
    assert res["authorized"] is False and res["ignition"] == "LOCKED"
    assert server.state.active_driver_auth == 0
    assert len(face_events()) == before + 1          # the rejected attempt is in the log


def test_only_the_straight_pictures_decide_who_it_is(client, monkeypatch):
    # turned faces are recognised badly; the straight ones are what counts
    results = [person(y, "Arun", recognised=abs(y) < 0.1) for y in GOOD]
    monkeypatch.setattr(server, "recognise", sequence(results))
    assert authenticate(client).get_json()["authorized"] is True


def test_one_good_picture_among_bad_straight_ones_is_not_enough(client, monkeypatch):
    results = [person(y, "Arun" if i == 0 else "Unknown", recognised=(i == 0)) for i, y in enumerate(GOOD)]
    monkeypatch.setattr(server, "recognise", sequence(results))
    assert authenticate(client).get_json()["authorized"] is False


def test_no_face_means_locked_and_nothing_logged(client, monkeypatch):
    monkeypatch.setattr(server, "recognise", sequence([(0, None, "Unknown", False, 0.0, "No face found.")] * 16))
    before = len(face_events())
    res = authenticate(client).get_json()
    assert res["authorized"] is False and res["detected"] is False and res["ignition"] == "LOCKED"
    assert len(face_events()) == before


def test_two_people_in_view_is_refused(client, monkeypatch):
    monkeypatch.setattr(server, "recognise", sequence([(2, None, "Unknown", False, 0.0, "")] * 16))
    res = authenticate(client).get_json()
    assert res["authorized"] is False and "one person" in res["message"]


@pytest.mark.parametrize("count", [0, 1, 2, 61, 100])
def test_the_number_of_frames_is_checked(client, count):
    assert authenticate(client, n=count).status_code == 400


def test_a_login_without_a_valid_request_is_refused_and_changes_nothing(client, monkeypatch):
    monkeypatch.setattr(server, "recognise", sequence(video(GOOD * 4)))
    server.state.active_driver_auth = 0
    for token in (False, "made-up-token"):
        res = authenticate(client, challenge=token)
        assert res.status_code == 400 and "expired" in res.get_json()["message"]
    assert server.state.active_driver_auth == 0


def test_a_request_works_once_only(client, monkeypatch):
    monkeypatch.setattr(server, "recognise", sequence(video(GOOD * 2)))
    token = client.post("/api/auth/challenge").get_json()["token"]
    assert authenticate(client, challenge=token).get_json()["authorized"] is True
    assert authenticate(client, challenge=token).status_code == 400            # replaying the same request


def test_an_old_request_expires():
    book = server.ChallengeBook()
    token = book.issue(now=1000.0)["token"]
    assert book.take(token, now=1000.0 + server.config.CHALLENGE_TTL_SECONDS + 1) is False
    assert book.take(book.issue(now=1000.0)["token"], now=1001.0) is True


def test_unused_requests_are_capped():
    book = server.ChallengeBook()
    for _ in range(server.config.CHALLENGE_MAX_OPEN + 20):
        book.issue()
    assert len(book._open) <= server.config.CHALLENGE_MAX_OPEN


def test_a_locked_car_unlocks_again_after_lock_vehicle(client, monkeypatch):
    monkeypatch.setattr(server, "recognise", sequence(video(GOOD * 2)))
    assert authenticate(client).get_json()["authorized"] is True
    assert client.post("/api/command", json={"command": "AUTH", "value": 0}).status_code == 200
    assert server.state.active_driver_auth == 0
    assert authenticate(client).get_json()["authorized"] is True
    assert server.state.active_driver_auth == 1


def test_a_failure_inside_the_check_leaves_the_car_locked(client, monkeypatch):
    server.state.active_driver_auth = 1

    def boom(frame):
        raise RuntimeError("camera model crashed")
    monkeypatch.setattr(server, "recognise", boom)
    res = authenticate(client)
    assert res.status_code == 500
    assert server.state.active_driver_auth == 0


# ---- the head-turn measure on its own

def test_yaw_ratio_follows_the_nose():
    from backend import headturn
    face = lambda nose_x, nose_y=130, eyes=(100, 100, 140, 100): [0, 0, 0, 0, *eyes, nose_x, nose_y]
    assert headturn.yaw_ratio(face(120)) == pytest.approx(0.0)
    assert headturn.yaw_ratio(face(110)) == pytest.approx(-0.25)          # nose toward the left of the picture = driver's own right
    assert headturn.yaw_ratio(face(130)) == pytest.approx(0.25)
    assert headturn.yaw_ratio(face(110, eyes=(140, 100, 100, 100))) == pytest.approx(-0.25)   # eye order does not matter
    assert headturn.yaw_ratio(face(120, eyes=(100, 100, 100, 100))) is None                    # no distance between the eyes
    assert headturn.yaw_ratio([1, 2]) is None


def test_a_tilted_head_is_not_a_turn():
    from backend import headturn
    # head rolled 25 degrees: eyes and nose rotate together around the middle of the face
    import math
    a = math.radians(25)
    rot = lambda x, y: (120 + (x - 120) * math.cos(a) - (y - 110) * math.sin(a), 110 + (x - 120) * math.sin(a) + (y - 110) * math.cos(a))
    (rx, ry), (lx, ly), (nx, ny) = rot(100, 100), rot(140, 100), rot(120, 130)
    assert abs(headturn.yaw_ratio([0, 0, 0, 0, rx, ry, lx, ly, nx, ny])) < 0.01


def test_check_sequence_needs_enough_pictures():
    from backend import headturn
    res = headturn.check_sequence(GOOD[:8])
    assert res["passed"] is False and "Not enough" in res["message"]
    assert headturn.check_sequence([None] * 30)["passed"] is False
    assert headturn.check_sequence(GOOD[:4] + [None, None] + GOOD[4:])["passed"] is True      # missed pictures are skipped


def test_one_picture_is_not_a_turn():
    from backend import headturn
    spike = [0.0] * 4 + [-0.4] + [0.0] * 3 + [0.4] + [0.0] * 6                                 # single odd pictures
    assert headturn.check_sequence(spike)["passed"] is False
