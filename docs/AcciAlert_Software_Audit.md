> **Fixes applied after the latest check.** Test suite: 310 passed, 4 skipped (need face photos), 0 failed. Checked in the browser on an isolated copy.
>
> | Item | Result |
> |---|---|
> | 1 No login on the API | **Not done on purpose** (no login or key was requested). Safe parts done: commands limited to STOP, RESET, ACK, AUTH, LIMIT with value checks (also on `/api/serial/send` and the socket); face threshold checked against the range of the recogniser in use; `/api/drivers/select` only accepts registered drivers, and `make_active` is ignored so a driver can only take the wheel through a face scan. `DELETE /api/logs` is kept because the Clear Logs button uses it; it now leaves a `LOGS_CLEARED` event behind. |
> | 2 Zone form false "saved" | **Fixed.** A server refusal shows the server's message and keeps the form open; the browser-only save is used only when the server cannot be reached and says so |
> | 3 Bad telemetry values | **Fixed.** Non-numbers, NaN and missing fields fall back to safe defaults; the rest of the packet (for example an accident) is still processed |
> | 4 Toggle Auth vs red banner | **Fixed** at the cause: every AUTH command now updates the server lock state, so the alert clears at once. The banner itself stays on screen until Acknowledge, which is how the banner is designed |
> | 5 Event severity | **Fixed.** New `severity` column (older databases are upgraded and filled), alert events store their severity, `severity=` filter, Severity column and filter in Event history, Severity in the CSV |
> | 6 Audit Logs counters | **Fixed.** The audit trail lists only face-login events with the real match score and liveness result (no invented 85 % / PASSED), and the counters count only those |
> | 7 Calibration screen | **Fixed.** The slider shows the recogniser really in use (SFace score or LBPH distance), with its own range, and the value is now applied to the live recogniser. The old "Calibrated range 16-20" text is gone |
> | 8 Traffic signal leftovers | **Removed** from the server, simulator, browser scripts, fleet page, CSS, README and protocol notes |
> | 9 Connection leaks | **Fixed** in all `database.py` functions (connections always close) |
> | 10 Silent fake data | **Fixed.** If the connection to the server is lost, the header turns red: "OFFLINE: showing simulated data from this browser" |
> | 11 ESP32 offline warning | **Fixed.** After 5 seconds without readings the header shows "No data from the vehicle for N s" and a toast; it clears when data returns. `/api/status` now reports `last_telemetry_age` |
> | Tests | Photo tests now skip when the face photos are missing; the timing-sensitive telemetry test no longer fails under load |
> | Still open | Login or key for the dashboard API (by request), Twilio SMS, the live camera and a real ESP32 were not tested |

> **Status after pulling `origin/main` (commit `58307c7`, 2026-11-14).** Sections 1-11 below describe the code at commit `c082cb6`. The team has since fixed many of those findings. Re-checked on the new main with an in-process probe:
>
> | Finding | Now |
> |---|---|
> | C1/C2 `/api/verify` crash and missing liveness method | **Fixed.** Enrolled faces unlock, an unknown face stays locked, garbage input gives 400 |
> | C3 zone engine called with wrong arguments | **Fixed** (engine call corrected, zone events written, telemetry saved once per second) |
> | C4 car starts unlocked | **Fixed** (starts as Unknown, auth 0) |
> | H4/Z6 two zone systems | **Fixed.** One zone list in the Zone Manager database; the old dashboard zones were imported (now Z07 onward) |
> | Z1/Z5 zone edit and validation | **Fixed.** The dashboard saves an edit by posting the same code; bad values give 400 |
> | M1 `limit=abc` gives 500 | **Fixed** (400) |
> | Spelling mismatch `UNAUTHORIZED` / `UNAUTHORISED` | **Fixed** |
> | **C5 no login:** `DELETE /api/logs`, `POST /api/config` (threshold 999), `POST /api/drivers/select`, any command text such as `FORMAT_DISK` all still return 200 | **Still open** |
> | Malformed telemetry (`"alc":"abc"`) raises `ValueError` in the packet handler | **Still open** |
> | RED signal raises a "violation" while the car is stopped (traffic signal is not wanted) | **Still open** (remove) |
> | Events have no `severity` column; `severity=` filter ignored | **Still open** |
> | Zone alert ignores the GPS-fix flag | **Fixed by this change** (re-applied on the new main) |
> | Connection leak in other `database.py` functions | **Still open** (not re-tested) |
> | SMS alerts (Circuit Digest Cloud, India, free) | **Built**: `backend/sms_alert.py`, setup with `python -m backend.tools.sms setup`. Needs the API key, template ID and verified numbers. Not yet tested with the real service |
>
> Added on top of main: the ESP32 Wi-Fi API (`POST /api/v1/telemetry`, `docs/ESP32_TELEMETRY_API.md`). My earlier "zone edit" and "default zone sets" changes were dropped because main's one-zone-list design replaced them. The default-zone data (3 Chennai junctions, 5 track zones) is kept in the git stash.
> Two tests, `test_face_detector.py::test_detect_single_face` and `::test_detect_multiple_faces`, fail on a checkout without `backend/dataset` (the face photos were removed from the repository).

# AcciAlert – Software Audit Report

**Scope:** software only (Flask backend, face login, zone engine, database, dashboard front-end). Hardware and firmware were not tested.
**Date:** 2026-11-14
**Baseline:** `AcciAlert_Requirements_Document.pdf` v1.0 and the six hardware/circuit PDFs.
**Method:** read all backend code, ran the 113 existing pytest tests, called every REST endpoint with good and bad input through the Flask test client, fed hand-made telemetry packets into the alert pipeline, and opened the live dashboard in a browser.

