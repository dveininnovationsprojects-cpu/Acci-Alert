# AcciAlert – Smart Driver Safety and Accident Prevention System
### Flask Backend & Live Real-Time Dashboard Module

**Developer:** Muniyappan  
**Role:** Flask Backend Architect & Dashboard Frontend Engineer  
**System Version:** 2.0 Pro  
**Operating Environment:** Windows 10/11 / VS Code / Python 3.8+

---

## 1. Project Overview

**AcciAlert** is an intelligent, connected vehicular safety monitoring system designed to proactively prevent collisions, detect driver impairment (alcohol detection), enforce dynamic danger-zone speed limits, detect physical rollovers/accidents, and respond to V2X traffic signals in real time.

This module represents the complete **Flask Backend and Live Mission Control Dashboard**, featuring high-frequency telemetry broadcasting via WebSockets, spatial geofencing with Leaflet.js, interactive rolling charts with Chart.js, safety event logging with SQLite, a synthetic Web Audio alert engine, and bidirectional USB serial communication with an ESP32 microcontroller at 115200 baud.

---

## 2. Technology Stack

- **Backend:** Python 3.8+, Flask 3.0, Flask-SocketIO (WebSocket), PySerial, SQLite3
- **Frontend:** Semantic HTML5, Custom High-Tech Dark Navy / Teal / Orange CSS3, Vanilla ES6+ JavaScript
- **Visualization:** Chart.js 4.4 (Live rolling telemetry charts)
- **Geographic Mapping:** Leaflet.js 1.9 (Dark Matter tiles, vehicle breadcrumb trail, danger zone circles)
- **Audio Synthesizer:** Web Audio API (Hardware-independent synthesized acoustic sirens and alert chimes)
- **Concurrency:** Non-blocking multithreaded architecture (Dedicated serial reader thread, background telemetry broadcast loop)

---

## 3. Directory & File Structure

```text
e:\AcciAlert\
│
├── app.py                      # Main application entry point: Flask server, Socket.IO routes, API endpoints
├── config.py                   # Centralized configuration: thresholds, ports, baudrate, coordinates
├── database.py                 # SQLite persistence layer: schema init, event logging, zone CRUD, CSV export
├── serial_manager.py           # PySerial communication manager with threaded non-blocking reader & auto-reconnect
├── simulator.py                # Comprehensive vehicle dynamics & mock telemetry generator with scenario bench
├── geo_utils.py                # Haversine distance, coordinate projection, and geofence collision detection
├── requirements.txt            # Python dependencies
├── README.md                   # Complete documentation and setup manual
│
├── data/
│   └── accialert.db            # SQLite database file (auto-created on startup)
│
├── templates/
│   └── index.html              # High-tech responsive dashboard single-page interface
│
└── static/
    ├── css/
    │   └── dashboard.css       # Dark navy, teal, orange cockpit design theme
    └── js/
        ├── alerts.js           # Web Audio API sound synthesizer and visual alert banner manager
        ├── charts.js           # Chart.js speed and alcohol real-time 60-second rolling charts
        ├── map.js              # Leaflet.js vehicle marker, breadcrumb path, and colored danger zones
        ├── zones.js            # Danger zone management: add, edit, delete, and coordinate pick
        └── dashboard.js        # Master application controller, Socket.IO client, event history, CSV export
```

---

## 4. Key System Features

