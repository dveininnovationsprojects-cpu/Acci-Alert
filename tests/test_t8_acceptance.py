"""
Acceptance Test T8 Pytest Suite
Verifies:
  - Acceptance Criterion 1: Authorised recognition accuracy >= 90%
  - Acceptance Criterion 2: Unknown 10 of 10 rejected (0 false acceptances)
  - Acceptance Criterion 3: Recognition latency < 2.0s
"""

import pytest
from ai.test_recognition import run_t8_acceptance_tests


def test_t8_acceptance_criteria():
    report = run_t8_acceptance_tests()
    assert report["overall_status"] == "PASS", "Acceptance Test T8 must pass all criteria"
    assert report["criteria"]["accuracy_ge_90_pct"]["pass"] is True
    assert report["criteria"]["accuracy_ge_90_pct"]["value"] >= 90.0
    assert report["criteria"]["unknown_10_of_10_rejected"]["pass"] is True
    assert report["criteria"]["unknown_10_of_10_rejected"]["false_acceptances"] == 0
    assert report["criteria"]["latency_lt_2s"]["pass"] is True
    assert report["criteria"]["latency_lt_2s"]["avg_latency_sec"] < 2.0