---

## 1. Verdict

The project looks complete on screen, and all 113 automated tests pass. **Several core features do not work when they are run through the real application**, because the tests check each module on its own and never check how `app.py` calls it.

| Area | Status |
|---|---|
| Dashboard (live cards, charts, map, event table, simulation) | Works |
| Alert pipeline (accident, alcohol, obstacle, overspeed, unauthorised) | Works for normal input, crashes on malformed input |
| Face recognition library (LBPH) | Works on its own (30/30 enrolled, 0 false accepts) |
| **Face login through the server (`/api/verify`)** | **Broken – every request returns HTTP 500** |
| **Zone engine events (ENTER / EXIT / OVERSPEED / pre-warning)** | **Broken in the server – the call raises an error that is hidden** |
| Zone manager (`/zm`) | Works, but is disconnected from the dashboard zones |
| Traffic signal | **Out of scope (not needed).** Leftover signal code should be removed (see §4) |
| **Emergency SMS (Twilio)** | **Not implemented – no SMS code at all** (see §5) |
| Telemetry storage (24 h) | Not implemented – the function exists but is never called |
| Security | None – no login, every endpoint open |

---

## 2. Defects found (ordered by severity)

### Critical – the feature does not work

**C1. Face login API always fails.** `backend/app.py:489-503`
`face_detector.detect_faces()` returns a `FaceDetectionResult` object, but `app.py` treats it as a list (`len(faces)`, `faces[0]`). Result: `POST /api/verify` returns `500 object of type 'FaceDetectionResult' has no len()` for every image, known or unknown, with or without liveness. The server-side recogniser, the ignition lock/unlock and the `AUTH` command to the ESP32 can never be reached from this route. Tested with an enrolled driver (Arun) and an unknown (Unknown_1).
**Fix:** use `result.face_count`, `result.primary_box` and `result.primary_roi` (see `face_detector.py:15-30`; `extract_face_roi` is probably not needed) and add a test that calls the real route with a photo. Also handle `MULTIPLE_FACES` explicitly.

**C2. Liveness check calls a method that does not exist.** `backend/app.py:513`
`liveness_detector.manual_pass()` is not defined in `LivenessDetector`. It is only reachable after C1 is fixed, then it fails with `AttributeError`. Separately, `/api/liveness` (`app.py:556`) is a stub that always answers "passed, 1 blink, motion 3.2" – it checks nothing, so anti-spoofing is not actually offered by the server.
**Fix:** implement a real multi-frame liveness session (the class has `update()`, which needs a frame stream) and remove the hard-coded response.

**C3. Danger-zone engine is never fed correctly, so zone events are never produced.** `backend/app.py:331-352`
The call is `zone_engine.update(lat=, lon=, speed=, driver=, timestamp=)` but the method is `update(self, lat, lon, speed_kmh=0.0, fix=1)`. Python raises `TypeError: unexpected keyword argument 'speed'`; the code catches it with `except Exception` and logs it at **debug** level, so nothing is visible. Consequences: no `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_APPROACH` or `OVERSPEED_IN_ZONE` events in the live system, and no `zone_event` socket messages, even though the README and the final report say they exist. The unit tests pass because they call the engine directly with the correct names.
**Fix:** `zone_engine.update(lat, lon, speed_kmh=..., fix=telemetry["fix"])`, read `ev["zone_id"]`/`ev["zone_name"]` (the code reads `ev.get("zone")`, which does not exist), log at `warning`, and add an integration test.

**C4. The vehicle starts unlocked.** `backend/app.py:85-86`
`active_driver_name = "Muniyappan"` and `active_driver_auth = 1` at start-up. The requirement says "unknown face = vehicle stays locked". Nobody has to be recognised before the system reports an authorised driver.
**Fix:** start with `auth = 0` / "No driver" until a successful verification.

**C5. Anyone on the network can unlock the car or wipe the logs.** `app.py:73-76, 454-460, 568-576, 839-851`
No authentication anywhere; `CORS(app)` and `cors_allowed_origins="*"`; server binds `0.0.0.0` (`config.py:56`). Verified: `POST /api/drivers/select {"name":"Hacker","authorized":1}` returns success and sets the driver as authorised; `DELETE /api/logs` erased the whole audit log; `POST /api/config {"threshold":999}` makes the face matcher accept anyone; `/api/command` accepts any text (`FORMAT_DISK` was "executed").
**Fix:** a login/API key for all write routes, bind to `127.0.0.1` for the demo, restrict CORS, whitelist commands, remove or protect `DELETE /api/logs`, validate `threshold` range.

### High

**H1. Emergency SMS (Twilio) is missing.** See §5.

**H2. (Withdrawn)** Traffic signal was listed here as missing. It is not needed, so it is no longer a defect; the leftover signal code is a clean-up item (§4, U13).

**H3. No telemetry is stored.** `db.log_telemetry()` exists and the purge job runs hourly, but `log_telemetry` is never called (also `state.last_telemetry_saved` is never used). Requirement 9.1 ("telemetry every 1 s, rolling 24 h, for charts and replay") is not met.

**H4. Two separate zone systems that do not talk to each other.** The dashboard uses `database.py` (`/api/zones`, 4 zones, codes Z01-Z04, real Chennai coordinates); the zone manager uses `db.py` + `zones.json` (`/zm/api/zones`, 6 zones, miniature "blackspot" coordinates). Editing in one does not update the other, the simulator, or the `ZoneEngine`. In `app.py` the same position is checked twice – once by `find_active_danger_zone` (dashboard zones) and once by `zone_engine` (zone-manager zones) – so they can disagree. There is also no `ZONES` command to the ESP32: `mount_zone_manager(...)` is called without `on_zones_changed` (README admits this).
**Fix:** one zones table and one engine; call `zones_to_esp32_cmd` on every change.

