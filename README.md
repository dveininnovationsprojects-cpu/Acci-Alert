# AcciAlert

Smart Driver Safety and Accident Prevention System.

An ESP32 in the car sends sensor data to a laptop. The laptop runs one Python server that shows a live
dashboard, watches the danger zones, records events and checks the driver's face before the car starts.

## Who built what

| Part | Owner |
|---|---|
| Dashboard (readings, charts, map, alerts, events) | Muniyappan |
| Face login | Arsal |
| Danger zones, zone manager, databases, backups | Prasanth |
| ESP32 firmware and serial protocol | Selvamani |

## Run it

Open a terminal in the `code` folder. You need Python 3.10 or newer.

1. Install the packages (first time only):
   ```bash
   pip install -r backend/requirements.txt
   ```
2. Download the face models (first time only):
   ```bash
   python -m backend.tools.get_models
   ```
3. Start the server:
   ```bash
   python backend/app.py
   ```
4. Open http://localhost:5173/ in the browser. Stop the server with Ctrl+C.

Without an ESP32 the dashboard shows fake car data. To use the real car, click **ESP32 Serial** and pick the COM port.

## Pages

| Address | What it is |
|---|---|
| `/` | Main dashboard. The Zone Manager button and the Face Recognition tab are inside it |
| `/dashboard` | Fleet dashboard |
| `/auth.html` | Face login page |
| `/zm/` | Zone manager on its own |

## Using the dashboard

- **Event history tab:** shows the 5 latest events. Click **View** for one event, or **View all events** for the whole history, 20 per page. The filters and **Export CSV** use the same search.
- **Danger Zone Management tab and Zone Manager:** both edit the same list of zones. A change shows up on every map.
- **Face Recognition tab:** open **Enrol Drivers**, type a name and license number, and click **Start Enrolment Capture**. The camera takes 25 frames and the driver is ready. The roster shows the 5 newest drivers, and **View** shows the details and history.
- **Live Detector:** start the camera and move your head a little. The car unlocks when the face matches.

## Where things are saved

All of this stays on your computer and is not in Git.

| What | Where |
|---|---|
| Events, drivers, enrolment history | `backend/data/accialert.db` |
| Zones, zone events, telemetry (kept 24 hours) | `backend/data/accialert_zones.db` |
| Face signatures | `backend/data/face_gallery.npz` |
| Photos taken at enrolment | `backend/dataset/<name>/` |

Back everything up with:
```bash
python backend/backup.py
```
Backups go to `backend/backups/`. The newest 10 of each file are kept.

## Tests

```bash
python -m pytest backend -q
```

## Folders

```
backend/     the server, database code, zone engine, face code and tests
frontend/    the web pages (dashboard, face login, zone manager)
docs/        test plan, test reports, requirements
firmware/    ESP32 serial protocol notes
```

## Danger zone rules

- **Enter:** the car is inside the zone circle. One event is recorded.
- **Exit:** the car is more than 5 m outside the circle.
- **Warning:** the car is within 20 m of the circle.
- **Overspeed:** the car is over the zone limit while inside. Recorded once per visit.
- **No GPS:** bad or missing positions are ignored, so a GPS drop-out does not cause a false exit.

## ESP32 messages

115200 baud, one JSON line per message.

- ESP32 to laptop, about 5 times a second:
  `{"t":123456,"spd":45,"alc":10,"dist":150,"tilt":0,"lat":13.0827,"lon":80.2707,"fix":1,"state":"RUNNING","auth":1,"signal":"GREEN","zone":0}`
- Laptop to ESP32: `AUTH`, `STOP`, `RESET`, `LIMIT`, `SIGNAL`. Example: `{"cmd":"AUTH","ok":1,"driver":"Arun"}`

## Good to know

- Zone changes are not sent to the ESP32 yet. The message format still has to be agreed with Selvamani.
- The server has no login. Use it on a local network for the demo only.
- Face signatures and photos are personal data. Keep them private.