### 4.1 10 Live Telemetry Values
1. **Vehicle Speed (km/h):** Large numeric readout with color-coded dynamic progress bar and active speed limit badge.
2. **Alcohol Sensor Level (ADC):** MQ-3 sensor ADC reading (0–1023), categorized into **NORMAL** (<350), **CAUTION** (350–599), and **DANGER** (≥600).
3. **Obstacle Distance (cm):** Ultrasonic proximity reading categorized into **CLEAR** (>100cm), **WARNING** (40–100cm), and **IMMINENT** (≤40cm).
4. **Accident / Tilt Status:** Vehicle roll/tilt angle in degrees accompanied by 3-axis accelerometer readings ($a_x, a_y, a_z$ in g-force). Rollover angle $\ge 45^\circ$ or impact magnitude $\ge 2.5g$ triggers immediate critical alert.
5. **GPS Latitude & Longitude:** Sub-meter accuracy coordinates with one-click map centering.
6. **Driver Name & Authorisation Status:** Driver identification (default: **Muniyappan**) with **AUTHORIZED** (Green) or **UNAUTHORIZED** (Red) state.
7. **Traffic Signal Status:** 3-bulb glowing V2X traffic lamp (RED / YELLOW / GREEN) with active light animation.
8. **Vehicle Status:** Operational state badge (**LOCKED / READY / RUNNING / WARNING / EMERGENCY**).
9. **ESP32 Connection Status:** Hardware status badge (**CONNECTED / DISCONNECTED / SIMULATED**) with port and baud diagnostics.
10. **GPS Connection Status:** High-precision link status (**3D FIX / NO FIX**) with active satellite count.

### 4.2 Live Rolling Charts (Last 60 Seconds)
- **Speed Chart:** Teal gradient area line chart displaying vehicle velocity over the last 60 seconds with instant reaction to braking, acceleration, and speed limits.
- **Alcohol Chart:** Orange area line chart featuring a dashed red reference line at the 600 ADC danger threshold.

### 4.3 Multi-Tone Synthetic Alert System
Uses the browser's native **Web Audio API** to synthesize dedicated audio alarms without external audio files:
- **ACCIDENT:** Alternating two-tone emergency siren.
- **ALCOHOL DETECTED:** Harsh dual-frequency warning pulse buzz.
- **UNAUTHORISED DRIVER:** Three rapid ascending warning chimes.
- **OBSTACLE DETECTED:** Sonar radar ping with exponential decay.
- **OVERSPEED:** Double chime alert.
- **DANGER ZONE:** Soft melodic dual-tone geofence entry chime.
- **TRAFFIC SIGNAL STOP:** Continuous pulsed red signal alert.

Includes a persistent **Acknowledge Alert** button to silence active sirens, plus an audio mute/unmute toggle.

### 4.4 Danger Zone Geofencing & Automatic Speed Limiting
- Add, Edit, Delete circular danger zones with:
  - Code (e.g. `Z01`, `Z02`)
  - Name (e.g. `Anna Salai School Zone`, `Guindy Sharp Curve`)
  - Latitude & Longitude (supports manual typing, "Use Current Vehicle GPS", or "Pick on Map")
  - Radius in meters (e.g. 150m)
  - Speed Limit in km/h (e.g. 30 km/h)
  - Severity: **LOW** (Teal), **MEDIUM** (Yellow), **HIGH** (Orange), **CRITICAL** (Red)
- **Automatic Speed Enforcer:** When vehicle coordinates enter a danger zone, the backend automatically issues a `LIMIT:<speed_limit>` command to the ESP32 / Simulator, triggers an audible geofence chime, and logs the event to SQLite.

### 4.5 Event Management & Audit Log
- All safety events are persisted in SQLite (`events` table).
- Interactive event filter by **Event Type**, **Date Range (From / To)**, and **Search Query**.
- **One-Click CSV Export:** Generates and downloads a complete `accialert_events_YYYYMMDD_HHMMSS.csv` spreadsheet.

### 4.6 Control Panel & ESP32 Commands
- 🛑 **EMERGENCY STOP:** Transmits `STOP` to vehicle, forcing speed to 0 km/h and setting state to `EMERGENCY`.
- 🔄 **RESET SYSTEM:** Transmits `RESET` to vehicle, restoring normal operational state.
- 🚦 **TRAFFIC SIGNAL OVERRIDE:** Transmits `SIGNAL:RED`, `SIGNAL:YELLOW`, or `SIGNAL:GREEN`.
- 👤 **DRIVER AUTHENTICATION:** Toggles between Authorized (1) and Unauthorized (0) via `AUTH:1` or `AUTH:0`.
- ⚡ **SPEED LIMITER:** Custom speed limit input transmits `LIMIT:<val>`.