**H5. Zone alert fires without a GPS fix.** `app.py:213` ignores `fix`. Test: packet with `fix=0`, position inside Z01, speed 50 → raises *DANGER ZONE SPEEDING*. The zone engine itself ignores no-fix packets correctly; the alert code does not.

**H6. Two incompatible `events` tables share one database file.** `db_logger.py` creates `events(type, severity, driver, …)` in `config.DB_PATH`, while `database.py` creates `events(event_type, driver_name, …)` in the same file (`DATABASE_PATH`). Whichever runs first wins; `DatabaseLogger.log_auth_event()` then inserts into columns that do not exist. It also posts to `POST /api/events`, a route that only allows GET (405). `DatabaseLogger` is created in `app.py:105` but never used. Same problem for the `drivers` table.

**H7. Malformed or unusual telemetry crashes the packet handler.** `process_telemetry_packet` does `int(packet.get("alc"))` / `float(...)` with no protection. A packet with `"alc":"abc"` raises `ValueError` inside the serial reader thread callback. Only the outer `except Exception` in `_reader_loop` keeps the thread alive, and the packet (possibly an accident) is lost.

**H8. Database lock errors (confirmed in the second test, single process).** While the background loop and API calls ran together, `sqlite3.OperationalError: database is locked` appeared in both the telemetry loop and the REST routes. During that run a second server process was also writing to the same file, so the lock may have been caused by two processes rather than one. Either way, every call opens a new connection and re-runs `PRAGMA journal_mode=WAL`, so writes from several threads can collide; in the telemetry loop that means a dropped accident event. Re-test with a single process before treating this as confirmed.
**Fix:** one connection (or a pool) with a write lock, set WAL once, retry on busy.

**H9. Accident / alerts are not latched and the requirement "never drop an accident" is only partly met.** Alerts are re-emitted on every tick (every second, or 5 Hz from a real ESP32) with no "alert cleared" message, and DB logging is throttled to once per 5 s per type. An accident that appears for one packet disappears from the dashboard on the next. No critical alert stays until **Acknowledge** is pressed, as section 8.3 requires for critical zones.

### Medium

| # | Finding | Where |
|---|---|---|
| M1 | Invalid input returns HTTP 500 instead of 400: `limit=abc`, non-numeric zone latitude, duplicate zone `code`, duplicate driver licence, latitude 999. | `app.py:742, 797, 829`, `database.py` |
| M2 | Zone POST accepts nonsense: radius -5, speed limit -10, severity "BOGUS", lat 999 stored/crashes. `zones.validate_zone` exists but the dashboard route does not use it. | `app.py:776-802` |
| M3 | `/api/events` advertises `driver` and `severity` filters (docstring) but ignores them; no `from`/`to` date filter on this route; `limit` is unbounded (negative accepted). | `app.py:736-757` |
| M4 | Event table has no `severity` or `zone id` column (requirement 9.1). Severity is guessed in `/api/logs` by `"OK" in event_type`; `match_score` is a constant (85 or 42) and `liveness_result` a constant `"PASSED"` – fake data shown in the audit table. | `app.py:596-611`, `database.py:23` |
| M5 | "Critical" no-entry zones should stop the car and need Acknowledge; the engine sets `critical=True` but nothing in `app.py` stops the vehicle or waits for acknowledgement. Zone limit 0 is also used to mean "lift limit" (`LIMIT 0` at `app.py:246`), which conflicts with "0 = stop". | `app.py:237-247` |
| M6 | Obstacle alert fires at the **danger** threshold with severity CRITICAL; the requirement has <40 cm = slow (medium) and <20 cm = brake. Overspeed uses a fixed 80 km/h, not the active zone/limit. The miniature car never reaches 80 km/h, so the alert can never fire on the real model. | `config.py:76-95` |
| M7 | Thresholds on the server (tilt 45°, 2.5 g) differ from the document (tilt > 60° for > 1 s). A one-sample spike triggers an accident; there is no "for 1 second" rule. | `config.py:90-94`, `app.py:156` |
| M8 | Mode switch does not connect anything: `/api/mode` only flips a flag. In serial mode the simulator still processes every command, and with the ESP32 unplugged the dashboard silently shows nothing (no "no data" / stale-data indicator; serial `get_status()` with `alive` exists but is not exposed). | `app.py:877`, `serial_link.py:172` |
| M9 | Serial command format does not match the protocol document: server sends `AUTH:1`, `LIMIT:40` as plain text; the document and README specify JSON lines such as `{"cmd":"AUTH","ok":1,"driver":"Arun"}`. The JSON sender exists (`ESP32SerialCommunicator`) but is never used, and `face_auth.py` (the standalone login) is not used by the server either. The two senders could open the same COM port. | `serial_link.py:109-129` vs `193-260` |
| M10 | Two separate face-login implementations: the Python LBPH model (3 drivers: Arun, Kumar, Ravi) and a browser-side JavaScript engine with 127 KB of embedded face images (`frontend/js/enrolled_data.js`). The page uses the browser one and stores drivers/logs in `localStorage`, so what the face page shows is **not** what the server or the database knows. | `frontend/js/app.js`, `face_engine.js` |
| M11 | Accuracy report is optimistic and partly empty: roster says 5 drivers but **Arsal and Muniyappan have 0 test samples** and no photos/model entry; 48 unknown samples tested, not the 50 implied; the "10/10" criterion is satisfied by five different unknown people, not by 10 attempts of one. The report file is rewritten every time pytest runs. | `docs/T8_test_report.md` |
| M12 | Biometric data in the repository and the web root: face photos in `backend/dataset*` and base64 faces in a publicly served JS file. Privacy risk. | repository |

