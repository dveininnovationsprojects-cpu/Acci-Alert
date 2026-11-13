# AcciAlert - Database, Danger-Zone Engine and Zone Manager

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
