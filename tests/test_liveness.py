"""
Unit Tests for LivenessDetector Module
"""

import numpy as np
import pytest
from ai.liveness import LivenessDetector
from ai.face_detector import FaceDetector


@pytest.fixture
def liveness():
    return LivenessDetector()


def test_liveness_initial_state(liveness):
    assert not liveness.is_passed
    assert liveness.blink_count == 0
    assert "blink" in liveness.status_message.lower()


def test_liveness_reset(liveness):
    liveness.blink_count = 3
    liveness.is_passed = True
    liveness.reset()
    assert not liveness.is_passed
    assert liveness.blink_count == 0


def test_liveness_manual_pass(liveness):
    # Set passed condition manually and verify immediate True
    liveness.is_passed = True
    passed, msg = liveness.update(np.zeros((100, 100, 3), dtype=np.uint8),
                                  np.zeros((100, 100), dtype=np.uint8),
                                  (10, 10, 80, 80))
    assert passed is True
    assert "verified" in msg.lower()

