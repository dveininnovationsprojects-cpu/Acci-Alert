# AcciAlert - Final Report: Database, Danger-Zone Engine and QA

Author: Prasanth (Software team)
Project: AcciAlert, Smart Driver Safety and Accident Prevention System
Status of this report: DRAFT. Software results are final. Hardware test results (T1 to T13) are marked PENDING and must be filled in once the tests are run.

## 1. Project overview

AcciAlert is a miniature smart-car prototype. A laptop running Python and OpenCV checks that the person in the driver's seat is an authorised driver. An ESP32 reads the sensors (alcohol, speed, obstacle distance, impact and tilt, GPS) and decides in real time whether the car is safe to run. A Flask web dashboard shows live data and keeps an event log. If an accident happens, the system sends an SMS with the location. The newest feature is danger-zone detection: the dashboard warns, and the car limits its speed, when the vehicle enters a marked zone such as a school crossing or accident black spot.

The software team has four members, each owning one layer:

| Member | Layer |
|---|---|
| Selvamani | ESP32 firmware and safety decision logic |
| Arsal | Face recognition and driver authorisation |
| Muniyappan | Flask backend and live dashboard |
| Prasanth | Database, danger-zone engine, testing and QA |

## 2. My responsibilities

1. Design the SQLite database and data-access layer.
2. Build the danger-zone engine.
3. Prepare zone data for the miniature track with Nivash.
4. Own the test plan (T1 to T13), log defects, run regression before every demo.
5. Verify Selvamani's ESP32 backup zone logic.
6. Maintain the project documentation and write this report.
7. Act as backup owner for Selvamani's firmware.

## 3. What was built

All code is in the `code` folder.

| File | Purpose |
|---|---|
| `backend/db.py` | SQLite data-access layer |
| `backend/zones.py` | Danger-zone engine |
| `backend/zone_api.py` | REST API for zones and events, with a standalone dev server |
| `backend/zones.json` | Starter data, 6 zones |
| `backend/backup.py` | Database backup tool |
| `backend/tests/` | Automated tests (pytest) |
| `frontend/zones.html, zones.css, zones.js` | Zone manager web page |
| `docs/TEST_PLAN.md` | Test checklist T1 to T13, defect log, regression list |
| `docs/WEEKLY_TEST_REPORT_TEMPLATE.md` | Weekly report template |
| `docs/AUTOMATED_TEST_REPORT.pdf` | Result of the automated test run |
| `README.md` | How to install and run |

Technologies used: Python, SQLite3, Flask, pytest, Leaflet (map), plain HTML, CSS and JavaScript.

### 3.1 Database

File `accialert.db` with four tables:

| Table | Content |
|---|---|
| `drivers` | Driver name and authorised flag |
| `events` | id, timestamp, type, severity, driver, speed, lat, lon, zone id, details |
| `telemetry` | One sample per second: speed, alcohol, distance, tilt, position, state, zone |
| `danger_zones` | id, name, type, lat, lon, radius_m, speed_limit, severity, active |

Design decisions:
- An event is committed to disk before the function returns, so that an accident event is stored before the dashboard is updated (requirement 9.1). The database uses write-ahead logging with full synchronous mode.
- Telemetry older than 24 hours is deleted by a background job that runs at startup and then every hour.
- Zone severity is restricted to low, medium, high or critical, and radius must be positive.
- The backup tool uses SQLite's online backup, which is safe while the server is running, and checks each backup for corruption.

### 3.2 Danger-zone engine

The engine takes each GPS fix (latitude, longitude, speed) and returns the events it caused.

| Rule | Behaviour |
|---|---|
| Distance | Haversine distance from the vehicle to each active zone |
| ENTER | Distance is within the zone radius and the vehicle was outside |
| EXIT | Distance is more than radius plus 5 m and the vehicle was inside |
| Pre-warning | Once per approach when within radius plus 20 m |
| Overspeed | `OVERSPEED_IN_ZONE` (high severity) once per episode when speed exceeds the zone limit inside a zone; it re-arms when speed falls back to the limit |
| Overlapping zones | The most severe zone is the current zone, and the lowest limit applies |
| Disabled or deleted zones | The vehicle is not left stuck inside; a disabled zone produces an EXIT |

The 5 m hysteresis prevents flicker at the zone boundary, so each crossing produces exactly one ENTER and one EXIT. The engine also builds the `{"cmd":"ZONES", ...}` line used to upload zones to the ESP32 for its backup check.

### 3.3 Zone API and zone manager page

API endpoints:

| Method | Path | Purpose |
|---|---|---|
| GET, POST | `/api/zones` | List and add zones |
| PUT, DELETE | `/api/zones/<id>` | Edit and delete a zone |
| GET | `/api/events` | Events filtered by type and date |
| GET | `/api/events.csv` | Same, as CSV |

