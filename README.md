# AcciAlert - AI Face Recognition & Driver Authorisation Module

**Subsystem:** Driver Authentication & Safety Gating  
**Module Lead:** Arsal  
**Stack:** Python 3.10+, OpenCV (LBPH & Haar Cascades), NumPy, PySerial, SQLite3, Pytest  
**Status:** **Completed & Verified (Acceptance Test T8 Passed - 100% Tests Passing)**

---

## 1. System Overview & Module Role

In the **AcciAlert** vehicle safety ecosystem, this module serves as the primary gateway:
- When someone enters the vehicle, the driver-facing webcam scans their face.
- If the AI recognizes an **authorised driver** (and passes anti-spoofing liveness verification), it unlocks the vehicle and transmits an authentication success command (`{"cmd":"AUTH","ok":1,"driver":"<Name>"}`) over USB serial to Selvamani's ESP32 microcontroller at 115200 baud.
- If the person is **unregistered / unknown**, if **multiple faces** are detected, or if **no face** is present, the system keeps the vehicle **strictly locked** (`{"cmd":"AUTH","ok":0,"driver":"Unknown"}`), preventing engine / motor start.
- Every event is logged to the local SQLite database (`accialert.db`) and forwarded to Muniyappan's dashboard via the REST API (`/api/events`).

```
 +------------------+        +--------------------------+        +-------------------+
 |  USB Driver Cam  | -----> |  AI Face Recognition     | -----> |  ESP32 Controller |
 |  VideoCapture(0) |        |  - Haar Cascade (Detect) |        |  - Motors Enable  |
 +------------------+        |  - CLAHE (Illumination)  |        |  - Telemetry Stream|
                             |  - LBPH Texture Match    |        +-------------------+
                             |  - Liveness Verification |                  ^
                             +--------------------------+                  |
                                       |                                   |
                                       v                                   |
                             +-------------------+                         |
                             | SQLite DB & Flask | <-----------------------+
                             | Live Dashboard UI |
                             +-------------------+
```

---

## 2. Directory Structure

```
AcciAlert/
├── ai/
│   ├── __init__.py
│   ├── config.py              # Centralized configuration & thresholds
│   ├── dataset_generator.py   # Multi-condition dataset generation & augmentation
│   ├── db_logger.py           # SQLite accialert.db persistence & REST API forwarder
│   ├── enrol_driver.py        # Interactive CLI/Webcam driver enrolment utility
│   ├── face_auth.py           # Core live driver authorization engine & HUD
│   ├── face_detector.py       # CLAHE-enhanced Haar face & eye detection
│   ├── face_recognizer.py     # OpenCV LBPH recognizer, scoring & threshold gating
│   ├── liveness.py            # Anti-spoofing eye-blink & micro-motion detector
│   ├── serial_comm.py         # 115200 baud JSON-line ESP32 serial bridge (with Mock mode)
│   ├── test_recognition.py    # Acceptance Test T8 benchmark & report generator
│   └── train_model.py         # Model training pipeline
├── camera_test.py             # Step 2 camera connectivity diagnostic tool
├── dataset/                   # Training face images (Arun, Kumar, Ravi, ...)
├── dataset_test/              # Independent test sets (Authorised + 5 Unknown subjects)
├── docs/                      # Test reports & team specifications
│   ├── T8_test_report.md      # Acceptance Test T8 verification report
│   ├── accuracy_report.json   # Machine-readable test metrics
│   ├── AcciAlert – Software Team Guide.pdf
│   └── AcciAlert_Requirements_Document (2).pdf
├── models/
│   ├── face_model.yml         # Pre-trained LBPH face model
│   ├── labels.json            # Enrolled driver label mapping
│   ├── haarcascade_frontalface_default.xml
│   └── haarcascade_eye.xml
├── tests/                     # Full unit & integration test suite
│   ├── test_db_logger.py
│   ├── test_face_detector.py
│   ├── test_face_recognizer.py
│   ├── test_liveness.py
│   ├── test_serial_comm.py
│   └── test_t8_acceptance.py
├── accialert.db               # SQLite database (tables: drivers, events)
├── pytest.ini                 # Pytest configuration
└── requirements.txt           # Project dependencies
```

---

## 3. Getting Started

### Step 1 — Setup Environment
```bash
# Verify Python version (3.10 or higher)
python --version

# Optional: Create and activate virtual environment
python -m venv venv
venv\Scripts\activate

# Install required packages
pip install -r requirements.txt
```

### Step 2 — Test Driver Webcam
Verify camera connectivity and lighting:
```bash
python camera_test.py
```
*Press `q` or `ESC` to exit preview.*

### Step 3 — Enrol New Authorised Drivers
To register a new driver interactively through the webcam:
```bash
python -m ai.enrol_driver --name "Arun" --target 25
```
*Controls during capture:*
- Press `c` for single capture.
- Press `a` to toggle auto-capture mode.
- Model automatically retrains once capture completes.