### 4.7 Simulation Mode & Real ESP32 Mode
- **Simulation Mode (Default):** Realistic vehicle driving physics following a waypoint circuit in Chennai with road curvature, natural sensor variance, and interactive quick-test buttons:
  - *Simulate Accident*
  - *Simulate Alcohol Spike*
  - *Simulate Obstacle Proximity*
  - *Simulate Overspeed*
  - *Simulate Unauthorized Driver*
  - *Enter Danger Zone*
  - *Red Signal Stop*
  - *Normal Safe Drive*
- **Real ESP32 Mode:** Seamlessly connects to physical ESP32 via USB Serial at 115200 baud, continuously streaming live sensor packets.

---

## 5. ESP32 Communication Protocol

### 5.1 Telemetry Packet (ESP32 $\rightarrow$ Flask Backend)
Transmitted continuously via USB Serial at **115200 baud**, formatted as JSON terminated by newline (`\n`):

```json
{
  "t": 12345678,
  "spd": 38.5,
  "alc": 195,
  "dist": 140,
  "ax": 0.02,
  "ay": 0.01,
  "az": 1.00,
  "tilt": 3.2,
  "lat": 13.082700,
  "lon": 80.270700,
  "fix": 1,
  "state": "RUNNING",
  "auth": 1,
  "signal": "GREEN",
  "zone": "Z01",
  "evt": ""
}
```

| Field | Type | Description |
|---|---|---|
| `t` | Integer | Microcontroller timestamp / uptime in milliseconds |
| `spd` | Float | Vehicle speed in km/h |
| `alc` | Integer | MQ-3 alcohol sensor analog ADC reading (0–1023) |
| `dist` | Integer/Float | Ultrasonic sensor distance to obstacle in cm |
| `ax, ay, az` | Float | 3-axis accelerometer readings in g ($a_z \approx 1.0$ at rest) |
| `tilt` | Float | Vehicle inclination / roll angle in degrees |
| `lat, lon` | Float | GPS latitude and longitude in decimal degrees |
| `fix` | Integer | GPS fix indicator ($1 = \text{3D Fix}$, $0 = \text{Searching}$) |
| `state` | String | Vehicle status: `LOCKED`, `READY`, `RUNNING`, `WARNING`, `EMERGENCY` |
| `auth` | Integer | Driver authorization ($1 = \text{Authorized}$, $0 = \text{Unauthorized}$) |
| `signal` | String | Traffic signal status: `RED`, `YELLOW`, `GREEN` |
| `zone` | String | Current danger zone code or `NONE` |
| `evt` | String | Active hardware alert flag (`ACCIDENT`, `ALCOHOL`, `OBSTACLE`, `OVERSPEED`, etc.) |

### 5.2 Control Commands (Flask Backend $\rightarrow$ ESP32)
Commands sent over serial terminated by newline:

| Command | Format | Action on ESP32 |
|---|---|---|
| `STOP` | `STOP\n` | Cuts motor drive, triggers emergency brake and hazard lights |
| `RESET` | `RESET\n` | Clears latched alarms and restores vehicle to `RUNNING` |
| `AUTH` | `AUTH:1\n` or `AUTH:0\n` | Locks or unlocks engine based on driver facial recognition / RFID |
| `SIGNAL` | `SIGNAL:RED\n` | Informs ESP32 of traffic signal color; stops vehicle on `RED` |
| `LIMIT` | `LIMIT:30\n` | Restricts maximum speed via PWM throttle governor |
| `ZONES` | `ZONES:<payload>\n` | Syncs active geofence definitions to ESP32 onboard flash |
| `ACK` | `ACK\n` | Silences onboard buzzer and acknowledges active alert |

