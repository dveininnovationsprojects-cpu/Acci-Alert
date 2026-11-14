"""Head-turn check for the face login and for enrolment: the driver turns the head to the RIGHT, then to the LEFT,
then looks STRAIGHT again. A photo cannot do that.

How far the head is turned is measured with the five face points the detector gives (YuNet landmarks): where the
nose sits between the two eyes. Looking straight it is in the middle (0). Turning the head moves the nose toward
the side it turns to. The value is divided by the distance between the eyes, so it does not depend on how far the
person is from the camera, and it follows the line of the eyes, so tilting the head does not disturb it.

Every person has their own "straight" (glasses, seat position), so everything is measured against the first few
pictures of the clip, which the page takes while the driver looks straight at the camera.

Sides are the driver's own: RIGHT means the driver's own right. The page shows a mirror picture, so the driver sees
their face turn to the right of the screen. In the camera's raw picture the nose then moves to the left (smaller x).
"""

from typing import Dict, List, Optional, Sequence

import numpy as np

try:
    from . import config
except ImportError:
    import config


def yaw_ratio(face: Sequence[float]) -> Optional[float]:
    """Head turn of one YuNet face row (x, y, w, h, eye, eye, nose, mouth ...): about 0 straight,
    positive when the nose is toward the right of the camera picture (the driver turned to their own LEFT),
    negative toward the left of the picture (the driver turned to their own RIGHT). None if the points are unusable."""
    try:
        a = np.array([face[4], face[5]], dtype=float)
        b = np.array([face[6], face[7]], dtype=float)
        nose = np.array([face[8], face[9]], dtype=float)
        if abs(a[0] - b[0]) < 1.0 and abs(a[1] - b[1]) < 1.0:
            return None
        left_in_picture, right_in_picture = (a, b) if a[0] <= b[0] else (b, a)
        axis = right_in_picture - left_in_picture                  # along the eyes, pointing to the right of the picture
        eye_distance = float(np.linalg.norm(axis))
        if eye_distance < 8.0:
            return None
        return float(np.dot(nose - (a + b) / 2.0, axis / eye_distance) / eye_distance)
    except Exception:
        return None


def _run(flags: List[bool], start: int, length: int) -> Optional[int]:
    """Index where `length` True values in a row end, searching from `start`; None if there is no such run."""
    count = 0
    for i in range(start, len(flags)):
        count = count + 1 if flags[i] else 0
        if count >= length:
            return i + 1
    return None


def check_sequence(levels: Sequence[Optional[float]]) -> Dict:
    """Did the person turn RIGHT, then LEFT, then look STRAIGHT? `levels` are yaw ratios in time order (None = no
    usable picture). Returns {"passed", "right", "left", "straight", "base", "message"}."""
    values = [v for v in levels if v is not None]
    result = {"passed": False, "right": False, "left": False, "straight": False, "base": None,
              "message": "Turn your head right, then left, then look straight."}
    if len(values) < config.HEAD_MIN_PICTURES:
        result["message"] = "Not enough pictures of your face. Look at the camera and try again."
        return result
    base = float(np.median(values[:config.HEAD_BASE_PICTURES]))          # where "straight" is for this person
    result["base"] = base
    rel = [v - base for v in values]
    need = config.HEAD_RUN
    # RIGHT = the driver's own right = nose toward the left of the camera picture = negative
    end = _run([r <= -config.HEAD_TURN_MIN for r in rel], 0, need)
    result["right"] = end is not None
    if end is not None:
        end = _run([r >= config.HEAD_TURN_MIN for r in rel], end, need)
        result["left"] = end is not None
        if end is not None:
            end = _run([abs(r) <= config.HEAD_STRAIGHT_MAX for r in rel], end, need)
            result["straight"] = end is not None
    result["passed"] = result["right"] and result["left"] and result["straight"]
    if result["passed"]:
        result["message"] = "Head turn verified."
    elif not result["right"]:
        result["message"] = "Turn your head to the RIGHT (the arrow on the right), clearly."
    elif not result["left"]:
        result["message"] = "Then turn your head to the LEFT (the arrow on the left), clearly."
    else:
        result["message"] = "Finally look straight at the camera."
    return result
