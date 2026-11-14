# AcciAlert

Smart Driver Safety and Accident Prevention System. An ESP32 in the car sends sensor data to a laptop.
The laptop runs one Flask server that shows the dashboard, checks danger zones, logs events and
checks the driver's face before the car starts.

## Parts and owners

| Part | What it does | Owner |
|---|---|---|
| Dashboard | Live readings, charts, map, alerts, event log, controls | Muniyappan |
| Face login | Face recognition and movement check, enrolled drivers | Arsal |
| Danger zones | Zone engine, zone manager page, zone events | Prasanth |
| Database and backups | SQLite storage, 24 h telemetry purge, backup script | Prasanth |
| ESP32 firmware | Serial protocol and car hardware | Selvamani |

## Folder layout

```
code/
  backend/
    app.py             Server. Start this one
    config.py          Port, serial settings, paths, limits
    serial_link.py     ESP32 serial: telemetry reader and face-login (AUTH) sender
    simulator.py       Fake car data when no ESP32 is connected
    zones.py           Danger-zone engine and distance helpers
    zone_api.py        Zone manager API (under /zm)
    zones.json         Starting zones
    database.py        Dashboard database: events, zones, drivers, commands
    db.py              Zone database: zone events, telemetry, zones
    db_logger.py       Saves face login results
    face_detector.py   Finds faces and eyes
    face_recognizer.py Recognises drivers: LBPH model and the pretrained SFace model
    liveness.py        Movement check
    train_model.py     Trains the face model from dataset/
    backup.py          Database backup script
    tools/             Scripts: get_models, camera test, T8 accuracy check
    tests/             All automated tests
    models/            Face models (LBPH model, Haar files, SFace and YuNet .onnx files)
    data/              Database files and face signatures (not in Git)
    dataset/           Photos saved at enrolment, one folder per driver (not in Git)
  frontend/
    index.html         Dashboard (main page)
    dashboard.html     Fleet dashboard page
    auth.html          Face login page
    zones.html/.css/.js   Zone manager page
    static/            Dashboard CSS and JavaScript
    css/, js/          Face login page files
  docs/                Test plan, reports, guides
  firmware/            ESP32 serial protocol notes
```

## Run it

Run these from the `code` folder. Python 3.10 or newer is needed.

```bash
pip install -r backend/requirements.txt
```
```bash
python backend/app.py
```

Open http://localhost:5173/

| Address | Page |
|---|---|
| `/` | Dashboard (Zone Manager and Face Recognition are inside it) |
| `/dashboard` | Fleet dashboard |
| `/auth.html` | Face login page |
| `/zm/` | Zone manager on its own |
| `/api/health` | Quick check that the server is up |

Without an ESP32 the dashboard shows simulated data. Choose **ESP32 Serial** and a COM port to use the real car.
Stop the server with Ctrl+C.

## Tests

```bash
python -m pytest backend -q
```

The T8 face test writes `docs/T8_test_report.md` and `docs/accuracy_report.json` every time it runs.

## Face login

The face check uses OpenCV's pretrained YuNet and SFace models. Download them once:

```bash
python -m backend.tools.get_models
```

Then start the server, open the **Face Recognition** tab, choose **Enrol Drivers**, type a name and click
**Start Enrolment Capture**. The browser camera takes live frames and the server saves the face signatures in
`backend/data/face_gallery.npz`, and the captured frames are kept as photos in `backend/dataset/<name>/`.
There is no training step. Once one driver is enrolled the
face check uses SFace. Drivers that were only in the old LBPH model must be enrolled live again.

Other face tools:

```bash
python -m backend.tools.camera_test
python -m backend.train_model     # old LBPH model, needs photos in backend/dataset/<name>/
```

## Zone rules

- **Enter:** the car is inside a zone circle. One event is logged.
- **Exit:** the car is more than 5 m outside the circle.
- **Pre-warning:** the car is within 20 m of the circle.
- **Overspeed:** the car is over the zone limit while inside. Logged once per episode.
- **No GPS:** bad or missing GPS positions are ignored, so there is no false exit.

Zone events appear in the dashboard event table as `ZONE_ENTER`, `ZONE_EXIT` and `OVERSPEED_IN_ZONE`.

## Main API addresses

| Address | Purpose |
|---|---|
| `GET /api/status` | System and serial status |
| `GET /api/events` | Event log with filters |
| `GET, POST /api/zones` | Dashboard zone list |
| `GET, POST /api/drivers` | Driver list |
| `POST /api/command` | Send STOP, RESET or SIGNAL to the ESP32 |
| `POST /api/mode` | Switch Simulation or ESP32 Serial |
| `POST /api/verify` | Check a face and the liveness result |
| `GET, POST /zm/api/zones` | Zone manager: list or add a zone |
| `PUT, DELETE /zm/api/zones/<id>` | Zone manager: edit or delete a zone |
| `GET /zm/api/events` | Zone events with type and date filters |

## ESP32 serial protocol

115200 baud, one JSON line per message.

- ESP32 to laptop, about every 200 ms:
  `{"t":123456,"spd":45,"alc":10,"dist":150,"tilt":0,"lat":13.0827,"lon":80.2707,"fix":1,"state":"RUNNING","auth":1,"signal":"GREEN","zone":0}`
- Laptop to ESP32: `AUTH`, `STOP`, `RESET`, `LIMIT`, `SIGNAL`. Example: `{"cmd":"AUTH","ok":1,"driver":"Arun"}`

## Backup

```bash
python backend/backup.py
```

Saves a checked copy of the database to `backend/backups/` and keeps the newest 10.

## Good to know

- Zone changes are not yet sent to the ESP32. The command format still has to be agreed with Selvamani.
- The server has no login and is meant for the local demo only.
- Database files stay on your machine and are not saved in Git.