### Low

- `SECRET_KEY` default `"accialert-dev-key"`; `allow_unsafe_werkzeug=True`; development server only.
- Shared mutable `state` object is read/written from several threads with no lock.
- The background telemetry thread starts at import time, so importing `app` (tests, tools) starts it, and `debug=True` would start two.
- Starting a second copy of the server on Windows does not fail with "port in use" – two processes were listening on 5173 at the same time and answering at random. Add a single-instance check.
- Wide `except Exception: pass` blocks hide faults (zone engine, `db_logger._notify_dashboard_api`).
- `requirements.txt` has no upper pins; `requests` is imported but only needed by the unused logger; `face_recognition`/`dlib` mentioned in the requirements are not used (OpenCV LBPH is fine, but the document should say so).
- Test suite gaps: nothing exercises `app.py` routes with real face images, zone integration, `/api/verify`, SMS, or malformed packets. `test_hardening.py` (52 tests) tests the engine and API in isolation.

---

## 3. What was tested and how it behaved

| Feature | Result |
|---|---|
| 113 existing pytest tests | 113 passed (14 s) |
| `GET /api/health`, `/api/status` | OK |
| Dashboard page, map (Leaflet), charts (Chart.js), live cards, buttons | Load without console errors; live simulated data shown |
| `POST /api/verify` (enrolled + unknown face) | **500 on all four calls** |
| `POST /api/liveness` | Stub – always "passed" |
| Zone engine through `app.py` | **TypeError swallowed – no events** |
| `GET/POST/DELETE /api/zones` | Works with valid data; **500** on bad/duplicate data; accepts invalid values |
| `/zm/api/zones`, `/zm/api/events` | OK |
| `/api/events` filters, CSV export | Type filter OK; `limit=abc` → **500**; severity/driver filters not implemented |
| Alerts: RED while stopped | Raises CRITICAL "traffic violation" even at speed 0 (wrong) |
| Alerts: GPS `fix=0` inside a zone | False zone-speeding alert |
| Alerts: bad numeric telemetry | Exception in handler |
| Authorisation by API | Anyone can authorise a driver, clear the log, change the threshold |
| Concurrent writes | `database is locked` errors (two server processes were running, see H8) |

---

## 4. Traffic signal – not needed; remove the leftover code

The traffic signal is **not a required feature for this project**, so it is not counted as a missing item. The requirements document mentions it, but the decision is to leave it out. The software still contains partial signal code that does nothing useful and one wrong rule. Remove it so the system matches the scope:

| Leftover item | Where | Problem | Action |
|---|---|---|---|
| RED SIGNAL alert ("Traffic violation") | `app.py` (check 7) | Fires as CRITICAL even when the car is stopped; creates false alerts and log entries | Delete the check |
| `signal` field handling | `app.py` telemetry packet, `simulator.py`, `client_simulator.js` | Carries an unused value | Delete (the ESP32 may still send the field; ignore it) |
| `SIGNAL` command | `/api/command`, `simulator.handle_command`, `alerts.js` | No UI uses it | Remove from the command whitelist |
| `red_signal` demo scenario and "TRAFFIC SIGNAL STOP" text | `simulator.py`, `alerts.js` | Test for a feature that is not wanted | Delete |
| `SIGNAL` in protocol and docs | `firmware/protocol.txt`, README, requirements | Documents a feature that will not exist | Remove or mark "not used"; tell Selvamani so the firmware does not wait for it |

Nothing needs to be added to the dashboard for signals.


## 5. Emergency SMS – replaces the GSM module (now built with Circuit Digest Cloud; Twilio was dropped)

**Decision:** the team will send the emergency SMS with **Twilio** and will not use the SIM800L GSM module. (An earlier version of this section assumed the opposite; it has been rewritten.)

**Finding:** the software has **no SMS code of any kind and no Twilio code**. The requirement (§1, §7.3) says an accident must send "Accident Alert! Lat/Lon" to the emergency contact; today nothing does. Test T7 is `PENDING`.

### What must be built (software)

1. **Twilio sender module** (`sms_alert.py`): calls the Twilio REST API (the `requests` package is already a dependency, or use the official `twilio` package). It must run in a **background thread**, never inside the telemetry loop, so a slow network cannot delay alerts.
2. **Trigger:** in `evaluate_and_dispatch_alerts`, when an ACCIDENT alert starts (a new accident, not every packet), queue one SMS. Decide whether alcohol-detected should also send one (the requirement says accident only).
3. **Message text** (from §7.3): `ACCIDENT ALERT! Vehicle AcciAlert-01. Lat: 13.0827 N, Lon: 80.2707 E. maps.google.com/?q=13.0827,80.2707`. If there is no GPS fix, send the last known position and mark it "last known".
4. **Rules:** send once per accident; resend after 60 s if not acknowledged; stop when the operator presses Acknowledge; retry on network or API error with a limit; a per-hour cap so a fault can never send hundreds of messages.
5. **Settings:** `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` and `EMERGENCY_CONTACT_NUMBER` from environment variables or an untracked settings file. **Never put them in the repository, the docs or the web page.** Add the settings file to `.gitignore`.
6. **Events:** log `SMS_SENT` (with the Twilio message SID and the number masked, for example `+91*****1234`) and `SMS_FAILED` (with the reason) in the event table, and add both to the Event history filter.
7. **Dashboard:** an SMS status card (Not configured / Ready / Sending / Sent time / Failed), an emergency-contact field, and a **Send test SMS** button so T7 can be run without causing a crash.
8. **Tests:** unit tests with Twilio mocked (no real messages): one accident gives one SMS; no fix gives "last known"; resend after 60 s; Acknowledge stops resends; failure is logged and retried; rate cap works.

