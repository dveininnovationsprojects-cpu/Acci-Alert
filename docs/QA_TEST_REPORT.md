# AcciAlert QA test report

Date: 14 Nov 2026
Checked by: Prasanth (software team), with automated tests and a manual page review
Code: branch `prasanth`, all of `backend/` and `frontend/`
Computer: Windows 11, Python 3.11.9, OpenCV 5.0.0

## 1. Result in one table

| Check | Result |
|---|---|
| Automated tests | **139 passed, 5 skipped, 0 failed** (about 17 s) |
| JavaScript syntax (12 files) | All pass |
| Python syntax and unused imports (all backend files) | Clean after fixes |
| Server response times (20 runs each) | All under 12 ms |
| Page layout (4 pages at 1280 px) | No overflow. Two layout faults fixed |
| Real webcam, real ESP32 | **Not tested** (see section 7) |

The 5 skipped tests need the face photos in `backend/dataset_test/`, which were deleted from the project
(commit `6eb2fa2`). They skip on their own when the photos are missing.

## 2. Automated tests by file

| Test file | What it covers | Passed | Skipped |
|---|---|---|---|
| test_zones.py | Zone engine: enter, exit, pre-warning, overspeed, bad GPS | 24 | 0 |
| test_hardening.py | Bad input, limits, CSV, threads, database safety | 52 | 0 |
| test_zone_api.py | Zone manager API | 4 | 0 |
| test_backup.py | Database backup script | 4 | 0 |
| test_dashboard_api.py | Dashboard pages and REST API, simulator, map maths | 9 | 0 |
| test_sface.py | Live enrolment, saved photos, license number, movement check, `/api/verify` | 26 | 0 |
| test_server_logic.py | Zone engine inside the server, telemetry saving, database repair | 5 | 0 |
| test_db_logger.py | Face login logging | 4 | 0 |
| test_serial_comm.py | ESP32 serial sender (mock mode) | 4 | 0 |
| test_liveness.py | Old blink detector | 3 | 0 |
| test_face_detector.py | Haar face detector | 3 | 2 |
| test_face_recognizer.py | LBPH recogniser | 1 | 2 |
| test_t8_acceptance.py | T8 accuracy run | 0 | 1 |
| **Total** | | **139** | **5** |

The tests now use a temporary data folder (`ACCIALERT_DATA_DIR`), so they no longer write into your real
database files.

## 3. Problems found and fixed

| # | Problem | How serious | Fix |
|---|---|---|---|
| 1 | **The danger-zone engine never ran in the merged server.** `process_telemetry_packet` called `zone_engine.update(...)` with wrong argument names. The error was hidden in a debug log, so no zone events were created. | High | Corrected the call. Zone events are saved, shown in the dashboard table and sent to the browser. Errors are now logged as warnings. |
| 2 | **Telemetry was no longer saved.** The 1 per second save was lost in the merges. | High | Restored in `app.py`. A test checks that 5 packets in a row save one row. |
| 3 | **`accialert_zones.db` was damaged** (the telemetry table could not be read). | High | The server now checks the file on start. A damaged file is kept as `*.corrupt-<time>`, a new file is created and zones, drivers and events are copied back. Checked on a copy of your real damaged file: 6 zones and 25 events recovered. |
| 4 | **Tests wrote into the real database files** while the server was running, a likely cause of problem 3. | Medium | `conftest.py` points all tests at a temporary folder. |
| 5 | **Face check crashed on every face** (`manual_pass` did not exist). | High | Fixed earlier today, now covered by tests. |
| 6 | The face box did not follow the face, and a leftover call stopped the camera loop during enrolment. | High | Fixed earlier today. |
| 7 | Every face check wrote a log entry and sent `AUTH` to the car. | Medium | Done only when the result changes. |
| 8 | `python -m backend.app` could fail because `db.py` and `zone_api.py` only used plain imports. | Medium | Both now work as a script or as a package. |
| 9 | The dashboard counter downloaded up to 500 events every 2 s. | Medium (lag) | New small call `/zm/api/zone-status`, every 3 s. The reply is a few bytes. |
| 10 | The face tab kept sending frames while the page or tab was hidden. | Low (lag) | The check is skipped when hidden. |
| 11 | Fleet page had 10 cards in 3 columns, leaving one card alone on a row. | Low (layout) | Two rows of five on wide screens. |
| 12 | Icons, the "Muniyappan & Arsal" subtitle and a teal button came back with the merges. | Low (look) | Removed again. The button now matches the amber theme. |
| 13 | About 24 unused imports in 8 backend files. | Low | Removed. |
| 14 | A test expected the name "Muniyappan" on the page. | Low | Now checks the system title. |

## 4. Speed

Measured with the Flask test client on this computer (average of 20 calls):

| Call | Time |
|---|---|
| GET `/api/status` | 0.4 ms |
| GET `/api/events` | 4.3 ms |
| GET `/api/zones` | 1.7 ms |
| GET `/zm/api/zones`, `/zm/api/events`, `/zm/api/zone-status` | 0.3 ms each |
| GET `/api/enrolled` | 1.7 ms |
| POST `/api/verify`, no face in view | 11.9 ms |
| One telemetry step (alerts, zone engine, save) | 1.5 ms |
| Face match with SFace, earlier test | about 15 ms |

The requirement is a face decision in under 2 seconds, and a check takes about 0.1 s even with the 0.5 s
timer. Telemetry arrives 1 time per second in the simulator and about 5 per second from the ESP32. The server
handles a step in about 1.5 ms, so there is plenty of room.

## 5. Face recognition check

Run on the old test photos (taken from Git history), with the SFace model, 5 drivers enrolled from 20 photos each:

| Check | Result |
|---|---|
| Known drivers accepted | 50 of 50 |
| Unknown people rejected | 50 of 50, no false accepts |
| Closest unknown score | 0.28 (limit 0.40) |
| Unlocks only after head movement | Yes (locked for 2 checks, unlocked on the 3rd) |

Warning: these test photos are edited copies of a few stock pictures, so these numbers only show the code works.
They are **not** the T8 result.

## 6. Page review (browser, 1280 px wide)

| Page | Result |
|---|---|
| `/` dashboard | Header, 9 cards in 3 columns, charts, map, tabs: all in place. Face Recognition tab fits the page. |
| `/dashboard` fleet page | 10 cards: fixed to 2 rows of 5. |
| `/auth.html` face login | Layout fine. Capture button colour and camera icon fixed. |
| `/zm/` zone manager | Map, form, zone list and event table all in place. |

## 7. Not tested, and still open

1. **A real webcam.** The face box, enrolment and unlock were tested through the API, not with a person in
   front of the camera. This must be tried before the demo.
2. **The ESP32 and the serial link.** Only the mock mode is tested. Zone changes are still not sent to the
   ESP32 (format not agreed with Selvamani).
3. **Test T8 with real people.** It needs live photos of 3 to 5 drivers in different light and 5 unknown people.
4. **Phone-width layout.** The browser pane could not show a real 375 px width, so small screens were not
   judged. The pages are meant for a laptop.
5. **The old server.** The server already running on port 5173 started before these fixes. Restart it to use them.
6. **Security.** The server has no login. Anyone on the network can call the API (for example
   `/api/drivers/select`). It is meant for the local demo only.
7. **Biometric data.** `backend/data/face_gallery.npz` and `backend/dataset/` hold face data. Git ignores both.
   Keep them private.