The zone manager page shows the zones as coloured circles on a Leaflet map, lets the operator click the map to add a zone, edit, enable, disable or delete it without restarting the server, and shows the event table with filters and CSV export. It covers the zone parts of dashboard requirements D4, D6 and D8. The rest of the dashboard belongs to Muniyappan.

## 4. Test results

### 4.1 Automated tests (software)

Run: 32 tests, 32 passed, 0 failed (about 1.2 seconds). Details are in `docs/AUTOMATED_TEST_REPORT.pdf`.

| Area | Tests | Main checks |
|---|---|---|
| Zone engine and database | 24 | Enter and exit once, hysteresis, boundary jitter, 10 crossings giving exactly 10 ENTER and 10 EXIT, pre-warning, overspeed, critical zones, disabled zones, event durability, telemetry purge |
| Zone API | 4 | Add, edit and delete without restart, input validation, event filter and CSV |
| Backup and purge | 4 | Backup contains data, pruning of old backups, missing database, purge job |

These tests use temporary databases and simulated GPS points. They show that the logic is correct. They do not replace the physical tests.

### 4.2 Physical tests T1 to T13

Results must be entered from the weekly test reports.

| ID | Test | Tester | Result |
|---|---|---|---|
| T1 | Motor control | Navin / Prasanth | PENDING |
| T2 | Alcohol detection | Yasik | PENDING |
| T3 | Obstacle detection | Yasik | PENDING |
| T4 | Speed vs stopwatch | Yasik / Prasanth | PENDING |
| T5 | Accident detection | Nivash | PENDING |
| T6 | GPS fix and accuracy | Sidharraj | PENDING |
| T7 | SMS delivery | Sidharraj | PENDING |
| T8 | Face recognition | Arsal | PENDING |
| T9 | Serial link stability | Selvamani / Muniyappan | PENDING |
| T10 | Dashboard latency | Muniyappan | PENDING |
| T11 | Danger-zone ENTER/EXIT, 10 crossings per zone | Prasanth | PENDING |
| T12 | Laptop disconnected | Selvamani | PENDING |
| T13 | Full demo x 3 | All | PENDING |

### 4.3 Defects

No physical test has been run yet, so no defects are recorded. Defects will be listed in the weekly reports.

## 5. Zone acceptance criteria

| Criterion | Status |
|---|---|
| Dashboard alert within 1 s of entry and cleared within 2 s of exit | PENDING (needs live data, see T10) |
| ENTER and EXIT logged exactly once per crossing | Verified in software; physical check PENDING (T11) |
| ESP32 applies the zone limit in 3 of 3 trials | PENDING (needs firmware) |
| 5 or more zones added, edited and removed without restarting the server | Verified in software (API tests); UI check PENDING |
| Works with laptop disconnected (nice to have) | PENDING |

## 6. Hand-offs

| To | What | Status |
|---|---|---|
| Muniyappan, Selvamani | Zone API, `zones.json` and zone format (due Week 4) | Built, hand-over to be confirmed |
| Muniyappan | Plug the API into his Flask app, and call the purge scheduler at startup | Instructions in `README.md` |
| From Nivash | Track zone map with coordinates | Not received; `zones.json` uses placeholders |
| From Selvamani | ESP32 firmware for the backup zone check | Not received |

## 7. Limitations and open items

1. The zone coordinates are placeholders until the track map is received.
2. The 5 m hysteresis is large for a 1 m miniature track. A smaller value can be passed to the engine in simulation mode.
3. The engine and database are not yet connected to the live serial feed, because that is part of the Flask server owned by Muniyappan. Until then the events and telemetry tables stay empty.
4. The events table on the zone page refreshes by polling every 3 seconds. Live push depends on Muniyappan's WebSocket layer.
5. The drivers table stores only name and authorised flag. Arsal's face encodings are not in the database.
6. The ESP32 backup zone logic is not verified yet.
7. The zone API has no authentication. It is meant for a local demo only.

## 8. Risks

| Risk | Mitigation |
|---|---|
| No GPS fix indoors, so no zone detection | Simulation or track mode with virtual coordinates (requirements section 8.5) |
| A team member is unavailable | Backup owner defined for every module |
| Demo failure on the day | Spare ESP32, spare modules, pre-recorded demo video, database backup |

## 9. Conclusion

The database, danger-zone engine, zone API, zone manager page, backup tool and test documents are built, and the 32 automated tests pass. The project is not complete until the physical tests T1 to T13 are run and recorded, the track zone map and ESP32 firmware are received and verified, and the software is connected to live data. This report should be updated with those results before the final submission.

## Appendix: how to run

From the `code` folder:

```bash
pip install flask pytest
python -m pytest backend/tests -q
python backend/zone_api.py
python backend/backup.py
```

Then open http://127.0.0.1:5000/ for the zone manager.