### Risks the team should know about

| Risk | Why it matters | What to do |
|---|---|---|
| **Needs the laptop and internet** | The document says safety rules must work even if the laptop is disconnected (§7.3). With Twilio, an accident while the laptop is off, offline or unplugged sends **no SMS**. | Accept and document this limit, or keep a GSM module as a backup. |
| **Trial account limits** | A Twilio trial can only message verified numbers and prefixes the text with a trial notice. | Verify the contact number first, or upgrade. |
| **Cost** | Each SMS is billed. | Per-hour cap, and protect the test endpoint. |
| **Open API plus billing** | The server has no login (audit C5). Anyone on the network could press the test button or trigger an accident scenario and send paid SMS. | Add authentication before enabling Twilio, and rate-limit `/api/sms/test`. |
| **Sending to India** | Operators there may require sender registration; delivery to Indian numbers can be restricted or delayed. | Check Twilio's current rules for the contact's country before the demo. |
| **Privacy** | The message reveals a live location and a phone number. | Mask numbers in logs, keep the number out of Git. |

### Effect on the hardware plan and documents

Because the GSM module is dropped, these items are no longer needed and should be removed or marked "not used": SIM800L module and antenna, the 2G SIM, the 4.0 V buck converter (Rail C) with its 1000 µF capacitor, GPIO25/26 (GSM UART), the `firmware/gsm` module and the GSM tasks and test log (Sidharraj). Update the requirements document (§1, §3, §4, §5.2, §6.1, §10), the wire connection and circuit PDFs, and the hardware component lists. Change test **T7** to "Twilio SMS delivered in under 30 s, 5/5 times". The GPS (NEO-6M) stays, because the position is still needed for the message.

The ESP32 no longer sends the SMS. It only reports the accident and the position in its telemetry, and the laptop sends the message. The telemetry protocol therefore needs **no new GSM fields**, and D10's "GSM signal" indicator is replaced by the SMS status card above.


## 6. Features of the project (as built)

**Driver safety system.** A miniature smart car. A laptop checks the driver's face before the car may run; an ESP32 reads sensors and applies safety rules; a web dashboard shows everything live and records events.

| # | Feature | Where | State |
|---|---|---|---|
| 1 | Live dashboard: speed, alcohol, obstacle distance, tilt/impact, GPS, driver identity, vehicle state, hardware status | `frontend/index.html`, `static/js/dashboard.js` | Working (simulation) |
| 2 | Live charts of speed and alcohol (last 60 s) | `static/js/charts.js` | Working |
| 3 | Map with vehicle marker, trail, danger-zone circles | `static/js/map.js` (Leaflet) | Working |
| 4 | Alert banner with sound: accident, alcohol, obstacle, overspeed, unauthorised, danger zone | `static/js/alerts.js`, `app.py` | Working for valid data |
| 5 | Event log with filter and CSV export | `/api/events`, `/api/events/export` | Partly (no severity/date filters on main route) |
| 6 | Controls: Emergency STOP, Reset, Acknowledge, Toggle Auth | `/api/command` | Working, unprotected |
| 7 | Simulation mode with scenarios (accident, alcohol, obstacle, overspeed, unauthorised, danger zone, normal) | `simulator.py` | Working |
| 8 | ESP32 serial link, COM-port list, connect/disconnect, JSON telemetry reader | `serial_link.py` | Written; untested with hardware |
| 9 | Face recognition (OpenCV LBPH + CLAHE), enrolment, training, camera test tools | `face_*.py`, `tools/` | Library works; API broken (C1) |
| 10 | Liveness (blink + motion) | `liveness.py` | Class works standalone; not wired (C2) |
| 11 | Danger-zone engine: enter, exit with 5 m hysteresis, 20 m pre-warning, overspeed per episode, no-GPS protection | `zones.py` | Engine correct; not wired (C3) |
| 12 | Zone manager page: add, edit, delete, enable/disable zones without restart, CSV | `/zm/`, `zone_api.py`, `db.py` | Working, isolated (H4) |
| 13 | Databases: SQLite events, zones, drivers, command log; backup script keeping 10 copies; telemetry purge job | `database.py`, `db.py`, `backup.py` | Working; telemetry never stored (H3) |
| 14 | Docs and tests | `docs/`, `backend/tests/` | 113 tests pass |
| 15 | Traffic signal | – | Not needed (leftover code to remove, §4) |
| 16 | **Emergency SMS through Twilio** | – | **Missing** |

---

## 7. Fix plan (suggested order)

1. **Make the live system honest (1-2 days):** C1, C3, C4, H5, H7 – these are one-line to small fixes and give an immediate working demo. Add integration tests that call the real routes and `process_telemetry_packet`.
2. **Security basics:** C5 (token, localhost bind, whitelist commands, protect delete).
3. **Data layer:** H6, H8, H3, M2-M4 – one database module, one connection strategy, shared validation, store telemetry, add severity and zone id.
4. **Zones:** H4, M5 – single zone table, push `ZONES` to ESP32, critical-zone stop and Acknowledge.
5. **New feature:** Twilio emergency SMS (§5). Also remove the leftover traffic-signal code (§4).
6. **Face login:** C2, M10, M11 – real liveness, a single recogniser, enrol Arsal and Muniyappan or remove them from the roster, regenerate the accuracy report honestly, remove face images from the web root.
7. **Docs:** update README/FINAL_REPORT (claims about zone events and SMS), mark T7 and T8 accurately.

