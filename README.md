# AcciAlert

Smart Driver Safety and Accident Prevention System. An ESP32 car sends sensor data to a laptop.
The laptop runs a Flask server that shows a live dashboard, checks danger zones and logs events.

## What is in the project

| Part | What it does | Owner |
|---|---|---|
| Live dashboard | Cards, charts, map, alerts, event log, controls | Muniyappan |
| Danger-zone engine | Detects zone enter, exit, pre-warning and overspeed | Prasanth |
| Zone manager | Add, edit, delete zones on a map | Prasanth |
| Database and backups | SQLite storage, 24 h telemetry purge, backup script | Prasanth |

## Folder layout

```
code/
  backend/
    app.py            Main server (start this)
    config.py         Port, serial settings, thresholds
    database.py       Dashboard database (events, zones, drivers)
    serial_manager.py ESP32 USB serial link
    simulator.py      Fake car data when no ESP32 is connected
    geo_utils.py      Distance and zone helpers
    db.py             Zone database (zone events, telemetry, zones)
    zones.py          Danger-zone engine
    zone_api.py       Zone manager API
    zones.json        Starting zones
    backup.py         Database backup script
    data/             Database files (not in Git)
    tests/            Zone engine tests
    test_app.py       Dashboard tests
  frontend/
    index.html        Dashboard page
    static/           Dashboard CSS and JavaScript
    zones.html/.css/.js   Zone manager page
  docs/               Test plan and reports
```

## Run it

Run these from the `code` folder. You need Python 3.10 or newer and an internet connection for the map.

```bash
pip install -r backend/requirements.txt pytest
```
```bash
python backend/app.py
```

Open http://127.0.0.1:5000/

- The dashboard opens first. It runs on simulated data, so no ESP32 is needed.
- Click **Zone Manager** to open the zone manager. Click **← Dashboard** to go back.
- Click **ESP32 Serial** and choose the COM port to use the real car.
- Stop the server with Ctrl+C.

## Run the tests

```bash
python -m pytest backend -q
```

## How the zones work

- **Enter:** the car is inside a zone circle. An event is logged once.
- **Exit:** the car is more than 5 m outside the circle.
- **Pre-warning:** the car is within 20 m of the circle.
- **Overspeed:** the car goes over the zone limit while inside. Logged once per episode.
- **No GPS:** bad or missing GPS positions are ignored, so there is no false exit.

Zone events show in the dashboard event table as `ZONE_ENTER`, `ZONE_EXIT` and `OVERSPEED_IN_ZONE`.

## Main addresses

Dashboard (Muniyappan):

| Address | Purpose |
|---|---|
| `GET /api/status` | System and serial status |
| `GET /api/events` | Event log with filters |
| `GET /api/events/export` | Event log as CSV |
| `GET, POST /api/zones` | Dashboard zone list |
| `GET, POST /api/drivers` | Driver list |
| `POST /api/command` | Send STOP, RESET, SIGNAL to the ESP32 |
| `POST /api/mode` | Switch Simulation or ESP32 Serial |

Zone manager (Prasanth), all under `/zm`:

| Address | Purpose |
|---|---|
| `GET, POST /zm/api/zones` | List or add a zone |
| `PUT, DELETE /zm/api/zones/<id>` | Edit or delete a zone |
| `GET /zm/api/events` | Zone events with type and date filters |
| `GET /zm/api/events.csv` | Zone events as CSV |

A zone has: `id, name, type, lat, lon, radius_m, speed_limit, severity (low, medium, high, critical), active`.

## Backup

```bash
python backend/backup.py
```

Saves a checked copy to `backend/backups/`. It keeps the newest 10. Old telemetry (older than 24 h) is deleted automatically every hour.

## Good to know

- The zone coordinates in `zones.json` are placeholders until the track map is ready.
- Zone changes are not yet sent to the ESP32. The command format still needs to be agreed with Selvamani.
- The server has no login and is meant for the local demo only.
- Database files stay on your machine and are not saved in Git.
