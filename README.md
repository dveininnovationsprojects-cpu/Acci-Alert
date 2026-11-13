# AcciAlert - Unified Smart Driver Safety, Accident Prevention & Driver Authorisation System

**AcciAlert** is an intelligent, multi-subsystem vehicular safety platform that prevents accidents, enforces driver authentication, monitors geofenced danger zones, and interfaces directly with an ESP32 vehicle controller over high-speed UART serial.

---

## 1. Subsystem Architecture & Responsibilities

| Subsystem | Responsibilities | Lead / Contributor | Tech Stack |
| :--- | :--- | :--- | :--- |
| **Vision AI & Driver Authorisation** | In-browser & edge facial recognition, CLAHE normalization, 1024-D LBP feature extraction, anti-spoofing liveness gating, driver enrollment | **Arsal** | OpenCV LBPH, Haar Cascades, NumPy, WebCam Canvas |
| **Danger-Zone Engine & Geofencing** | Geo-spatial Haversine boundary detection, pre-warning, zone entry/exit events, speed-limit governing | **Prasanth** | Python, SQLite, Geodesic math, Leaflet.js |
| **Zone Manager & Telemetry API** | Zone CRUD operations, event logging, SQLite WAL durability, 24h rolling purges, database backup automation | **Prasanth** | Flask, SQLite3, Backup scripts |
| **Fleet Telemetry Dashboard** | Real-time vehicle HUD, sensor charts, tilt/impact monitoring, Leaflet map tracking, simulated trip generator | **Muniyappan** | Socket.IO, Chart.js, HTML5/CSS3 |
| **Microcontroller & Firmware** | ESP32 UART bridge at 115200 baud, relay actuation, sensor telemetry stream, and hardware command execution | **Selvamani** | ESP32 C++, UART JSON protocol |

---

## 2. Unified Folder Layout

```
Acci-Alert/
├── backend/
│   ├── app.py                # Master unified server (Flask + SocketIO on Port 5173)
│   ├── config.py             # Central configuration (thresholds, ports, paths)
│   ├── database.py           # Dashboard & telemetry database operations
│   ├── db.py                 # Danger zone SQLite engine with thread-safe locking
│   ├── zones.py              # Geofencing calculation engine & boundary hysteresis
│   ├── zone_api.py           # Zone Manager REST Blueprint (/zm)
│   ├── zones.json            # Seed danger zones (Anna Salai, Guindy, Koyambedu, etc.)
│   ├── geo_utils.py          # Haversine distance & geofence coordinate matching
│   ├── serial_manager.py     # Hardware ESP32 USB serial manager & COM autodetection
│   ├── simulator.py          # Physics-based vehicle telemetry mock generator
│   ├── backup.py             # SQLite automated database backup utility
│   ├── face_detector.py      # CLAHE-enhanced Haar Cascade face & eye detector
│   ├── face_recognizer.py    # OpenCV LBPH recognizer & threshold gating
│   ├── liveness.py           # Eye-blink EAR & micro-motion anti-spoofing engine
│   ├── db_logger.py          # SQLite database logger for security audit trails
│   ├── serial_comm.py        # Serial communication bridge with mock fallback
│   ├── enrol_driver.py       # Interactive driver facial registration utility
│   ├── train_model.py        # LBPH model training pipeline
│   ├── test_recognition.py   # Acceptance Test T8 verification runner
│   ├── test_app.py           # Backend server & REST API integration tests
│   └── tests/                # Danger zone & database test suite
│       ├── test_zones.py
│       ├── test_zone_api.py
│       ├── test_hardening.py
│       └── test_backup.py
├── frontend/
│   ├── index.html            # Master Unified Cockpit (Driver Authorisation + Fleet Dashboard)
│   ├── dashboard.html        # Standalone Fleet Telemetry & Sensor Dashboard
│   ├── zones.html            # Danger Zone Manager UI
│   ├── zones.css / zones.js  # Zone manager styling and Leaflet mapping
│   ├── css/style.css         # Tactical dark automotive cockpit styling
│   ├── js/                   # Vision AI client-side engines
│   │   ├── app.js            # Master UI coordinator & tab switching
│   │   ├── face_engine.js    # Client-side face detection & 1024-D LBP extractor
│   │   ├── enrolled_data.js  # Enrolled driver biometric templates
│   │   └── serial_controller.js # Web Serial API & ESP32 bridge
│   └── static/               # Fleet dashboard assets (charts, map, alerts)
├── models/                   # Pre-trained models & Haar XML cascades
│   ├── face_model.yml        # 100% accuracy LBPH model file
│   ├── labels.json           # Driver label mappings
│   ├── haarcascade_frontalface_default.xml
│   └── haarcascade_eye.xml
├── dataset/                  # Multi-condition driver training datasets
├── dataset_test/             # Independent test subjects (Authorised + 5 Unknowns)
├── docs/                     # Test reports, architecture specifications & guides
│   ├── FINAL_REPORT.md
│   ├── TEST_PLAN.md
│   └── T8_test_report.md
├── firmware/
│   └── protocol.txt          # ESP32 to Laptop UART JSON protocol specification
├── tests/                    # Vision AI & acceptance test suite
│   ├── test_db_logger.py
│   ├── test_face_detector.py
│   ├── test_face_recognizer.py
│   ├── test_liveness.py
│   ├── test_serial_comm.py
│   └── test_t8_acceptance.py
└── pytest.ini                # Pytest configuration
```