---

## 8. Testing notes

- During testing the API call `DELETE /api/logs` was run against the live database file `backend/data/accialert.db`, which cleared the event table (it held simulator output from the running server). Test zones and a test driver selection were also created and, for zones, removed again. The runtime database is excluded from Git, so nothing in the repository changed.
- A second copy of the server was started for testing and is still running on port 5173 next to the original one (see Low findings). Stop it from Task Manager (python.exe started at 10:10) or restart the server.

---

## 9. Unwanted features (not in the requirements, duplicated, or harmful) and what to do

"Unwanted" here means: not asked for in the requirements document, a second copy of something that already exists, or a feature that adds risk without adding value.

| # | Unwanted item | Why it is unwanted | Action |
|---|---|---|---|
| U1 | **Browser-side face engine** (`frontend/js/face_engine.js`, `enrolled_data.js`, `app.js` face logic, `localStorage` drivers/logs) | A second, separate face-login system next to the Python LBPH model. The two disagree (browser roster vs 3 trained drivers). `enrolled_data.js` puts 127 KB of base64 face photos in the public web root. | Remove. The page should only capture a frame and call a fixed `/api/verify`. |
| U2 | **Second zone system** (`database.py` zones + `/api/zones`, `find_active_danger_zone`) | The requirement has one zone engine. Two tables, two APIs and two detectors give conflicting alerts (H4). | Keep `/zm` + `zones.py` + `db.py`, delete the other, point the dashboard map at it. |
| U3 | **`db_logger.py` (`DatabaseLogger`)** | Never called. Creates a conflicting `events`/`drivers` schema in the same DB file and posts to a route that rejects POST (H6). | Delete; use the one events module. |
| U4 | **`ESP32SerialCommunicator` and `face_auth.py`** (standalone login loop) | Duplicate of `SerialManager.send_command` and `/api/verify`. Could open the same COM port twice and uses a different message format (M9). | Keep one sender that writes the JSON line format from the protocol. Keep `face_auth.py` only as a command-line test tool, not imported by the server. |
| U5 | **Browser simulator** (`static/js/client_simulator.js`) | Duplicates `simulator.py`. If the socket drops, the page shows fake data that looks real. | Remove, or show a clear "OFFLINE – demo data" banner. |
| U6 | **Fleet dashboard page** (`/dashboard`, `dashboard.html`) | The project is one miniature car, not a fleet; "fleet" is not in the requirements. | Remove, or merge anything useful into the main dashboard. |
| U7 | **Stub and fake-data endpoints**: `/api/liveness` (always passed), `/api/logs` constant `match_score` 85/42 and `liveness_result: PASSED` | They display results that were never measured. | Remove the stub; show real values or leave the field out. |
| U8 | **Dangerous open endpoints**: `DELETE /api/logs`, `POST /api/config` (threshold), `/api/serial/send` and `/api/command` with any text | Not in the API table of the requirements (section 7.4) and each one can unlock the car or destroy evidence. | Delete `DELETE /api/logs` and `/api/serial/send`; whitelist `/api/command` (STOP, RESET, SIGNAL, LIMIT, ACK, AUTH); protect `/api/config`. |
| U9 | **Fleet-style seed data**: licence numbers, "Karthik Raja", "Unknown Driver", real Chennai zones (Anna Salai, Guindy, Koyambedu, Marina) in the dashboard DB | Requirement says 3-5 enrolled drivers and miniature-track zones. Real-world coordinates cannot be reached by an indoor 1 m track. | Seed only enrolled drivers; use track-coordinate zones from `zones.json`. |
| U10 | **Repository clutter**: `__pycache__/*.pyc`, `dataset_raw/*.webp`, face photos in `dataset/` and `dataset_test/`, `.git` data inside the folder | Compiled files and personal photos do not belong in a hand-in; privacy risk (M12). | Delete caches, keep a few sample photos or none, add to `.gitignore`. |
| U11 | **Unused code and dependencies**: imports of `ESP32SerialCommunicator`, `zones_to_esp32_cmd`, `haversine_distance` in `app.py`; `state.last_telemetry_saved`; `requests` only used by the unused logger; `face_recognition`/`dlib` named in the requirements but not used | Dead weight and misleading documentation. | Remove, or actually use `zones_to_esp32_cmd` (needed for H4). Update the requirement text to say OpenCV LBPH. |
| U12 | **Over-claiming documents**: `FINAL_REPORT.md` and README say zone events work, face accuracy 100 % for 5 drivers, and the report file is auto-rewritten by pytest | These statements are false in the running app (C1, C3, M11). | Correct them after the fixes. Stop pytest overwriting the report. |
| U13 | **Leftover traffic-signal code** (RED SIGNAL alert, `SIGNAL` command, `red_signal` scenario, `signal` field) | Feature is not needed, and the RED alert is wrong (§4). | Remove as listed in §4. |

### What to fix to finish the cleanup (in order)

1. Delete U1, U3, U5, U7 and the dangerous endpoints in U8: this removes most defects (C5, H6, M10) without writing new code.
2. Merge zones (U2) and feed the engine correctly (C3), then send `ZONES` to the ESP32.
3. Keep one serial sender (U4) and fix the command format (M9).
4. Decide on the fleet page (U6) and clean seed data (U9).
5. Clean the repository (U10), tidy dependencies (U11), then rewrite the reports (U12).
6. After that, add the one remaining wanted feature: Twilio emergency SMS (section 5).

Items U6 and parts of U9 are judgement calls: I did not open every page in `dashboard.html`, so check that nothing you demo depends on them before deleting.

---