---

## 6. REST API Reference

| Method | Endpoint | Description | Query / Body Parameters |
|---|---|---|---|
| `GET` | `/` | Serves dashboard web application | None |
| `GET` | `/api/status` | System mode, serial connection, and active driver | None |
| `POST` | `/api/mode` | Switch between `simulation` and `serial` mode | `{"mode": "simulation"\|"serial", "port": "COM3", "baudrate": 115200}` |
| `GET` | `/api/events` | Retrieve filtered events from SQLite | `?limit=50&offset=0&type=ACCIDENT&start_date=2026-01-01` |
| `GET` | `/api/events/export` | Download filtered events history as CSV | `?type=ALL&start_date=YYYY-MM-DD` |
| `GET` | `/api/zones` | Retrieve all configured danger zones | None |
| `POST` | `/api/zones` | Create or update a danger zone | `{"code":"Z05","name":"...","latitude":13.08,"longitude":80.27,"radius_meters":150,"speed_limit":30,"severity":"HIGH"}` |
| `DELETE` | `/api/zones/<id>` | Delete a danger zone by primary key | URL parameter `id` |
| `GET` | `/api/drivers` | List registered drivers | None |
| `POST` | `/api/drivers` | Register or update driver authorisation | `{"name":"Muniyappan","license_number":"...","authorized":1}` |
| `POST` | `/api/command` | Transmit command to vehicle / simulator | `{"command":"STOP"\|"RESET"\|"SIGNAL"\|"LIMIT"\|"AUTH", "value": "..."}` |
| `GET` | `/api/serial/ports` | Enumerate available COM ports on host PC | None |
| `POST` | `/api/simulation/scenario`| Trigger an alert scenario in simulator | `{"scenario":"accident"\|"alcohol"\|"obstacle"\|"overspeed", "duration": 20}` |

### WebSocket Events (Flask-SocketIO)
- **`telemetry` (Server $\rightarrow$ Client):** Emitted every 1000ms containing full JSON telemetry.
- **`alert` (Server $\rightarrow$ Client):** Emitted when an alert threshold is breached (`ACCIDENT`, `ALCOHOL DETECTED`, etc.).
- **`status` (Server $\rightarrow$ Client):** Emitted on mode change or serial connect/disconnect.
- **`command` (Client $\rightarrow$ Server):** Transmits control panel commands.

---

## 7. Installation and Execution Guide

### Prerequisites
- Windows 10 or 11
- Python 3.8 or higher installed and added to PATH
- VS Code (recommended) or any modern web browser (Chrome, Edge, Firefox)

### Step 1: Open Project in VS Code
Open VS Code, press `Ctrl + K, Ctrl + O`, and select the `e:\AcciAlert` directory.

### Step 2: Open Terminal in VS Code
Press `` Ctrl + ` `` to open the integrated PowerShell terminal. Verify Python:
```powershell
python --version
pip --version
```

### Step 3: Install Required Dependencies
Run:
```powershell
pip install -r requirements.txt
```

### Step 4: Run the Application
Execute:
```powershell
python app.py
```

Expected output:
```text
INFO:AcciAlert.App:Background telemetry worker loop started.
INFO:AcciAlert.App:Starting AcciAlert Server on http://0.0.0.0:5000
 * Running on all addresses (0.0.0.0)
 * Running on http://127.0.0.1:5000