---

## 3. Quickstart & Execution

### Prerequisites
- Python 3.10 or higher
- Modern Web Browser (Chrome / Edge recommended for WebCam and Web Serial support)

### Step 1: Install Dependencies
```bash
pip install -r requirements.txt
pip install flask flask-socketio flask-cors python-socketio
```

### Step 2: Start Unified Server
The unified server runs strictly on **Port 5173**:
```bash
python -m backend.app
```

Open your browser to:
- **Driver Authorisation Cockpit:** [http://localhost:5173/](http://localhost:5173/)
- **Fleet Telemetry & Danger Zones:** [http://localhost:5173/dashboard](http://localhost:5173/dashboard)
- **Zone Manager:** [http://localhost:5173/zones.html](http://localhost:5173/zones.html) (or `/zm/`)
- **API Health Check:** [http://localhost:5173/api/health](http://localhost:5173/api/health)

---

## 4. Hardware & Software Team Contracts

### 4.1 ESP32 Serial Protocol (Selvamani)
- **Baud Rate:** `115200` baud
- **Format:** Single-line JSON terminating with `\n`
- **Telemetry from ESP32 to Laptop (every 200ms):**
  ```json
  {"t": 123456, "spd": 45, "alc": 10, "dist": 150, "ax": 0.1, "ay": 0.2, "az": 9.8, "tilt": 0, "lat": 13.0827, "lon": 80.2707, "fix": 1, "state": "RUNNING", "auth": 1, "signal": "GREEN", "zone": 0, "evt": "NONE"}
  ```
- **Commands from Laptop to ESP32:**
  - `AUTH`: `{"cmd":"AUTH","ok":1,"driver":"Arsal"}`
  - `STOP`: Emergency motor cut-off
  - `RESET`: System resume
  - `LIMIT`: Speed governor threshold in km/h
  - `SIGNAL`: Traffic light state (`RED` / `GREEN`)

### 4.2 Database & REST Endpoints
- **Database:** SQLite WAL mode with 24-hour telemetry purges and automated snapshots.
- **REST APIs:**
  - `POST /api/verify`: AI facial verification & anti-spoofing gating
  - `POST /api/liveness`: Vitality detection
  - `GET, POST, DELETE /api/logs`: Audit trail with CSV export
  - `GET, POST /api/zones`: Danger-zone management
  - `GET, POST /api/drivers`: Driver roster
  - `POST /api/command`: Vehicle actuation
  - `GET /zm/api/zones`: Zone Manager API

---

## 5. Verification & Testing

Run all automated test suites across both subsystems:

```bash
# Run Vision AI & Acceptance Test T8 suite (20 tests)
python -m pytest tests/ -v

# Run Danger Zone & Hardening test suite (84 tests)
python -m pytest backend/tests/ -v

# Run Backend Server & REST integration test suite
python -m pytest backend/test_app.py -v
```