## 10. Second end-to-end test (2026-11-14, isolated copy)

**Setup.** To avoid touching live data, the project was copied to a scratch folder and run on port 5199 (one server process only). Tests: the 113 pytest tests again, 78 scripted API checks, a socket check, and a browser run of every dashboard tab. The test server has been stopped.

**Result: 113/113 unit tests pass. In the 78 end-to-end checks, 55 passed and 23 failed.** (The script first showed 41 passed / 37 failed; 14 of those were my test client's fault, because the `websocket-client` package was missing, so socket checks could not run. I re-checked those 14 through the REST API and the real browser, and all passed.)

### What works (verified)

| Area | Result |
|---|---|
| All 20 pages and GET endpoints | HTTP 200 |
| Live data over Socket.IO (WebSocket) | Works in the browser; cards, charts and map update every second |
| Demo scenarios: accident, alcohol, obstacle, overspeed, unauthorised, danger zone | Each raises the red banner and writes an event row |
| Emergency STOP / RESET / Toggle Auth buttons | Work (speed to 0 and EMERGENCY, then back to RUNNING; LOCKED and unlocked) |
| Commands are recorded in the command log | Yes |
| Zone add / delete in the dashboard (valid data) | Works; the form refuses obviously invalid input |
| Zone Manager page (`/zm`) | Loads with map and 6 zones; add, edit, delete work; invalid latitude returns 400 |
| Map (Leaflet), charts (Chart.js) | Render |
| Event list pagination, type filter on the API, CSV export | Work |
| Mode switch Simulation / ESP32 Serial | Works; connecting to a missing COM port fails cleanly |
| Alerts no longer spam the database | About 2-5 rows per type per minute (throttle works) |

### What failed (23 genuine failures)

| # | Failure | Related finding |
|---|---|---|
| 1 | Vehicle starts as authorised (driver "Muniyappan", auth 1) | C4 |
| 2-8 | Face login: `/api/verify` returns 500 for 3 enrolled faces, 2 unknown faces, garbage input and a blank image | C1 |
| 9 | No ZONE_ENTER / ZONE_EXIT in the live event log; no `zone_event` socket messages | C3 |
| 10 | Events have no `severity` column | M4 |
| 11 | Unknown command `FORMAT_DISK` is accepted and logged | C5, U8 |
| 12-14 | Zone API accepts latitude 999, radius -5, speed limit -3 and severity "BOGUS" (all stored); a duplicate zone code and latitude "abc" return 500 | M1, M2 |
| 15 | Zone added on the dashboard does not appear in the Zone Manager (two separate databases) | H4 |
| 16-18 | Intermittent 500 errors from `database is locked` on zone add, zone delete and driver add | H8 |
| 19 | `/api/events?limit=abc` returns 500 | M1 |
| 20-22 | Unauthenticated `drivers/select` succeeded, threshold 999 accepted, `DELETE /api/logs` succeeded | C5 |
| 23 | No telemetry stored (0 rows) | H3 |

### New defects found in this round

**N1. The dashboard's Event history filters do nothing.** `frontend/static/js/dashboard.js:421` `loadEvents()` always calls `/api/events?limit=50`; the Type, From date, To date and Search fields are never sent. Filter, Reset and the type drop-down all show the same 50 rows (tested: filtering "ACCIDENT" still returned 50 mixed rows). The backend `get_events()` already supports type, dates and search; only the route and the front end are not connected.

**N2. Event type spelling differs between back end and front end.** The server emits `UNAUTHORIZED DRIVER` (Z) while the dashboard, banner titles, badge colours and the filter list use `UNAUTHORISED DRIVER` (S); `/api/verify` logs a third form, `UNAUTHORISED`. Even after N1 is fixed, filters and badges would miss these events. Use one constant list.

**N3. The dashboard silently shows fake data when the connection fails.** If the first Socket.IO handshake fails, `initSocket()` starts the browser simulator and calls `updateConnectionBadge(true, "Standalone mode")`, so the status badge still looks healthy while the values come from `client_simulator.js`. This happened on the first page load in the test (console: "No backend detected. Starting in Standalone Frontend Mode"), then the page switched to live data a few seconds later. The same happens if the server is stopped. Show a clear red "OFFLINE - simulated data" state.

**N4. Serial mode with no hardware gives no warning.** After switching to ESP32 Serial with nothing connected, the telemetry kept ageing (3 s and rising) and nothing on the page said "no data". Add a stale-data indicator.

**N5. Demo scenarios leak into each other.** The accident scenario never auto-clears (the timer is skipped for it), so tilt 58.5 stays and later scenarios (for example "Unauthorized") show the ACCIDENT banner. The danger-zone scenario teleports to the first zone in code order, which was a leftover test zone, and "danger zone" after "unauthorised" showed SECURITY BREACH instead because the lock remained. Reset state before each scenario.

**N6. Overspeed message contradicts itself.** The banner read "Speed is 79.6 km/h, exceeding the safety threshold (80.0 km/h)". The alert fired because the simulator set the event text `OVERSPEED`, not because speed passed the limit.

**N7. Slow database access.** Each write request took about 0.25-0.33 s because every call opens a new connection. This is related to the lock errors (H8).

### Clean-up of test side effects
All test data was created in the scratch copy only. Your real project, its database and the running server on port 5173 were not touched in this round. The two extra files I made are in my scratchpad, not in the project.

---

## 11. Danger zone management: detailed check (2026-11-14)

Checked: the dashboard "Danger Zone Management" tab, the Zone Manager (`/zm`), both zone databases, the zone engine, the map and the alert path. Tested on a scratch copy; nothing in the project database was changed.

**Root cause of the "database is locked" errors (confirmed, replaces the guess in H8).** Every function in `database.py` opens a connection, runs a statement and closes the connection on the last line, with no `try/finally`. If the statement fails (for example a duplicate zone code gives `IntegrityError`), the connection is never closed and its open transaction keeps the write lock. While the error object is still alive (Flask holds it for the traceback), **every other write to the database fails after waiting 10 seconds**, including telemetry events such as an accident. Reproduced: after one duplicate insert the next write failed after 11.1 s; after the error was released it worked at once. Fix: close connections in `finally` (or use `with closing(...)` and rollback) in all functions.

**Defects found in zone management**

| # | Defect | Evidence |
|---|---|---|
| Z1 | Editing a zone on the dashboard tab does not save. The form sends `POST /api/zones` (add), the server rejects the duplicate code with 500, and the page then shows "Danger zone saved (Local Mode)" and keeps the change only in the browser. Reloading shows the old values. `database.update_zone()` exists but no `PUT` route uses it. | Edited speed limit 30 to 11: server stayed 30, toast said saved |
| Z2 | Any server error is reported as a successful local save, so the operator thinks the zone exists while the server, the alert logic and the ESP32 never see it. Same for delete. | `saveZone`/`deleteZone` fall back to `localStorage` on every failure |
| Z3 | The failed duplicate insert in Z1 leaks a connection and blocks all database writes for 10 s or more (root cause above). | locktest3 |
| Z4 | The suggested new code is "Z" plus the number of zones plus 1, so after a delete it repeats an existing code (4 zones, delete one, the next suggestion is Z04 or Z05 depending on the gap) and fails as in Z1. | code review, `zones.js` `openModal` |
| Z5 | `POST /api/zones` has no validation: latitude 999, radius -5, speed limit -3 and severity "BOGUS" are all stored; text values and duplicates give 500. `zones.validate_zone` already exists. | tests |
| Z6 | Two separate zone systems: the dashboard tab and map use `database.py` (codes, upper-case severity, real Chennai coordinates, radius 150 m); the Zone Manager uses `db.py` and `zones.json` (ids, lower-case severity, track placeholders, radius 20-30 m). Changes in one never reach the other. The engine, the Zone Manager and the zone events use only the second; the alerts, the map circles, the simulator and the speed-limit commands use only the first. | `app.py` lines 233 and 331 |
| Z7 | Zone events never happen (engine called with wrong arguments, error hidden) and nothing in the front end listens for `zone_event`. The header counter "Zones entered today" (`zm-count`) is never updated by any script, and "time spent inside" does not exist. | code and grep |
| Z8 | Zone Manager changes are never sent to the ESP32: `mount_zone_manager` is called without `on_zones_changed`, so the `ZONES` command is never built or sent. | `app.py` |
| Z9 | The dashboard zone check has no hysteresis or pre-warning. A car at the edge of a zone flips between inside and outside and sends `LIMIT 30` / `LIMIT 0` repeatedly. The engine has hysteresis (5 m) but is not used for this. | `app.py` section 5 |
| Z10 | Critical zones (for example the no-entry road, limit 0) should stop the car and show a full-width alert until Acknowledge. The `critical` flag is produced but never used. A limit of 0 also clashes with `LIMIT 0` meaning "no limit". | `zones.py`, `app.py` |
| Z11 | Overlapping zones are handled differently: the dashboard picks the closest centre, the engine picks the most severe zone and the lowest limit. | `find_active_danger_zone` vs `ZoneEngine` |
| Z12 | The dashboard tab has no enable/disable switch and no zone type, although the requirement and the Zone Manager have them. Defaults (radius 150 m, limit 30 km/h) suit real roads, not the miniature track (20-30 m, 5-10 km/h). | `zones.js`, `zones.json` |
| Z13 | Zone names and details are inserted into the page as raw HTML (table and map popup). A name such as `<img onerror=...>` would run script if the browser renders it. Not confirmed by a run: my test zone was never saved because of the database lock (Z3), so nothing was rendered. Re-test after the lock is fixed. | code review: `renderTable`, `renderDangerZones` |
| Z14 | `zones.json` coordinates are placeholders around Chennai; the real track map from Nivash is still missing. | `zones.json` note |

**What works:** the Zone Manager page itself (add, edit through `PUT`, delete, enable/disable, validation with 400 errors, map click to place, CSV of zone events); the engine logic (enter, exit with 5 m margin, 20 m pre-warning, overspeed once per episode, no-GPS handling), when called correctly; the dashboard add and delete with valid new data; map circles coloured by severity; "Locate" and "Enforce limit" buttons.

### Status update: fixed in this round

| Item | Result |
|---|---|
| Z1 Zone edit did not save | Fixed. New `PUT /api/zones/<id>`; the form edits through it. Tested in the browser: an edit persists after reload. |
| Z2 Failures shown as "saved" | Fixed. When the server refuses, the form stays open and shows the server's message. The browser-only save is used only when the server cannot be reached, and says so. |
| Z3 Connection leak that froze writes | Fixed for all zone functions (connections always close). Regression tests check that a duplicate code no longer blocks the next write. Other `database.py` functions (events, drivers, commands) still have the same pattern and should get the same change. |
| Z4 Duplicate suggested code | Fixed. The server suggests the first unused code. |
| Z5 No validation | Fixed for add and edit (400 with a readable message). |
| WAL mode switched on every connection | Now only set when not already set, with retry. |
| Default zones | Added: Outdoor set (3 Chennai junctions) and Track set (5 zones) with a switch and a "Load default zones" button. |

Still open from section 11: Z6 (the Zone Manager `/zm` is a separate system), Z7 to Z14.
