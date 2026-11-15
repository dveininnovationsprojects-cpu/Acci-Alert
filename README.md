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

## Quick start

1. [What you need](#1-what-you-need)
2. [Run it, step by step](#2-run-it-step-by-step) (install, face models, start, open, stop)
3. [First-time setup in the dashboard](#3-first-time-setup-in-the-dashboard) (enrol drivers, zones, "Waiting for ESP32")
4. [Connect the ESP32](#4-connect-the-esp32-real-car) (same Wi-Fi, laptop address, firewall, phone test, pairing, power up, USB option)
5. [If something goes wrong](#5-if-something-goes-wrong) (troubleshooting table)
6. [Pages and how to use the dashboard](#6-pages-and-how-to-use-the-dashboard) (Driver Login, Lock vehicle, Controls tab)
7. [SMS alerts (Circuit Digest Cloud)](#sms-alerts-circuit-digest-cloud-free-india) (set up, test, limits)

The short version, once everything is installed:
```bash
python backend/app.py          # then open http://127.0.0.1:5173/
```

## 1. What you need

- A Windows, Mac or Linux laptop with **Python 3.10 or newer** (check with `python --version`).
- A webcam (for the driver's face login).
- Internet, once, to install the packages and download the face models.
- For the real car: the ESP32 on the **same Wi-Fi** as the laptop (or a USB cable).

## 2. Run it, step by step

Open a terminal **in the project folder** (the one that contains `backend`, `frontend` and this README).

**Step 1. Install the packages (once)**
```bash
pip install -r backend/requirements.txt
```

**Step 2. Download the face models (once, about 37 MB)**
```bash
python -m backend.tools.get_models
```
Without them the face login and driver enrolment do not work ("SFace models are missing").

**Step 3. Start the server**
```bash
python backend/app.py
```
Leave this terminal open. You should see a box with the addresses and the line `Server Port: 5173`.
If it says `port 5173 is already used`, another copy is running: close it first (Windows: `taskkill /F /IM python.exe`), then start again.

**Step 4. Open the dashboard in Chrome or Edge**

http://127.0.0.1:5173/  (the first page is the driver face login)

(On Windows use `127.0.0.1`, not `localhost`: `localhost` makes every request about 2 seconds slower.)
When the browser asks, **allow the camera** (face login) and **allow location** (the laptop position is shown when the GPS module has no fix).

**Step 5. Stop the server** with `Ctrl+C` in the terminal.

## 3. First-time setup in the dashboard

1. Open the **Admin Panel** (button at the bottom right of the first page, password `1234`), then **Face Recognition**, then **Enrol Drivers**. Type the driver's name and license number and click **Start Enrolment Capture**. Enrolment uses the same head turn as the login: follow the arrows on the camera (look straight, turn your head **RIGHT**, then **LEFT**, then look straight). Those pictures are the enrolment (about 25 to 35), and a photo cannot be enrolled because it cannot turn. Keep only one person in view. Repeat for each driver.
2. The 37 Tamil Nadu danger zones are already loaded. Add or edit zones in the admin panel, **Danger Zone Management** tab.
3. The dashboard shows **Waiting for ESP32** and no values until the car sends its first reading. The system has no fake data.

## 4. Connect the ESP32 (real car)

Use the Wi-Fi way. Do these in order:

The ready firmware is in `firmware/AcciAlert_ESP32/` (open `AcciAlert_ESP32.ino` in Arduino IDE; libraries ArduinoJson 7 and TinyGPSPlus; board "ESP32 Dev Module"). It has all sensors, the motor, the buzzer and the Wi-Fi link to this software. Wi-Fi name, password and the laptop address are the three `#define` lines near the top of that file. To test the laptop side without the board: `python firmware/simulate_esp32.py http://127.0.0.1:5173 <MAC>` (see the top of that file).

1. Put the laptop and the ESP32 on the same Wi-Fi. Find the laptop's address with `ipconfig` (IPv4 of the Wi-Fi adapter, for example `192.168.1.19`) and put it in the firmware as the server address (`SERVER_BASE_URL`).
2. If the ESP32 cannot reach the laptop, allow port 5173 in the Windows firewall (administrator terminal):
   ```bash
   netsh advfirewall firewall add rule name="AcciAlert 5173" dir=in action=allow protocol=TCP localport=5173 profile=any
   ```
3. Start the server (Step 3 above). Test from a phone on the same Wi-Fi: `http://<laptop-address>:5173/api/v1/ping` must answer `ok`.
4. Flash the firmware and read the ESP32's MAC address on the serial monitor (115200 baud).
5. Pair it, once. This opens a 15 minute window in which that MAC address may register:
   ```bash
   python -m backend.tools.devices pair --mac AA:BB:CC:DD:EE:FF
   ```
6. Power up the ESP32. It registers, keeps its token in flash, and starts sending readings. The dashboard header changes to **Live ESP32 data**. Check the pairing with `python -m backend.tools.devices list` (status `active`).

USB cable instead of Wi-Fi: connect the cable, open the **Controls** tab, choose the COM port and 115200 baud.
Details, message formats and an Arduino example: `docs/ESP32_TELEMETRY_API.md`.

## 5. If something goes wrong

| Problem | What to do |
|---|---|
| `port 5173 is already used` or pages give 404 errors | An old server is still running. Close all of them (`taskkill /F /IM python.exe`) and start one |
| "SFace models are missing" | Run Step 2 (`python -m backend.tools.get_models`) |
| Camera does not start | Allow the camera in the browser, close other programs using it, open the page at `http://127.0.0.1:5173/` |
| Dashboard shows "OFFLINE" | The server is not running or the address is wrong |
| "Waiting for ESP32" stays | The ESP32 is not paired or cannot reach the laptop (see Connect the ESP32) |
| Position shows "No position" | The GPS module has no fix and the browser location is blocked |
| ESP32 gets `403 pairing_closed` | The pairing window expired. Run the `pair` command again |
| ESP32 gets `409 already_registered` | Run `python -m backend.tools.devices reset`, then `pair` again |

## 6. Pages and how to use the dashboard

### Pages

| Address | What it is |
|---|---|
| `/` | **First page: driver face login.** The camera opens by itself, shows ENGINE START for an authorised face, then opens the dashboard. **Admin Panel** button at the bottom right |
| `/home` | Dashboard: readings, charts, map, alerts, **SOS** button, Lock vehicle. Opens only after a face login |
| `/admin-login`, `/admin` | Admin panel (password): enrol drivers, driver logs, Danger Zone Management, Controls (Reset, Acknowledge, ESP32 serial setup), Event history |
| `/dashboard` | Fleet dashboard (without the face login) |
| `/zm/` | Zone manager on its own |

### Using the dashboard

- **Event history tab:** shows the 5 latest events. Click **View** for one event, or **View all events** for the whole history, 20 per page. The filters and **Export CSV** use the same search.
- **Danger Zone Management tab and Zone Manager:** both edit the same list of zones. A change shows up on every map.
- **First page, driver login:** nothing to press. The camera opens, and when one face is in view the page shows arrows on the camera picture: look straight, then turn your head **RIGHT** (arrow on the right), then **LEFT** (arrow on the left), then look straight again. Each arrow turns green when the turn is seen. The server measures the turn from where the nose sits between the eyes, compared with your own straight position, so a photo does not work. An authorised driver sees **ENGINE START** and the dashboard opens; anyone else is refused and the page tries again after a few seconds. The request is valid once and for 45 seconds, and the head-turn check cannot be switched off from the page. If real drivers are often refused, or a small turn is accepted too easily, tune `HEAD_TURN_MIN` and `HEAD_STRAIGHT_MAX` in `config.py` (the login answer lists `yaw_levels` to help). Good light on the face matters. Every attempt is logged. **Lock vehicle** (dashboard, top right) locks the car and returns to the first page. **SOS** (top right) asks for confirmation, records the emergency and texts the owner with the car's position.
- **Admin panel:** password `1234` (change it with the environment variable `ACCIALERT_ADMIN_PASSWORD`). Five wrong passwords lock that computer for a minute, and the login ends after 30 minutes. Changes to drivers, zones, Reset and the serial port are refused without it.
- **Enrol Drivers:** see First-time setup. Each driver in the roster has **View** and **Delete** buttons (Delete asks first). Deleting removes the face data, the record, the enrolment history and the saved photos, and locks the car if that driver had unlocked it. The event log keeps a `DRIVER_DELETED` entry. The roster shows the 5 newest drivers, and **View** shows the details and history.
- **Admin panel, Controls tab:** Reset System and Acknowledge Alert (mute the siren). The Emergency Stop button was removed.

## SMS alerts (Circuit Digest Cloud, free, India)

**On/off button:** Admin Panel, **Controls** tab, **SMS alerts**. Off means no text at all (alerts, SOS and the test). The choice is kept after a restart (`backend/data/sms_switch.json`); the key and numbers are not touched. `ACCIALERT_SMS_ENABLED=0` still forces it off.

The laptop texts the owner when one of these starts: **accident**, **alcohol detected**, **the car moving with no
authorised driver**, or **an unauthorised person trying to start the car** (a face shown to the camera and rejected).
Pressing **SOS** on the dashboard also texts the owner (it ignores the hourly and daily limits and can repeat after 30 seconds).
Overspeed, obstacles and danger-zone alerts are not texted.

It uses the free SMS API of Circuit Digest Cloud (circuitdigest.cloud). It works for **Indian mobile numbers only**, and the
text is a **ready-made template** with two slots (`var1`, `var2`): you cannot write your own sentence or add a map link.
By default `var1` is the alert (Accident, Alcohol, UnauthorisedDriver, UnauthorisedLogin) and `var2` is the driver's name.
Each value is letters and digits only, at most 30. The free plan allows about 100 SMS a month, so the software stops at 90.

**Set up (once):**
1. Create an account at circuitdigest.cloud and get your **API key**.
2. On their dashboard add and verify the mobile numbers that should get the texts (they use an OTP).
3. Pick a template and note its **template ID** (a number, like `103`) and its wording, so you know what the two slots say.
4. On the laptop run:
   ```bash
   python -m backend.tools.sms setup
   ```
   It asks for the API key (typed hidden), the template ID and the numbers (10 digits, separated by commas). It saves them in `backend/data/sms_settings.json`, which is **not** in Git. Nothing is written in the code. (You can use the environment variables `ACCIALERT_SMS_API_KEY`, `ACCIALERT_SMS_TEMPLATE_ID` and `ACCIALERT_SMS_TO` instead.)
5. Send a test: `python -m backend.tools.sms test`
6. Other commands: `status`, `log` (what was sent and whether the service accepted it), `disable`, `enable`.

To change what goes into the slots, edit `var1` and `var2` in `backend/data/sms_settings.json`. Each can be `alert` (Accident), `alert_text` (vehicle accident), `driver`, `speed`, `time` or `location` (a Google Maps link to the car, or "unknown location").
Example for template 101 ("Your {var1} is currently at {var2}."): `"template_id": "101", "var1": "alert_text", "var2": "location", "value_style": "free"` gives "Your vehicle accident is currently at https://maps.google.com/?q=13.05000,80.25000."
`value_style` is `plain` (letters and digits only, up to 30, as the service's tutorial says) or `free` (readable text and links, up to 100). If a text is refused, try `plain`.

**It cannot spam:** the same alert is texted once per 5 minutes, at most 6 texts per hour, 20 per day and 90 per month (each recipient counts). The counts are kept in the database, so a restart does not reset them. Texts are sent in the background, so a slow service never delays the dashboard. If SMS is not set up, nothing is sent and nothing breaks.

**Good to know:** the service may refuse a text if its template is not approved for India's telecom rules (DLT), so always send a test first. Check the current limits and the exact API address on their dashboard.

## Where things are saved

All of this stays on your computer and is not in Git.

| What | Where |
|---|---|
| Events, drivers, enrolment history | `backend/data/accialert.db` |
| Zones, zone events, telemetry (kept 24 hours) | `backend/data/accialert_zones.db` |
| Face signatures | `backend/data/face_gallery.npz` |
| SMS settings (secret) | `backend/data/sms_settings.json` |
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

## The danger-zone map

- The map looks like Google Maps (Esri street map) and has a **Map / Satellite** switch. Each danger spot is a pin; click it for the details and an **Open in Google Maps** link.
- **Live location:** the map shows this device's own position as a blue dot with a light circle for the accuracy (like Google Maps), updating as you move. Press **My location** to go to it. Allow location in the browser the first time. The dot works even with no ESP32 connected. The car (ESP32 GPS) is a blue car icon on top.
- **Notification distance:** in the admin panel, **Danger Zone Management**, choose how close the car (or you) must be for the danger zone notification: 100 m, 250 m, 500 m, 1 km, 2 km, 5 km or your own distance (50 to 5000 m). The default is 1 km. Then a message such as "Danger zone ahead: Kathipara junction, about 420 m away" shows once per approach, and "You are inside a danger zone" when you enter one. The map shows the spots within the same distance, and the zone engine records the "approaching" event at that distance too. Only the admin can change it.
- **One map:** the page shows a single Google-style street map (with a Map / Satellite switch). The car is a blue car icon like the one on Google Maps, and your own live location is the blue dot. Advanced: start the server with the environment variable `ACCIALERT_GOOGLE_MAPS_KEY` set to a Google Maps API key and the same map is drawn on Google Maps itself (with Google's Traffic layer); if Google refuses the key, the street map is used. Google does not publish accident black spots to other programs, so the accident spots on the map are the ones in `docs/ZONE_SOURCES.md` and in Danger Zone Management.
- **Every danger spot always has a pin** on the map (a small Google-style pin, never a filled red area). The light circle around a spot, and the notification, are for the spots **within the notification distance** of the car (or of your live location). The card on the map says how many spots there are, how many are near, and the nearest one; **Go to nearest danger spot** moves the map there. Untick **Show all danger spots** to see only the near ones.
- The 37 Tamil Nadu zones are whole districts (5 km radius), so they are drawn only as a thin dashed outline while you are inside them, never as a filled circle.
- 13 named Chennai accident spots from public reports (Nazarathpet, Thandalam, Kathipara and others) are loaded once. Where each came from, and what was left out, is in `docs/ZONE_SOURCES.md`. Their positions are the place names found on OpenStreetMap, so check them and edit them in the admin panel if needed.

## Danger zone rules

- **Enter:** the car is inside the zone circle. One event is recorded.
- **Exit:** the car is more than 5 m outside the circle.
- **Warning:** the car is within 20 m of the circle.
- **Overspeed:** the car is over the zone limit while inside. Recorded once per visit.
- **No GPS:** bad or missing positions are ignored, so a GPS drop-out does not cause a false exit.

## ESP32 messages

115200 baud, one JSON line per message.

- ESP32 to laptop, about 5 times a second:
  `{"t":123456,"spd":45,"alc":10,"dist":150,"tilt":0,"lat":13.0827,"lon":80.2707,"fix":1,"state":"RUNNING","auth":1,"zone":0}`
- Laptop to ESP32: `AUTH`, `STOP`, `RESET`, `LIMIT`, `ACK`. Example: `{"cmd":"AUTH","ok":1,"driver":"Arun"}`

## ESP32 over Wi-Fi

The ESP32 can send its readings and collect the dashboard's commands over Wi-Fi. The device id is set on the backend
(`DEVICE_ID` in `config.py`) and the firmware holds no key: it pairs once with its MAC address and keeps the token in flash.

```
python -m backend.tools.devices pair --mac AA:BB:CC:DD:EE:FF     (opens a 15 minute pairing window)
POST /api/v1/device/register          ESP32 gets its device id and token (once)
POST /api/v1/telemetry                readings, stored with a timestamp and shown on the dashboard
GET  /api/v1/device/commands          STOP, RESET, ACK, AUTH, LIMIT, ZONES waiting for the ESP32, plus the lock state
POST /api/v1/device/commands/ack      confirm commands so they are not run twice
```

Full format, errors and an ESP32 example: `docs/ESP32_TELEMETRY_API.md`.

## Good to know

- Zone changes are sent to the ESP32 over Wi-Fi as the `ZONES` command (format in `docs/ESP32_TELEMETRY_API.md`). Selvamani's firmware must read it. The serial (USB) link does not carry zone lists.
- The dashboard API has no login. Use it on a local network for the demo only. Nothing on the dashboard is simulated: with no reading you see no values. Commands are limited to `STOP`, `RESET`, `ACK`, `AUTH` and `LIMIT`.
- Face signatures and photos are personal data. Keep them private.