```

### Step 5: Open the Live Dashboard
Open your web browser and navigate to:
```text
http://127.0.0.1:5000
```

---

## 8. Operating the Dashboard

1. **Dashboard Overview:** When launched, the application immediately starts in **Simulation Mode**, streaming realistic driving telemetry along the Chennai corridor.
2. **Testing Alert Scenarios:**
   - Under the **Control Panel** tab, click **Simulate Accident** to test high tilt, impact force, and emergency siren.
   - Click **Simulate Alcohol** to test the MQ-3 threshold spike (845 ADC), banner alert, and warning sound.
   - Click **Simulate Obstacle** to verify proximity warnings (<40cm).
   - Click **Enter Danger Zone** to watch the vehicle enter a geofence and observe the automatic speed limit command applied.
3. **Connecting a Real ESP32:**
   - Plug the ESP32 into your PC via USB.
   - In the header, click **ESP32 Serial**.
   - Under the **Control Panel** tab in the **Physical ESP32 Serial Interface Setup** card, select the corresponding COM port (e.g. `COM3` or `COM4`).
   - The system automatically receives, parses, and displays real-time telemetry from the microcontroller.
4. **Managing Danger Zones:**
   - Click the **Danger Zone Management** tab.
   - Click **+ Add Danger Zone**. You can click **Pick on Map** to interactively choose coordinates on the Leaflet map.
   - Set radius, speed limit, and severity, then click **Save**.
5. **Auditing & Exporting Events:**
   - Click the **Event History & Audit Log** tab.
   - Filter by event type (e.g. `ALCOHOL DETECTED`) or search by driver.
   - Click **Export CSV** to download a spreadsheet report.

---

## 9. Verification & Safety Validation

- **Non-Blocking Operation:** The serial listener runs in an isolated daemon thread, preventing UI freezing or WebSocket latency.
- **Fail-Safe Geofence Limiter:** Speed limits are enforced whenever vehicle coordinates intersect with danger zone boundaries.
- **Debounced Event Logging:** Avoids database bloat by debouncing high-frequency alerts while maintaining continuous live visual banners.
- **Offline Reliability:** All fonts, leaflet components, and synthetic audio operate without requiring third-party audio hosting or external cloud services.

---

Developed with ❤️ by **Muniyappan** for **AcciAlert – Smart Driver Safety and Accident Prevention System**.


---

# Part 2 - Database, Danger-Zone Engine and Zone Manager (Prasanth)

Software team module owned by Prasanth: SQLite database, danger-zone engine,
zone API and the zone manager web page.

## Folder layout

```
code/
  backend/
    db.py          SQLite data layer (drivers, events, telemetry, danger_zones)
    zones.py       Danger-zone engine (haversine, ENTER/EXIT, hysteresis, pre-warning, overspeed)
    zone_api.py    Flask API for zones and events + standalone dev server
    zones.json     Starter zones (PLACEHOLDER coordinates - replace with Nivash's track map)
    tests/         pytest unit tests
    accialert.db   Created automatically on first run
  frontend/
    zones.html, zones.css, zones.js   Zone manager page (Leaflet)
```

## Requirements

- Python 3.10+
- Flask and pytest

```bash
pip install flask pytest
```

The map page loads Leaflet and OpenStreetMap tiles from the internet.

## Run the tests

From the `code` folder:

```bash
python -m pytest backend/tests -q
```

## Run the zone manager

From the `code` folder:

```bash
python backend/zone_api.py
```

Open http://127.0.0.1:5000/ in a browser. Stop the server with Ctrl+C.

On first start the 6 zones in `backend/zones.json` are loaded into `accialert.db`.
After that the database is the source of truth (zones.json is only used to add missing ids).

## Backup and telemetry purge

```bash
python backend/backup.py              # writes backend/backups/accialert_<date>_<time>.db
python backend/backup.py --keep 20    # keep the newest 20 (default 10)
python backend/backup.py --dest D:/usb
```

Safe to run while the server is running. Each backup is integrity-checked.
`zone_api.py` also starts a background job that deletes telemetry older than 24 h (hourly).
In another app, call `db.start_purge_scheduler()` once at startup.

## Using the page

- Click the map to place a new zone, choose a type (fills default radius, limit, severity), then Save.
- Click a circle or press Edit to change a zone. On/Off enables or disables it. Del removes it.
- All changes apply immediately, no server restart.
- The Zone events table filters by type and date and exports CSV.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/zones` | List zones |
| POST | `/api/zones` | Add a zone (JSON body, all fields required) |
| PUT | `/api/zones/<id>` | Edit a zone |
| DELETE | `/api/zones/<id>` | Delete a zone |
| GET | `/api/events?type=&from=&to=&limit=` | Events, newest first (`from`/`to` are epoch seconds) |
| GET | `/api/events.csv` | Same filters, CSV download |

Zone fields: `id, name, type, lat, lon, radius_m, speed_limit, severity (low|medium|high|critical), active`.

Quick check with curl:

```bash
curl http://127.0.0.1:5000/api/zones
curl "http://127.0.0.1:5000/api/events?type=ZONE_ENTER"
```

## Using the engine from other modules

```python
import db
from zones import ZoneEngine, zones_to_esp32_cmd

conn = db.connect()
engine = ZoneEngine(db.list_zones(conn))

# on every GPS fix (about 1 Hz):
for ev in engine.update(lat, lon, speed_kmh):
    db.log_event(conn, ev["type"], ev["severity"], driver=driver, speed=ev["speed"],
                 lat=ev["lat"], lon=ev["lon"], zone_id=ev["zone_id"], details=ev["details"])
    # then push ev over WebSocket (log first - accident events must never be dropped)
```

Event types produced: `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_APPROACH`, `OVERSPEED_IN_ZONE`.
`zones_to_esp32_cmd(zones)` builds the `{"cmd":"ZONES",...}` line for the ESP32.

To plug the API into Muniyappan's Flask app:

```python
from zone_api import create_zone_blueprint
app.register_blueprint(create_zone_blueprint(conn, engine, on_zones_changed=send_to_esp32))
```

## Integration contract (for Muniyappan, Selvamani, Arsal)

Pass the ESP32 telemetry fields straight through. The engine is built so bad data cannot crash your threads.

| Call | Guarantee |
|---|---|
| `engine.update(lat, lon, spd, fix)` | Never raises. Returns a list of events. `fix` is the telemetry `fix` field. |
| No GPS (`fix` = 0), `lat`/`lon` missing, NaN, out of range, or exactly `0, 0` | Ignored: no events and the in/out state is held. No false EXIT, no duplicate ENTER when GPS returns. |
| `engine.set_zones(list)` | Skips invalid zones and returns the rejection messages. Safe to call from any thread. |
| `db.log_event(...)` | Commits before it returns. Never rejects an event for bad field values (an ACCIDENT is never lost): bad severity becomes `high` (original kept in details), bad numbers become NULL, long text is truncated. |
| `db.*` functions | One shared connection is safe from the serial, Flask and purge threads (process-wide lock). |
| `db.upsert_zone(zone)` | Raises `ValueError` with a safe message on invalid zones. |
| `zones_to_esp32_cmd(zones)` | Active, valid zones only, most severe first, coordinates rounded to 6 decimals, capped at 20 zones (`MAX_ESP32_ZONES`; Selvamani to confirm the ESP32 buffer size). |
| `on_zones_changed(cmd)` callback | If it raises, the zone change is still saved and the API still returns success (the error is logged). |

Units: speed in km/h, distances in metres, timestamps in epoch seconds.
Event types: `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_APPROACH`, `OVERSPEED_IN_ZONE`.
Zone ids: 1 to 32 characters of letters, digits, `_` or `-`.

API safety: JSON objects only, request body limited to 64 KB, `limit` clamped to 1..5000, errors are generic (no database details), CSV export neutralises spreadsheet formulas, server binds to 127.0.0.1 and has no login (local demo only).

## Notes

- Zone coordinates in `zones.json` are placeholders until the track map is ready.
- Engine defaults: 5 m exit hysteresis, 20 m pre-warning ring. Pass `hysteresis_m=` / `prewarn_m=` to `ZoneEngine` to change them (a smaller value suits the miniature track).
- The events table refreshes by polling every 3 s. Live push is handled by the dashboard WebSocket layer.
