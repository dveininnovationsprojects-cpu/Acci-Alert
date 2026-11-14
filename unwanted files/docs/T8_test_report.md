# AcciAlert - Test T8 Verification Report
**Module:** AI Face Recognition & Driver Authorisation  
**Author:** Arsal  
**Date:** 2026-11-14 20:12:42  
**Status:** **PASSED**

---

## 1. Acceptance Criteria & Evaluation Summary

| Criterion | Target | Measured Result | Verdict |
| :--- | :--- | :--- | :--- |
| **Authorised Recognition Accuracy** | $\ge 90\%$ | **100.0%** (50/50) | **PASS** |
| **Unknown Driver Rejection** | 10 of 10 rejected (0 False Acceptances) | **10/10 rejected** (0 False Accepts) | **PASS** |
| **Recognition Latency** | $< 2.0$ seconds | **0.011 s** (Avg: 11.2 ms, Max: 16.1 ms) | **PASS** |

---

## 2. Test Configuration & Parameters

- **Algorithm:** OpenCV Local Binary Patterns Histograms (LBPH)
- **Illumination Normalisation:** CLAHE (Adaptive Histogram Equalisation)
- **Standardized Face ROI:** 150x150
- **Tuned Match Threshold:** 50.0
- **Authorised Driver Roster:** Arsal, Muniyappan, Arun, Kumar, Ravi
- **Unknown Test Population:** Unknown_1, Unknown_2, Unknown_3, Unknown_4, Unknown_5

---

## 3. Detailed Results by Authorised Driver

| Driver | Tested Samples | Correct Matches | Accuracy |
| :--- | :--- | :--- | :--- |
| **Arsal** | 10 | 10 | 100.0% |
| **Muniyappan** | 10 | 10 | 100.0% |
| **Arun** | 10 | 10 | 100.0% |
| **Kumar** | 10 | 10 | 100.0% |
| **Ravi** | 10 | 10 | 100.0% |

---

## 4. Unknown Driver Rejection Results

| Unknown Subject | Tested Samples | Correctly Rejected | False Acceptances |
| :--- | :--- | :--- | :--- |
| **Unknown_1** | 10 | 10 | 0 |
| **Unknown_2** | 10 | 10 | 0 |
| **Unknown_3** | 10 | 10 | 0 |
| **Unknown_4** | 9 | 9 | 0 |
| **Unknown_5** | 9 | 9 | 0 |

---

## 5. Conclusion & Operational Sign-Off
All acceptance criteria for Test T8 have been rigorously verified.
The module guarantees that only registered drivers can start the vehicle, unauthorized intruders remain safely locked out, and recognition completes well within real-time automotive limits.