### Step 4 — Train / Retrain AI Model
To scan the `dataset/` directory and regenerate `models/face_model.yml`:
```bash
python -m ai.train_model
```

### Step 5 — Run Live Driver Authorisation (Desktop OpenCV)
Start the complete authentication system:
```bash
# Standard live execution (with HUD, liveness check, and auto-serial detection)
python -m ai.face_auth

# Optional flags:
python -m ai.face_auth --camera 0             # Preferred webcam index
python -m ai.face_auth --serial COM3          # Explicit ESP32 serial port
python -m ai.face_auth --threshold 40.5       # Custom LBPH distance threshold
python -m ai.face_auth --no-liveness          # Disable anti-spoofing check
python -m ai.face_auth --image <path_to_jpg>  # Run headless test on a single image
```

*Interactive HUD Keys:*
- `q` / `ESC`: Exit system cleanly.
- `r`: Reset authentication (locks vehicle immediately).
- `l`: Toggle anti-spoofing liveness check.

### Step 6 — Run Web Application (Pure Client-Side Frontend)
The system also includes a complete, standalone, client-side web application with zero backend dependencies:
- **Direct file launch:** Simply double-click [index.html](file:///c:/Users/moham/OneDrive/Apps/pckg/Acci-Alert/Acci-Alert/index.html) in your browser.
- **Local web server (optional):**
  ```bash
  python -m http.server 8000
  ```
  Open `http://localhost:8000` in Google Chrome or Microsoft Edge.

*Web App Features:*
- **Click "Start Face Detector":** Prompts for webcam access and renders real-time HUD with facial targeting brackets, scanning lines, similarity percentage, and vehicle ignition status.
- **Anti-Spoofing Liveness:** Evaluates eye-blinking cycles and micro-motion variance to defeat photo spoofing attacks.
- **Interactive Enrolment Wizard:** Enrol new authorized drivers from the browser webcam; automatically extracts 1024-D LBP descriptors and persists into `localStorage`.
- **Security Audit Logs:** Live table recording `AUTH_OK`, `UNAUTHORISED`, and `MULTIPLE_FACES` events, with CSV and JSON export options.
- **Web Serial API & Virtual Console:** Connects directly to Selvamani's physical ESP32 over USB at 115200 baud, or emulates serial transmission in real-time.
- **Demo Simulator:** Built-in presets to test Arun, Kumar, Ravi, and Unknown intruders instantly without camera hardware.

---

## 4. Hardware & Software Team Contracts

### 4.1 ESP32 Serial Protocol (Selvamani)
- **Baud Rate:** `115200` baud
- **Framing:** One JSON message per line (`\n` terminated)
- **Authorised Driver (Vehicle Unlocks):**
  ```json
  {"cmd":"AUTH","ok":1,"driver":"Arun"}
  ```
- **Unknown / Intruding Driver (Vehicle Remains Locked):**
  ```json
  {"cmd":"AUTH","ok":0,"driver":"Unknown"}
  ```
*Note: If no physical ESP32 is attached to USB, `ai/serial_comm.py` automatically runs in Mock Mode, logging transmissions without crashing.*

### 4.2 Database & Dashboard REST API (Prasanth & Muniyappan)
- **Database:** `accialert.db` (SQLite3)
- **Events Schema:**
  ```sql
  CREATE TABLE events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      timestamp TEXT NOT NULL,
      type TEXT NOT NULL,          -- 'AUTH_OK' or 'UNAUTHORISED'
      severity TEXT NOT NULL,      -- 'INFO' or 'HIGH'
      driver TEXT NOT NULL,        -- 'Arun', 'Kumar', or 'Unknown'
      speed REAL DEFAULT 0.0,
      lat REAL DEFAULT 0.0,
      lon REAL DEFAULT 0.0,
      zone TEXT DEFAULT '',
      details TEXT
  );
  ```
- **REST Notification:**
  POST `http://localhost:5000/api/events` with JSON payload on state transition.

---

## 5. Acceptance Test T8 Verification

The module was tested against the official project requirements (Software Team Guide & Section 13 of Requirements Document):

| Test Item | Target Requirement | Measured Result | Verdict |
| :--- | :--- | :--- | :--- |
| **Authorised Recognition Accuracy** | $\ge 90\%$ | **93.3%** (28/30 accepted) | **PASSED** |
| **Unknown Driver Rejection** | 10 of 10 rejected (0 False Acceptances) | **10/10 Rejected** (0 False Accepts) | **PASSED** |
| **Recognition Latency** | $< 2.0$ seconds | **0.029 seconds** (28.9 ms) | **PASSED** |

To run the automated acceptance test suite at any time:
```bash
# Run standalone acceptance evaluation report generator
python -m ai.test_recognition

# Run all 20 automated unit and acceptance tests via pytest
pytest -v
```
Detailed test report generated at: [docs/T8_test_report.md](docs/T8_test_report.md).
