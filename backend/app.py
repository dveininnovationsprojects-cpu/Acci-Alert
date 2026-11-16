"""Main server: dashboard, zone manager, face login and the ESP32 link on one port (5173).

Parts: face recognition (Arsal), danger zones and zone manager (Prasanth),
dashboard and map (Muniyappan), serial protocol (Selvamani).
Start with:  python backend/app.py
"""

import base64
import hashlib
import json
import logging
import math
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime
from typing import Optional
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, request, jsonify, Response, send_from_directory, redirect
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

# Ensure backend directory is in sys.path
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import config
    import database
    from serial_link import SerialManager
    import db as zone_db
    from zones import ZoneEngine, find_active_danger_zone
    from zone_api import mount_zone_manager
    from face_detector import FaceDetector
    from face_recognizer import FaceRecognizer, SFaceRecognizer
    from liveness import LivenessDetector, ChallengeBook
    from db_logger import DatabaseLogger
    from device_api import create_device_blueprint, get_api_key, reset_tilt_reference
    import device_registry
    import sms_alert
    import admin_auth
    import headturn
except ImportError:
    from backend import config
    from backend import database
    from backend.serial_link import SerialManager
    from backend import db as zone_db
    from backend.zones import ZoneEngine, find_active_danger_zone
    from backend.zone_api import mount_zone_manager
    from backend.face_detector import FaceDetector
    from backend.face_recognizer import FaceRecognizer, SFaceRecognizer
    from backend.liveness import LivenessDetector, ChallengeBook
    from backend.db_logger import DatabaseLogger
    from backend.device_api import create_device_blueprint, get_api_key, reset_tilt_reference
    from backend import device_registry
    from backend import sms_alert
    from backend import admin_auth
    from backend import headturn

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
)
logger = logging.getLogger("AcciAlert.UnifiedServer")

# Initialize Flask & SocketIO
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
app = Flask(__name__, template_folder=FRONTEND_DIR,
            static_folder=os.path.join(FRONTEND_DIR, "static"))
app.config["SECRET_KEY"] = config.SECRET_KEY
CORS(app)

# Use threading mode for broad compatibility on Windows
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Initialize SQLite database
database.init_db()

def safe_number(value, default=0.0, as_int=False):
    """value as a finite number, or `default` if it is missing, text, NaN or infinite.
    A bad value from the ESP32 must never stop a whole packet."""
    try:
        if isinstance(value, bool):
            raise ValueError("bool")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("not finite")
    except (TypeError, ValueError):
        if default is None:           # the caller wants to know that the value is not usable
            return None
        number = float(default)
    return int(number) if as_int else number


# Commands the dashboard may send. STOP, RESET and ACK take no value.
ALLOWED_COMMANDS = ("STOP", "RESET", "ACK", "AUTH", "LIMIT")


def check_command(command, value=None):
    """Return (COMMAND, value) if the command is allowed, else raise ValueError with a readable message."""
    name = str(command or "").strip().upper()
    if name not in ALLOWED_COMMANDS:
        raise ValueError(f"Unknown command. Allowed: {', '.join(ALLOWED_COMMANDS)}.")
    if name == "AUTH":
        if str(value).strip().lower() not in ("0", "1", "true", "false"):
            raise ValueError("AUTH needs the value 0 or 1.")
        return name, 1 if str(value).strip().lower() in ("1", "true") else 0
    if name == "LIMIT":
        limit = safe_number(value, default=float("nan"))
        if not math.isfinite(limit) or not 0 <= limit <= 300:
            raise ValueError("LIMIT needs a speed between 0 and 300 km/h.")
        return name, limit
    return name, None


# Application State
class SystemState:
    def __init__(self):
        self.mode = "waiting"     # "waiting" until the first real reading, then "serial" (ESP32 by USB or Wi-Fi)
        self.laptop_location = None   # {"lat", "lon", "accuracy_m", "epoch"} sent by the dashboard page
        self.active_driver_name = "Unknown"
        self.active_driver_auth = 0
        self.latest_telemetry = None
        self.current_zone_code = "NONE"
        self.current_zone_name = "None"
        self.active_alerts = {}  # alert_type: timestamp
        self.last_logged_alerts = {}  # alert_type: timestamp
        self.zone_speed_limit_applied = None
        self.failed_logins = []   # times of the failed face logins in a row (see UNAUTH_LOGIN_SMS_AFTER)
        self.last_telemetry_saved = 0.0

state = SystemState()

# Initialize AI Face Recognition Singletons (Arsal Subsystem)
face_detector = FaceDetector()
face_recognizer = FaceRecognizer()
face_sface = SFaceRecognizer()   # pretrained model, drivers enrolled live from the camera
database.sync_driver_samples(face_sface.enrolled())   # keep the driver table in step with the saved faces
liveness_detector = LivenessDetector()
challenges = ChallengeBook()
db_logger = DatabaseLogger()


def mark_live():
    """The first real reading switches the dashboard from "waiting" to live hardware data."""
    if state.mode != "serial":
        state.mode = "serial"
        socketio.emit("status", {"mode": state.mode, "driver": state.active_driver_name,
                                 "serial_connected": serial_mgr.is_connected()})
        logger.info("Hardware data received: live mode")


def on_serial_telemetry_received(packet: dict):
    """Callback when raw telemetry arrives from ESP32 hardware over the USB cable."""
    mark_live()
    process_telemetry_packet(packet, source="ESP32_SERIAL")


serial_mgr = SerialManager(
    port=config.DEFAULT_SERIAL_PORT,
    baudrate=config.DEFAULT_SERIAL_BAUD,
    on_telemetry_callback=on_serial_telemetry_received
)

# Prasanth's danger-zone engine + zone manager (backend/), mounted at /zm
zone_conn = zone_db.connect()  # backend/data/accialert_zones.db (zones + zone events)
zone_db.seed_zones_from_json(zone_conn, os.path.join(os.path.dirname(os.path.abspath(__file__)), "zones.json"))
if not zone_db.get_meta(zone_conn, "tn_default_zones_loaded"):
    # Tamil Nadu accident zones: loaded once, so a zone you delete later does not come back on restart
    zone_db.seed_zones_from_json(zone_conn, os.path.join(os.path.dirname(os.path.abspath(__file__)), "zones_tamilnadu.json"))
    zone_db.set_meta(zone_conn, "tn_default_zones_loaded", "1")
if not zone_db.get_meta(zone_conn, "chennai_blackspots_loaded"):
    # Named Chennai accident spots from public reports (see docs/ZONE_SOURCES.md): loaded once as well
    zone_db.seed_zones_from_json(zone_conn, os.path.join(os.path.dirname(os.path.abspath(__file__)), "zones_chennai.json"))
    zone_db.set_meta(zone_conn, "chennai_blackspots_loaded", "1")
zone_engine = ZoneEngine(zone_db.list_zones(zone_conn))


def zone_alert_distance() -> int:
    """How close (metres) the car must be to a danger zone for the notification. Chosen in Danger Zone Management."""
    try:
        value = int(float(zone_db.get_meta(zone_conn, "zone_alert_distance_m")))
    except (TypeError, ValueError):
        return config.ZONE_ALERT_DEFAULT_M
    return value if config.ZONE_ALERT_MIN_M <= value <= config.ZONE_ALERT_MAX_M else config.ZONE_ALERT_DEFAULT_M


zone_engine.prewarn_m = float(zone_alert_distance())       # the "approaching" event fires at that distance
mount_zone_manager(app, zone_conn, zone_engine, on_zones_changed=lambda *_: zones_changed(), prefix="/zm")
zone_db.start_purge_scheduler()


# ---------------------------------------------------------------- one zone list
# The zone manager's database is the only list of danger zones. The dashboard (map, zone tab),
# and the zone engine read it. The old dashboard table is imported once.
ZONE_TYPE_LABELS = {
    "blackspot": "Accident black spot", "curve": "Sharp curve", "school": "School", "hospital": "Hospital",
    "construction": "Construction", "restricted": "Restricted / no-entry",
}


def guess_zone_type(name: str) -> str:
    text = name.lower()
    for word, zone_type in (("school", "school"), ("curve", "curve"), ("hospital", "hospital"),
                            ("construction", "construction"), ("no-entry", "restricted"), ("restricted", "restricted")):
        if word in text:
            return zone_type
    return "blackspot"


def zone_for_dashboard(z: dict) -> dict:
    """A zone in the shape the dashboard expects (numeric id, code, latitude, longitude, ...)."""
    return {
        "id": z["num"], "code": z["id"], "name": z["name"],
        "latitude": z["lat"], "longitude": z["lon"], "radius_meters": z["radius_m"],
        "speed_limit": z["speed_limit"], "severity": str(z["severity"]).upper(),
        "details": ZONE_TYPE_LABELS.get(z["type"], z["type"]), "created_at": "",
    }


dashboard_zone_cache = []


def refresh_zone_cache():
    """Reload the zones used by the dashboard (only active zones are in force)."""
    dashboard_zone_cache[:] = [zone_for_dashboard(z) for z in zone_db.list_zone_rows(zone_conn, active_only=True)]


def zones_for_device() -> dict:
    """The active zones in the short form the ESP32 receives (command ZONES). "version" changes whenever a zone does,
    so the ESP32 can ignore a list it already has."""
    zones = []
    for z in zone_db.list_zones(zone_conn, active_only=True)[:config.DEVICE_MAX_ZONES]:
        zones.append({"id": z["id"], "name": str(z["name"])[:24], "lat": round(float(z["lat"]), 6), "lon": round(float(z["lon"]), 6),
                      "r": round(float(z["radius_m"]), 1), "limit": z.get("speed_limit"), "sev": z.get("severity")})
    version = hashlib.sha1(json.dumps(zones, sort_keys=True).encode()).hexdigest()[:8]
    return {"version": version, "count": len(zones), "zones": zones}


def send_zones_to_devices():
    """Queue the current zone list for every paired ESP32 (a newer list replaces one still waiting)."""
    try:
        device_registry.enqueue_command("ZONES", zones_for_device())
    except Exception:
        logger.exception("Could not queue the zone list for the ESP32")


def zones_changed(*_):
    """Called after any change to the zones, from the zone manager or from the dashboard's zone tab."""
    zone_engine.set_zones(zone_db.list_zones(zone_conn))
    refresh_zone_cache()
    socketio.emit("zones_updated", {})
    send_zones_to_devices()


def import_dashboard_zones_once():
    """The dashboard used to keep its own zones. Add them to the zone manager's list one time (as Z07, Z08, ...)."""
    if zone_db.get_meta(zone_conn, "dashboard_zones_imported"):
        return
    known = {z["name"].lower() for z in zone_db.list_zones(zone_conn)}
    for old in database.get_all_zones():
        if old["name"].lower() in known:
            continue
        try:
            zone_db.upsert_zone(zone_conn, {
                "id": zone_db.next_zone_id(zone_conn), "name": old["name"], "type": guess_zone_type(old["name"]),
                "lat": old["latitude"], "lon": old["longitude"], "radius_m": old["radius_meters"],
                "speed_limit": old["speed_limit"], "severity": str(old["severity"]).lower(), "active": 1,
            })
        except ValueError as err:
            logger.warning(f"Dashboard zone '{old['name']}' was not imported: {err}")
    zone_db.set_meta(zone_conn, "dashboard_zones_imported", "1")
    zone_engine.set_zones(zone_db.list_zones(zone_conn))


import_dashboard_zones_once()
refresh_zone_cache()


def on_device_reading(packet: dict):
    """A valid reading arrived from the ESP32 over Wi-Fi: show it on the dashboard.
    Returns the names of the active alerts."""
    mark_live()
    process_telemetry_packet(packet, source="ESP32_WIFI")
    return sorted(state.active_alerts)


def vehicle_state_for_device() -> dict:
    """What the ESP32 must follow whenever it asks. It starts locked until the server says AUTH 1."""
    return {"auth": 1 if state.active_driver_auth else 0}


# ESP32 Wi-Fi API: /api/v1/device/register, /api/v1/telemetry, /api/v1/device/commands (+ /ack)
device_registry.ensure_default_device()
device_registry.purge_old_commands()
app.register_blueprint(create_device_blueprint(on_device_reading, vehicle_state_for_device, on_register=send_zones_to_devices))
send_zones_to_devices()      # a restarted server hands the current list to ESP32s that are already paired
get_api_key()  # makes sure a key exists (creates backend/data/device_api_key.txt on first start)


# Telemetry processing & alert logic

def evaluate_and_dispatch_alerts(telemetry: dict, now: Optional[float] = None):
    """
    Examine sensor values against safety thresholds, manage active alerts,
    log critical events into SQLite, and emit alerts via SocketIO.
    """
    now_ts = time.time() if now is None else now
    current_alerts = []

    spd = safe_number(telemetry.get("spd"), 0.0)
    alc = safe_number(telemetry.get("alc"), 0, as_int=True)
    dist = safe_number(telemetry.get("dist"), 999.0)
    ax = safe_number(telemetry.get("ax"), 0.0)
    ay = safe_number(telemetry.get("ay"), 0.0)
    az = safe_number(telemetry.get("az"), 1.0)
    tilt = safe_number(telemetry.get("tilt"), 0.0)
    auth = safe_number(telemetry.get("auth"), 1, as_int=True)
    zone_code = str(telemetry.get("zone", "NONE"))
    evt_field = str(telemetry.get("evt", "")).upper()
    lat = safe_number(telemetry.get("lat"), None)    # None: no GPS and no laptop location
    lon = safe_number(telemetry.get("lon"), None)

    # Calculate 3D acceleration vector magnitude
    accel_magnitude = math.sqrt(ax * ax + ay * ay + az * az)

    # 1. ACCIDENT Check
    is_accident = (
        (tilt >= config.THRESHOLDS["tilt"]["accident"] and bool(telemetry.get("tilt_violent", True))) or
        accel_magnitude >= config.THRESHOLDS["impact_acceleration"] or
        "ACCIDENT" in evt_field
    )
    if is_accident:
        current_alerts.append({
            "type": "ACCIDENT",
            "severity": "CRITICAL",
            "title": "ACCIDENT DETECTED!",
            "message": f"Critical impact or rollover detected! Tilt: {tilt}°, Impact: {accel_magnitude:.2f}g",
            "sound": "accident"
        })

    # 2. ALCOHOL Check
    is_alcohol = (
        alc >= config.THRESHOLDS["alcohol"]["danger"] or
        "ALCOHOL" in evt_field
    )
    if is_alcohol:
        current_alerts.append({
            "type": "ALCOHOL DETECTED",
            "severity": "CRITICAL",
            "title": "DRIVER IMPAIRMENT DETECTED",
            "message": f"Alcohol sensor triggered! Level: {alc} ADC (Threshold: {config.THRESHOLDS['alcohol']['danger']})",
            "sound": "alarm"
        })

    # 3. OBSTACLE Check
    no_echo = bool(telemetry.get("no_echo")) and config.CRASH_ON_NO_ECHO     # Dist: none (sent as 500): see CRASH_ON_NO_ECHO
    is_crash = dist < config.THRESHOLDS["obstacle"]["crash"] or no_echo      # the card shows 4 cm and less (it rounds), so 4.4 counts
    is_obstacle = (
        dist <= config.THRESHOLDS["obstacle"]["danger"] or
        "OBSTACLE" in evt_field
    )
    if is_obstacle and not is_crash:                              # a crash replaces the plain obstacle message
        current_alerts.append({
            "type": "OBSTACLE DETECTED",
            "severity": "CRITICAL",
            "title": "CRITICAL PROXIMITY ALERT",
            "message": f"Obstacle within danger zone: {dist:.1f} cm! Collision imminent.",
            "sound": "alarm"
        })

    # 3b. CAR CRASHED: the obstacle is 4 cm away or closer (shown rounded), so the car has hit something (also texted)
    if is_crash:
        current_alerts.append({
            "type": "CAR CRASHED",
            "severity": "CRITICAL",
            "title": "CAR CRASHED",
            "message": ("The car has crashed: the distance sensor has no echo (reading above 400 cm / none)." if no_echo
                        else f"The car has crashed: the obstacle is only {dist:.1f} cm away."),
            "sound": "accident"
        })

    # 4. OVERSPEED Check
    is_overspeed = (
        spd > config.THRESHOLDS["speed"]["critical"] or
        "OVERSPEED" in evt_field
    )
    if is_overspeed:
        current_alerts.append({
            "type": "OVERSPEED",
            "severity": "WARNING",
            "title": "EXCESSIVE SPEED ALERT",
            "message": f"Speed is {spd:.1f} km/h, exceeding the safety threshold ({config.THRESHOLDS['speed']['critical']} km/h).",
            "sound": "warning"
        })

    # 5. DANGER ZONE ENTRANCE & SPEED RESTRICTION
    # Without a GPS fix the position is unreliable, so skip the zone check and keep the last zone state.
    gps_fix = safe_number(telemetry.get("fix"), 1, as_int=True) and lat is not None and lon is not None
    zone_match, zone_dist = (find_active_danger_zone(lat, lon, dashboard_zone_cache)
                             if gps_fix else (None, None))
    if zone_match:
        state.current_zone_code = zone_match["code"]
        state.current_zone_name = zone_match["name"]

        # Check speed limit violation in zone
        zone_limit = float(zone_match["speed_limit"])
        if spd > zone_limit:
            current_alerts.append({
                "type": "DANGER ZONE SPEEDING",
                "severity": "CRITICAL",
                "title": f"SPEED LIMIT VIOLATION IN {zone_match['name'].upper()}",
                "message": f"Vehicle speed {spd:.1f} km/h exceeds zone limit of {zone_limit:.0f} km/h! Auto-governor engaging.",
                "sound": "alarm"
            })
        else:
            current_alerts.append({
                "type": "DANGER ZONE",
                "severity": zone_match.get("severity", "MEDIUM"),
                "title": f"INSIDE DANGER ZONE: {zone_match['name']}",
                "message": f"Caution: Zone limit is {zone_limit:.0f} km/h. Distance to center: {zone_dist:.0f}m.",
                "sound": "warning"
            })

        # Send speed limit down to vehicle if not applied
        if state.zone_speed_limit_applied != zone_limit:
            send_unified_command("LIMIT", zone_limit, source="ZONE_ENGINE")
            state.zone_speed_limit_applied = zone_limit
    elif gps_fix:
        state.current_zone_code = "NONE"
        state.current_zone_name = "None"
        if state.zone_speed_limit_applied is not None:
            # Lift zone limit
            send_unified_command("LIMIT", 0, source="ZONE_ENGINE")
            state.zone_speed_limit_applied = None

    # 6. UNAUTHORISED DRIVER Check: the car is MOVING with no authorised driver. A locked car that stands still
    # (nobody has logged in yet, or the admin panel is open) is not "operated", so it raises no alert.
    if (auth == 0 or state.active_driver_auth == 0) and spd > config.UNAUTH_DRIVER_MIN_SPEED_KMH:
        current_alerts.append({
            "type": "UNAUTHORISED DRIVER",
            "severity": "CRITICAL",
            "title": "SECURITY BREACH",
            "message": f"The car is moving ({spd:.0f} km/h) with no authorised driver ({state.active_driver_name})!",
            "sound": "alarm"
        })

    # Dispatch alerts and keep the event log short
    new_active_types = set()
    for alert in current_alerts:
        a_type = alert["type"]
        new_active_types.add(a_type)

        # Log an alert when it starts, and again after a minute if it is still going on.
        # (Before, a condition that lasted logged a new row every 5 seconds.)
        last_logged = state.last_logged_alerts.get(a_type, 0.0)
        just_started = a_type not in state.active_alerts
        if (just_started and (now_ts - last_logged) > 5.0) or (now_ts - last_logged) > 60.0:
            database.add_event(
                event_type=a_type,
                driver_name=state.active_driver_name,
                vehicle_speed=spd,
                latitude=lat,
                longitude=lon,
                danger_zone_name=state.current_zone_name,
                details=alert["message"],
                severity=alert["severity"],
            )
            state.last_logged_alerts[a_type] = now_ts

        # Text the owner when a serious alert starts (limits and settings are in sms_alert.py).
        # An UNAUTHORISED DRIVER alert is only texted when the car is really moving: a locked car that is
        # only switched on is not an emergency.
        if just_started and a_type in config.SMS_ALERT_TYPES and (a_type != "UNAUTHORISED DRIVER" or spd > config.UNAUTH_DRIVER_MIN_SPEED_KMH):
            sms_alert.notify(a_type, {"driver": state.active_driver_name, "speed": spd, "lat": lat, "lon": lon,
                                      "source": telemetry.get("position_source")})

        # Emit live alert via SocketIO
        socketio.emit("alert", alert)

    # Prune alerts that cleared
    state.active_alerts = {k: v for k, v in state.active_alerts.items() if k in new_active_types}
    for a in current_alerts:
        state.active_alerts[a["type"]] = now_ts


def fresh_laptop_location():
    """The position the dashboard page last sent, if it is recent enough to use."""
    loc = state.laptop_location
    if loc and time.time() - loc["epoch"] <= config.LAPTOP_LOCATION_MAX_AGE_S:
        return loc
    return None


def choose_position(packet: dict):
    """Where the car is: the GPS module first. With no GPS fix, the laptop's own location is shown instead.
    Returns (lat, lon, fix, source, accuracy_m). A laptop location counts as a fix for the danger zones
    only when it is accurate enough, because a Wi-Fi or IP based location can be off by hundreds of metres."""
    nan = float("nan")
    lat = safe_number(packet.get("lat"), nan)
    lon = safe_number(packet.get("lon"), nan)
    module_fix = safe_number(packet.get("fix"), 1, as_int=True)
    usable = (module_fix and math.isfinite(lat) and math.isfinite(lon)
              and -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0))
    if usable:
        return lat, lon, 1, "GPS", None
    laptop = fresh_laptop_location()
    if laptop:
        good = laptop["accuracy_m"] is None or laptop["accuracy_m"] <= config.LAPTOP_ZONE_MAX_ACCURACY_M
        return laptop["lat"], laptop["lon"], 1 if good else 0, "LAPTOP", laptop["accuracy_m"]
    return None, None, 0, "NONE", None


def derive_vehicle_state(reported, spd) -> str:
    """Status for the dashboard card. Any active alert shows WARNING (an accident shows EMERGENCY),
    a car that is really moving shows MOVING, otherwise RUNNING (engine on, standing still)."""
    reported = str(reported or "").upper()
    if reported == "EMERGENCY" or "ACCIDENT" in state.active_alerts:
        return "EMERGENCY"
    if reported == "LOCKED":
        return "LOCKED"
    if reported == "WARNING" or state.active_alerts:
        return "WARNING"
    if safe_number(spd, 0.0) >= 0.5:
        return "MOVING"
    return reported if reported == "READY" else "RUNNING"


def process_telemetry_packet(packet: dict, source: str = "ESP32"):
    """Standardizes, decorates, and broadcasts a telemetry packet.
    A missing or non-numeric field falls back to a safe default instead of dropping the packet."""
    if not isinstance(packet, dict):
        logger.warning("Ignored a telemetry packet that is not a JSON object")
        return
    lat, lon, fix, position_source, accuracy = choose_position(packet)
    telemetry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": time.time(),
        "source": source,
        "spd": round(safe_number(packet.get("spd"), 0.0), 1),
        "alc": safe_number(packet.get("alc"), 0, as_int=True),
        "dist": round(safe_number(packet.get("dist"), 150.0), 1),
        "ax": round(safe_number(packet.get("ax"), 0.0), 2),
        "ay": round(safe_number(packet.get("ay"), 0.0), 2),
        "az": round(safe_number(packet.get("az"), 1.0), 2),
        "tilt": round(safe_number(packet.get("tilt"), 0.0), 1),
        "no_echo": bool(packet.get("no_echo", False)),             # the distance sensor gave no echo (ESP32 over Wi-Fi)
        "tilt_violent": bool(packet.get("tilt_violent", True)),   # False: only turned slowly (a hand), not knocked or spun
        "lat": lat,
        "lon": lon,
        "fix": fix,
        "position_source": position_source,        # "GPS" (the module), "LAPTOP" (this computer) or "NONE"
        "position_accuracy_m": accuracy,           # only for the laptop location
        "state": str(packet.get("state", "RUNNING")),
        "auth": safe_number(packet.get("auth"), state.active_driver_auth, as_int=True),
        "zone": str(packet.get("zone", state.current_zone_code)),
        "evt": str(packet.get("evt", "NONE")),
        "driver": state.active_driver_name,
        "zone_name": state.current_zone_name
    }

    state.latest_telemetry = telemetry

    # Run alert detection logic
    evaluate_and_dispatch_alerts(telemetry)

    # Vehicle status shown on the dashboard: EMERGENCY / LOCKED / WARNING / MOVING / RUNNING
    telemetry["state"] = derive_vehicle_state(telemetry["state"], telemetry["spd"])

    # Broadcast over Socket.IO to connected browsers
    socketio.emit("telemetry", telemetry)

    # Save one telemetry row per second (the ESP32 sends about 5 per second)
    if telemetry["epoch"] - state.last_telemetry_saved >= 1.0:
        state.last_telemetry_saved = telemetry["epoch"]
        try:
            zone_db.log_telemetry(zone_conn, speed=telemetry["spd"], alcohol=telemetry["alc"],
                                  distance=telemetry["dist"], tilt=telemetry["tilt"],
                                  lat=telemetry["lat"], lon=telemetry["lon"],
                                  state=telemetry["state"], zone_id=telemetry["zone"],
                                  ax=telemetry["ax"], ay=telemetry["ay"], az=telemetry["az"])
        except Exception as err:
            logger.warning(f"Telemetry could not be saved: {err}")

    # Danger-zone engine: zone enter / exit / pre-warning / overspeed events
    try:
        for ev in zone_engine.update(telemetry["lat"], telemetry["lon"], telemetry["spd"], telemetry["fix"] if telemetry["lat"] is not None else 0):
            zone_db.log_event(zone_conn, ev["type"], ev["severity"], driver=state.active_driver_name,
                              speed=ev["speed"], lat=ev["lat"], lon=ev["lon"],
                              zone_id=ev["zone_id"], details=ev["details"])
            if ev["type"] in ("ZONE_ENTER", "ZONE_EXIT", "OVERSPEED_IN_ZONE"):   # shown in the dashboard event table
                database.add_event(
                    event_type=ev["type"],
                    driver_name=state.active_driver_name,
                    vehicle_speed=ev["speed"],
                    latitude=ev["lat"],
                    longitude=ev["lon"],
                    danger_zone_name=ev["zone_name"],
                    details=ev["details"],
                )
            socketio.emit("zone_event", ev)
    except Exception as err:
        logger.warning(f"Zone engine step failed: {err}")


def vehicle_position():
    """Speed, latitude and longitude of the car right now, from the last real reading.
    The position is None when there is no GPS fix and no laptop location, and before any reading."""
    t = state.latest_telemetry
    if t:
        return t["spd"], t["lat"], t["lon"]
    return 0.0, None, None


def send_unified_command(command: str, value=None, source: str = "UI"):
    """
    Unified command dispatcher.
    Sends the command to the ESP32 over the USB cable (when connected) and queues it for the Wi-Fi ESP32.
    """
    logger.info(f"Command Dispatched [{source} -> {state.mode}]: {command} = {value}")
    if str(command).upper() == "AUTH":
        # The lock state the alerts read must follow every lock or unlock, not only a face scan.
        state.active_driver_auth = 1 if safe_number(value, 0, as_int=True) else 0
    if str(command).upper() == "RESET":
        reset_tilt_reference()        # RESET SYSTEM: the car as it stands now is the new "upright"
    database.log_command(f"{command}:{value}" if value is not None else command, source=source)

    # 1. USB serial, if connected
    if serial_mgr.is_connected():
        serial_mgr.send_command(command, value)

    # 2. Queue it for the ESP32 that connects over Wi-Fi (it collects it with GET /api/v1/device/commands)
    try:
        device_registry.enqueue_command(command, value)
    except Exception:
        logger.exception("Could not queue %s for the ESP32", command)

    # Broadcast command ack to frontend
    socketio.emit("command_executed", {
        "command": command,
        "value": value,
        "mode": state.mode,
        "source": source
    })
    return True


# Static assets & frontend pages

def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/")
def login_page():
    """First page: the camera opens by itself and the driver's face is checked."""
    return no_cache(send_from_directory(FRONTEND_DIR, "login.html"))


@app.route("/home")
def dashboard_home():
    """The dashboard. Only open after a successful face login, otherwise back to the first page."""
    if not state.active_driver_auth:
        return redirect("/")
    return no_cache(send_from_directory(FRONTEND_DIR, "index.html"))


@app.route("/admin-login")
def admin_login_page():
    return no_cache(send_from_directory(FRONTEND_DIR, "admin_login.html"))


def admin_logged_in() -> bool:
    return (not config.ADMIN_AUTH_REQUIRED) or admin_auth.is_valid(request.cookies.get("admin_session"))


@app.route("/admin")
def admin_panel():
    """Admin panel: enrol drivers, danger zones, controls, event history. Password needed."""
    if not admin_logged_in():
        return redirect("/admin-login")
    return no_cache(send_from_directory(FRONTEND_DIR, "index.html"))


@app.route("/api/admin/login", methods=["POST"])
def admin_login_api():
    password = (request.get_json(silent=True) or {}).get("password")
    token, problem, status = admin_auth.login(password, request.remote_addr or "?")
    if problem:
        return jsonify({"status": "error", "message": problem}), status
    response = jsonify({"status": "success"})
    response.set_cookie("admin_session", token, max_age=config.ADMIN_SESSION_TTL_S, httponly=True, samesite="Strict")
    return response


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout_api():
    admin_auth.logout(request.cookies.get("admin_session"))
    response = jsonify({"status": "success"})
    response.delete_cookie("admin_session")
    return response


@app.route("/api/admin/status", methods=["GET"])
def admin_status_api():
    return jsonify({"status": "success", "admin": admin_logged_in()})


# These changes need the admin login (reading is open: the dashboard shows zones and events)
ADMIN_ONLY_WRITES = ("/api/zone-settings", "/api/enrol", "/api/enrolled", "/api/drivers", "/api/zones", "/zm/api/zones", "/api/serial/connect",
                     "/api/serial/send", "/api/logs", "/api/config", "/api/sms/enabled", "/api/sms/test")


@app.before_request
def require_admin_for_changes():
    if not config.ADMIN_AUTH_REQUIRED or request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    path = request.path
    protected = any(path == p or path.startswith(p + "/") for p in ADMIN_ONLY_WRITES)
    if path == "/api/command":
        body = request.get_json(silent=True) or {}
        protected = str(body.get("command", "")).upper() in ("RESET", "STOP")
    if protected and not admin_logged_in():
        return jsonify({"status": "error", "message": "Admin login required."}), 401
    return None


@app.route("/api/face-present", methods=["POST"])
def face_present_api():
    """Is exactly one face in this picture? Used by the first page to know when to start the login check.
    It changes nothing: no lock, no log."""
    frame_data = (request.get_json(silent=True) or {}).get("frame")
    try:
        frame = decode_frame(frame_data) if frame_data else None
    except Exception:
        frame = None
    if frame is None:
        return jsonify({"status": "error", "message": "frame data is required."}), 400
    # only the number of faces is needed: no face match (that is the slow part)
    face_count = len(face_sface._faces(frame)) if face_sface.models_ready else recognise(frame)[0]
    return jsonify({"status": "success", "faces": int(face_count)})


@app.route("/api/head-pose", methods=["POST"])
def head_pose_api():
    """One picture in, how many faces and how far the head is turned out (see headturn.py). Used by the login and
    enrolment pages to show the driver that a turn was seen. It changes nothing: no lock, no log."""
    frame_data = (request.get_json(silent=True) or {}).get("frame")
    try:
        frame = decode_frame(frame_data) if frame_data else None
    except Exception:
        frame = None
    if frame is None:
        return jsonify({"status": "error", "message": "frame data is required."}), 400
    if not face_sface.models_ready:
        return jsonify({"status": "error", "message": "SFace models are missing. Run: python -m backend.tools.get_models"}), 400
    faces = face_sface._faces(frame)
    yaw = headturn.yaw_ratio(faces[0]) if len(faces) == 1 else None
    return jsonify({"status": "success", "faces": len(faces), "yaw": None if yaw is None else round(yaw, 3)})


@app.route("/api/sos", methods=["POST"])
def sos_api():
    """The SOS button on the dashboard: record the emergency and text the owner with the car's position."""
    speed, lat, lon = vehicle_position()
    driver = state.active_driver_name if state.active_driver_auth else "Unknown"
    database.add_event("SOS", driver, speed, lat, lon, None, "SOS button pressed on the dashboard", severity="CRITICAL")
    texted = sms_alert.notify("SOS", {"speed": speed, "lat": lat, "lon": lon, "driver": driver,
                                      "source": (state.latest_telemetry or {}).get("position_source")})
    socketio.emit("sos", {"driver": driver, "lat": lat, "lon": lon})
    message = "SOS sent. The owner is being texted." if texted else "SOS recorded. The text was not sent (SMS is switched off or not set up, or a limit was reached)."
    return jsonify({"status": "success", "texted": bool(texted), "message": message})


@app.route("/dashboard")
def dashboard_view():
    """Fleet dashboard page."""
    return send_from_directory(FRONTEND_DIR, "dashboard.html")


@app.route("/zones.html")
def zones_page():
    """Serves the Zone Manager page."""
    return send_from_directory(FRONTEND_DIR, "zones.html")


@app.route("/zones.css")
def zones_css():
    return send_from_directory(FRONTEND_DIR, "zones.css")


@app.route("/zones.js")
def zones_js():
    return send_from_directory(FRONTEND_DIR, "zones.js")


@app.route("/static/<path:filename>")
def serve_static(filename):
    # no-cache: the browser asks again every time, so a changed script is never served from an old copy
    response = send_from_directory(os.path.join(FRONTEND_DIR, "static"), filename)
    response.headers["Cache-Control"] = "no-cache"
    return response


# AI FACE RECOGNITION & DRIVER AUTHORISATION APIS (Arsal)

@app.route("/api/health", methods=["GET"])
def health_check_api():
    """Service health and diagnostics check."""
    return jsonify({
        "status": "ok",
        "app": "AcciAlert",
        "version": "3.0.0-unified",
        "port": config.PORT,
        "mode": state.mode,
        "position_source": state.latest_telemetry.get("position_source") if state.latest_telemetry else None,
        "active_driver": state.active_driver_name,
        "serial_connected": serial_mgr.is_connected()
    })


def use_sface() -> bool:
    """Use the SFace recogniser whenever its models are downloaded."""
    return config.DEFAULT_FACE_ENGINE in ("auto", "sface") and face_sface.models_ready


def decode_frame(data: str):
    """Base64 image (with or without the data: prefix) -> OpenCV image, or None."""
    if "," in data:
        data = data.split(",", 1)[1]
    buf = np.frombuffer(base64.b64decode(data), np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def roster_entries():
    """Every driver the face login knows, most recently enrolled first. The details come from the database."""
    known = set(face_sface.enrolled()) | set(face_recognizer.labels_map.values())
    records = {d["name"]: d for d in database.get_driver_records()}
    entries = []
    for name in known:
        row = records.get(name, {})
        entries.append({
            "name": name,
            "samples": row.get("face_samples") or 0,
            "license": row.get("license_number") or "",
            "enrolled_at": row.get("last_enrolled_at") or "",
            "images": row.get("photos_saved") or 0,
            "folder": row.get("photo_folder") or f"backend/dataset/{name}",
        })
    entries.sort(key=lambda e: (e["enrolled_at"], e["name"]), reverse=True)   # no date -> last
    return entries


@app.route("/api/enrolled", methods=["GET"])
def enrolled_drivers_api():
    """Drivers the face login knows, newest enrolment first."""
    return jsonify({
        "engine": "sface" if use_sface() else "lbph",
        "models_ready": face_sface.models_ready,
        "drivers": roster_entries(),
    })


@app.route("/api/enrolled/<name>", methods=["GET"])
def enrolled_driver_details_api(name):
    """One driver's details and full enrolment history."""
    entry = next((e for e in roster_entries() if e["name"] == name), None)
    if entry is None:
        return jsonify({"status": "error", "message": "Driver not found."}), 404
    entry["history"] = database.get_driver_history(name)
    return jsonify(entry)


@app.route("/api/enrolled/<name>", methods=["DELETE"])
def delete_enrolled_driver_api(name):
    """Admin only: delete a driver. The face data, the driver record, the enrolment history and the saved photos
    are removed, so that person can no longer start the car. The event log keeps its history."""
    entry = next((e for e in roster_entries() if e["name"] == name), None)
    if entry is None:
        return jsonify({"status": "error", "message": "Driver not found."}), 404
    samples = face_sface.forget(name)
    face_recognizer.forget(name)
    database.delete_driver(name)

    photos = 0
    folder = (config.DATASET_DIR / name)
    try:       # only a folder directly inside the photo folder, never anything else
        if name and "/" not in name and "\\" not in name and not name.startswith(".") and folder.is_dir()                 and folder.resolve().parent == Path(config.DATASET_DIR).resolve():
            photos = sum(1 for f in folder.iterdir() if f.is_file())
            shutil.rmtree(folder)
    except OSError:
        logger.exception("Could not delete the photos of %s", name)

    if state.active_driver_name == name and state.active_driver_auth:       # the car was unlocked by this driver: lock it
        state.active_driver_auth = 0
        state.active_driver_name = "Unknown"
        state.last_face_result = (False, "Unknown")
        send_unified_command("AUTH", 0, source="DRIVER_DELETED")
    database.add_event("DRIVER_DELETED", name, 0.0, None, None, None,
                       f"Driver deleted by the admin: {samples} face samples and {photos} photos removed", severity="MEDIUM")
    return jsonify({"status": "success", "message": f"{name} was deleted.", "face_samples": samples, "photos": photos})


@app.route("/api/enrol", methods=["POST"])
def enrol_driver_api():
    """Enrol a driver from live camera frames: {"name": "Arun", "frames": ["data:image/jpeg;base64,...", ...]}."""
    payload = request.get_json(silent=True) or {}
    frames = payload.get("frames")
    if not payload.get("name") or not isinstance(frames, list) or not frames:
        return jsonify({"status": "error", "message": "name and frames are required."}), 400
    if len(frames) > 200:
        return jsonify({"status": "error", "message": "Send at most 200 frames."}), 400
    license_number = re.sub(r"[^A-Za-z0-9 /-]", "", str(payload.get("license") or "")).strip()[:30]
    if not license_number:
        return jsonify({"status": "error", "message": "Enter the driver's license number."}), 400
    if config.ENROL_REQUIRE_TURN:
        if len(frames) > AUTH_MAX_FRAMES:
            return jsonify({"status": "error", "message": f"Send at most {AUTH_MAX_FRAMES} frames."}), 400
        if not challenges.take(payload.get("challenge")):
            return jsonify({"status": "error", "message": "The head-turn request expired. Try again."}), 400
    try:
        images = [decode_frame(f) for f in frames]
        name = face_sface.clean_name(payload["name"])
        owner = next((d["name"] for d in database.get_all_drivers()
                      if d.get("license_number") == license_number and d["name"] != name), None)
        if owner:
            return jsonify({"status": "error", "message": f"License number {license_number} already belongs to {owner}."}), 400
        if config.ENROL_REQUIRE_TURN:        # the same head turn as the login: right, left, straight (a photo cannot do it)
            levels = []
            for shot in images:
                faces = face_sface._faces(shot) if shot is not None and face_sface.models_ready else []
                levels.append(headturn.yaw_ratio(faces[0]) if len(faces) == 1 else None)
            turn = headturn.check_sequence(levels)
            if not turn["passed"]:
                return jsonify({"status": "error", "message": turn["message"] + " Nothing was saved."}), 400
        result = face_sface.enrol(name, [img for img in images if img is not None],
                                  image_dir=config.DATASET_DIR / name)
        result["folder"] = f"backend/dataset/{result['name']}"
    except (ValueError, RuntimeError) as err:
        return jsonify({"status": "error", "message": str(err)}), 400
    except Exception as err:
        logger.exception("Error in /api/enrol")
        return jsonify({"status": "error", "message": f"Enrolment failed: {str(err)[:120] or type(err).__name__}"}), 500

    database.save_driver_enrolment(result["name"], license_number, result["accepted"], result["total_samples"],
                                   result["images_saved"], result["folder"])
    result["license"] = license_number
    database.add_event("DRIVER_ENROLLED", result["name"], 0.0, None, None,
                       "None", f"Enrolled from live camera: {result['accepted']} frames, license {license_number}, photos in {result['folder']}")
    return jsonify({"status": "success", **result})


def recognise(frame):
    """Find the face in a frame and match it.
    Returns (face_count, box [x, y, w, h] in pixels or None, driver name, recognised, confidence %, note, eye openness).
    The last value is how far the head is turned (see headturn.py), None if unknown."""
    if use_sface():
        found = face_sface.identify(frame)
        if found["face_count"] == 0:
            return 0, None, "Unknown", False, 0.0, "No face found. Look at the camera."
        if found["face_count"] > 1:
            return found["face_count"], None, "Unknown", False, 0.0, "Only one person should be in view."
        yaw = headturn.yaw_ratio(found["face"]) if found.get("face") else None
        if not face_sface.gallery:
            return 1, found["box"], "Unknown", False, 0.0, "No driver is enrolled yet. Use Enrol Drivers.", yaw
        note = "" if found["authorized"] else "Face not recognised."
        return 1, found["box"], found["driver"], found["authorized"], max(0.0, found["score"]) * 100.0, note, yaw

    # Old LBPH model (used only when the SFace models are not downloaded)
    det = face_detector.detect_faces(frame)
    if det.face_count == 0 or det.primary_roi is None:
        return 0, None, "Unknown", False, 0.0, "No face found. Look at the camera."
    if det.face_count > 1:
        return det.face_count, None, "Unknown", False, 0.0, "Only one person should be in view."
    result = face_recognizer.recognize(det.primary_roi)
    box = [int(v) for v in det.primary_box]
    note = "" if result["authorized"] else "Face not recognised."
    return 1, box, str(result["driver"]), bool(result["authorized"]), float(result["confidence"]), note, None


@app.route("/api/verify", methods=["POST"])
def verify_face_api():
    """Check one camera frame: find the face, match it, check the person is live, then lock or unlock the car."""
    payload = request.get_json(silent=True) or {}
    frame_data = payload.get("frame") or payload.get("image")
    if not frame_data:
        return jsonify({"status": "error", "message": "frame data is required (base64 string)."}), 400

    try:
        frame = decode_frame(frame_data)
    except Exception:
        frame = None
    if frame is None:
        return jsonify({"status": "error", "message": "Invalid image format."}), 400

    try:
        frame_h, frame_w = frame.shape[:2]
        face_count, box, driver_name, recognised, confidence, note = recognise(frame)[:6]

        liveness = "NO_FACE" if face_count == 0 else "PENDING"
        live = True
        if face_count != 1:
            liveness_detector.forget_motion()
        elif recognised and payload.get("liveness_check", config.ENABLE_LIVENESS_BY_DEFAULT):
            live = liveness_detector.check_motion(box, frame_w, frame_h)
            liveness = "PASSED" if live else "PENDING"
            if not live:
                note = "Move your head slightly."
        elif recognised:
            liveness = "PASSED"

        # Estimate physical distance from camera to person using pinhole face geometry
        distance_cm = None
        if box and len(box) >= 4 and box[2] > 0:
            # Average adult face width ~14.5 cm, standard webcam FOV focal length F ~ 0.85 * frame_w
            calc_dist = (14.5 * 0.85 * float(frame_w)) / float(box[2])
            distance_cm = round(max(15.0, min(250.0, calc_dist)), 1)

        # Eye tracking & vitality analysis
        eyes_count = 0
        eye_status = "No face"
        if face_count == 1 and box:
            try:
                bx, by, bw, bh = [int(v) for v in box]
                face_crop = frame[max(0, by):min(frame_h, by + bh), max(0, bx):min(frame_w, bx + bw)]
                if face_crop.size > 0:
                    gray_crop = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY) if len(face_crop.shape) == 3 else face_crop
                    eyes = face_detector.detect_eyes_in_face(gray_crop)
                    eyes_count = len(eyes)
            except Exception:
                eyes_count = 0

            if liveness == "PASSED":
                eye_status = "PASSED"
            elif eyes_count >= 2:
                eye_status = "TRACKING (2 EYES)"
            elif eyes_count == 1:
                eye_status = "TRACKING (1 EYE)"
            else:
                eye_status = "BLINK DETECTED"

        unlocked = recognised and live
        shown_driver = driver_name if unlocked else "Unknown"
        if unlocked:
            note = f"Welcome, {driver_name}."

        # Lock or unlock and write to the log only when the result changes, not on every frame
        current = (unlocked, shown_driver)
        changed = current != getattr(state, "last_face_result", None)
        if changed:
            state.last_face_result = current
            state.active_driver_name = shown_driver
            state.active_driver_auth = 1 if unlocked else 0
            send_unified_command("AUTH", 1 if unlocked else 0, source="AI_FACIAL_RECOGNITION")
        # Pressing "Capture Image" is a login attempt, so it is always logged (even a repeated rejection).
        pressed_capture = bool(payload.get("capture"))
        if (changed or pressed_capture) and (unlocked or face_count == 1):
            speed_now, lat_now, lon_now = vehicle_position()
            database.add_event(
                event_type="AUTH_OK" if unlocked else "UNAUTHORISED DRIVER",
                driver_name=shown_driver,
                vehicle_speed=speed_now,
                latitude=lat_now,
                longitude=lon_now,
                details=f"Match {confidence:.0f}%, liveness {liveness.lower()}",
                severity="INFO" if unlocked else "HIGH",
            )

        dist_str = f"{int(round(distance_cm))} cm" if distance_cm else "--"
        dist_full_str = f"{int(round(distance_cm))} cm ({distance_cm/100.0:.2f} m)" if distance_cm else "--"

        return jsonify({
            "status": "success",
            "detected": face_count == 1,
            "face_count": face_count,
            "authorized": unlocked,
            "auth_status": "AUTHORIZED" if unlocked else ("UNAUTHORIZED" if face_count > 0 else "STANDBY"),
            "driver": shown_driver,
            "driver_name": shown_driver,
            "match_pct": round(confidence),
            "confidence": round(confidence, 1),
            "distance": round(100.0 - confidence, 2),
            "person_distance_cm": distance_cm,
            "person_distance_m": round(distance_cm / 100.0, 2) if distance_cm else None,
            "person_distance_str": dist_str,
            "person_distance_full": dist_full_str,
            "liveness": liveness,
            "eyes_count": eyes_count,
            "eye_tracking": eye_status,
            "ignition": "UNLOCKED" if unlocked else "LOCKED",
            "box": [box[0] / frame_w, box[1] / frame_h, box[2] / frame_w, box[3] / frame_h] if box else None,
            "message": note,
        })
    except Exception as e:
        logger.error(f"Error in /api/verify: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


AUTH_MIN_FRAMES = 3
AUTH_MAX_FRAMES = 60            # the head-turn clip is about 16 pictures
AUTH_MATCH_FRAMES = 6           # at most this many straight pictures go through the (slow) face match


@app.route("/api/auth/challenge", methods=["POST", "GET"])
def auth_challenge_api():
    """Step 1 of the face login (and of enrolment): the request "Turn your head right, then left, then look straight".
    Valid once, for a short time. The page shows it, takes pictures while the driver does it, and sends the token with
    /api/authenticate (or /api/enrol)."""
    return jsonify(dict(challenges.issue(), status="success"))


@app.route("/api/authenticate", methods=["POST"])
def authenticate_driver_api():
    """The driver login. The page sends a few frames taken over about two seconds (the driver turns the head a
    little). One decision is made from all of them: the same enrolled driver in most frames and a small movement
    (so a photo does not work). An authorised driver unlocks the car, anyone else leaves it locked.
    There is no continuous camera monitoring: the car is checked only when this is called."""
    payload = request.get_json(silent=True) or {}
    frames = payload.get("frames")
    if not isinstance(frames, list) or not (AUTH_MIN_FRAMES <= len(frames) <= AUTH_MAX_FRAMES):
        return jsonify({"status": "error", "message": f"Send {AUTH_MIN_FRAMES} to {AUTH_MAX_FRAMES} frames."}), 400
    started = time.time()
    # The movement request cannot be switched off by the page: anyone on the network could send "liveness_check": false
    use_liveness = True if config.AUTH_REQUIRE_CHALLENGE else payload.get("liveness_check", config.ENABLE_LIVENESS_BY_DEFAULT)

    try:
        liveness_detector.forget_motion()
        turn_check = bool(use_liveness and config.AUTH_REQUIRE_CHALLENGE)
        if turn_check and not challenges.take(payload.get("challenge")):
            # no car state is changed: this is not a face check at all
            return jsonify({"status": "error", "message": "The head-turn request expired. Try again."}), 400
        seen = []                  # one entry per frame that has exactly one face, in time order
        crowded = False
        moved = False
        # Fast way (head-turn login with the SFace models): every picture is only searched for the face and the head
        # turn (cheap). The slow face match runs afterwards on a few straight pictures only (see below).
        fast = bool(turn_check and use_sface() and face_sface.gallery)
        timing = {"decode": 0.0, "detect": 0.0, "match": 0.0}       # seconds, written to the server log (see below)
        for data in frames:
            step_started = time.perf_counter()
            try:
                frame = decode_frame(data)
            except Exception:
                frame = None
            timing["decode"] += time.perf_counter() - step_started
            if frame is None:
                continue
            frame_h, frame_w = frame.shape[:2]
            if fast:
                step_started = time.perf_counter()
                faces = face_sface._faces(frame)
                timing["detect"] += time.perf_counter() - step_started
                if len(faces) > 1:
                    crowded = True
                if len(faces) != 1:
                    continue
                row = [float(v) for v in faces[0]]
                seen.append({"driver": "Unknown", "recognised": False, "confidence": 0.0,
                             "box": [int(v) for v in row[:4]], "w": frame_w, "h": frame_h,
                             "yaw": headturn.yaw_ratio(row), "_frame": frame, "_face": faces[0]})
                continue
            result = recognise(frame)
            face_count, box, driver_name, recognised, confidence, _note = result[:6]
            yaw = result[6] if len(result) > 6 else None
            if face_count > 1:
                crowded = True
            if face_count != 1:
                continue
            seen.append({"driver": driver_name, "recognised": recognised, "confidence": confidence,
                         "box": box, "w": frame_w, "h": frame_h, "yaw": yaw})
            if recognised and use_liveness and not turn_check and liveness_detector.check_motion(box, frame_w, frame_h):
                moved = True
        turn = headturn.check_sequence([s["yaw"] for s in seen]) if turn_check else None
        if turn_check:
            moved = turn["passed"]

        # Who is it? Only the pictures where the head is (nearly) straight count: a turned face is harder to recognise.
        straight = seen
        if turn_check and turn["base"] is not None:
            straight = [s for s in seen if s["yaw"] is not None and abs(s["yaw"] - turn["base"]) <= config.HEAD_FRONTAL_MAX]
        if fast:
            # match only a few pictures, spread over the straight ones (the same rule as before: most of them must agree)
            if len(straight) > AUTH_MATCH_FRAMES:
                step = (len(straight) - 1) / (AUTH_MATCH_FRAMES - 1)
                straight = [straight[int(round(i * step))] for i in range(AUTH_MATCH_FRAMES)]
            match_started = time.perf_counter()
            for s in straight:
                name, score = face_sface._best_match(face_sface._signature(s["_frame"], s["_face"]))
                ok = score >= face_sface.threshold
                s["driver"], s["recognised"], s["confidence"] = (name if ok else "Unknown"), ok, max(0.0, score) * 100.0
            timing["match"] = time.perf_counter() - match_started
            for s in seen:
                s.pop("_frame", None)
                s.pop("_face", None)
        winner, votes = None, 0
        names = {}
        for s in straight:
            if s["recognised"]:
                names[s["driver"]] = names.get(s["driver"], 0) + 1
        if names:
            winner, votes = max(names.items(), key=lambda kv: kv[1])
        matched = bool(winner) and votes >= max(2, math.ceil(0.6 * len(straight)))
        live = moved or not use_liveness
        authorized = matched and live

        if authorized:
            note = f"Welcome, {winner}."
        elif not seen:
            note = "Only one person should be in view." if crowded else "No face found. Look at the camera."
        elif matched:
            note = turn["message"] if turn_check else "Turn your head slightly while the picture is taken."
        else:
            note = "Face not recognised."

        shown_driver = winner if authorized else "Unknown"
        used = [s for s in seen if s["recognised"] and s["driver"] == winner] if matched else seen
        confidence = (sum(s["confidence"] for s in used) / len(used)) if used else 0.0
        last = seen[-1] if seen else None
        distance_cm = None
        if last and last["box"] and last["box"][2] > 0:
            distance_cm = round(max(15.0, min(250.0, (14.5 * 0.85 * float(last["w"])) / float(last["box"][2]))), 1)
        liveness = "NO_FACE" if not seen else ("PASSED" if authorized else "PENDING")

        # The result is the lock state, so every attempt is sent to the car and written to the log
        state.last_face_result = (authorized, shown_driver)
        state.active_driver_name = shown_driver
        state.active_driver_auth = 1 if authorized else 0
        send_unified_command("AUTH", 1 if authorized else 0, source="AI_FACIAL_RECOGNITION")
        if authorized:
            state.failed_logins = []                      # a good login starts the count again
        elif seen:
            # Text the owner only after several failed attempts in a row (not on the first mistake): a driver whose
            # head turn was not seen, or a wrong face tried once or twice, does not cost a text message.
            now_login = time.time()
            recent = [t for t in state.failed_logins if now_login - t <= config.UNAUTH_LOGIN_WINDOW_S] + [now_login]
            state.failed_logins = recent
            if len(recent) >= config.UNAUTH_LOGIN_SMS_AFTER:
                state.failed_logins = []
                speed_now, lat_now, lon_now = vehicle_position()
                sms_alert.notify("UNAUTHORISED LOGIN", {"speed": speed_now, "lat": lat_now, "lon": lon_now,
                                                        "source": (state.latest_telemetry or {}).get("position_source")})
        if seen:
            speed_now, lat_now, lon_now = vehicle_position()
            database.add_event(
                event_type="AUTH_OK" if authorized else "UNAUTHORISED DRIVER",
                driver_name=shown_driver,
                vehicle_speed=speed_now, latitude=lat_now, longitude=lon_now,
                details=f"Match {confidence:.0f}%, liveness {liveness.lower()}" + (f", head turn {'ok' if turn['passed'] else 'not seen'}" if turn_check else ""),
                severity="INFO" if authorized else "HIGH",
            )

        total_ms = (time.time() - started) * 1000
        logger.info("Face login check: %d pictures sent, %d with one face | decode %.0f ms, face search %.0f ms, "
                    "face match %.0f ms | whole check %.0f ms | %s", len(frames), len(seen), timing["decode"] * 1000,
                    timing["detect"] * 1000, timing["match"] * 1000, total_ms, "UNLOCKED" if authorized else "stays locked")
        return jsonify({
            "status": "success",
            "authorized": authorized,
            "timing_ms": {"decode": round(timing["decode"] * 1000), "search": round(timing["detect"] * 1000),
                          "match": round(timing["match"] * 1000), "total": round(total_ms)},
            "auth_status": "AUTHORIZED" if authorized else ("UNAUTHORIZED" if seen else "STANDBY"),
            "driver": shown_driver,
            "driver_name": shown_driver,
            "ignition": "UNLOCKED" if authorized else "LOCKED",
            "detected": bool(seen),
            "face_count": 2 if crowded and not seen else (1 if seen else 0),
            "frames_used": len(seen),
            "frames_sent": len(frames),
            "match_pct": round(confidence),
            "confidence": round(confidence, 1),
            "liveness": liveness,
            "eye_tracking": "PASSED" if authorized else "--",
            "head_turn": ({"right": turn["right"], "left": turn["left"], "straight": turn["straight"]} if turn_check else None),
            "yaw_levels": [None if x["yaw"] is None else round(x["yaw"], 2) for x in seen] if turn_check else [],   # for tuning HEAD_* in config.py
            "eyes_count": 0,
            "person_distance_cm": distance_cm,
            "person_distance_m": round(distance_cm / 100.0, 2) if distance_cm else None,
            "person_distance_str": f"{int(round(distance_cm))} cm" if distance_cm else "--",
            "elapsed_ms": int((time.time() - started) * 1000),
            "message": note,
        })
    except Exception as e:
        logger.error(f"Error in /api/authenticate: {e}")
        try:        # when the check itself fails the car must not be left unlocked
            state.active_driver_auth = 0
            send_unified_command("AUTH", 0, source="AI_FACIAL_RECOGNITION")
        except Exception:
            logger.exception("Could not lock the car after a failed authentication")
        return jsonify({"status": "error", "message": "Authentication failed. The car is locked."}), 500


def _face_log_entry(e: dict) -> dict:
    """One audit-trail row for a face-login event. Match score and liveness come from the saved details."""
    details = e["details"] or ""
    ok = e["event_type"] == "AUTH_OK"
    score = re.search(r"Match\s+(\d+(?:\.\d+)?)\s*%", details)
    live = re.search(r"liveness\s+([A-Za-z]+)", details, re.IGNORECASE)
    driver = e["driver_name"] or "Unknown"
    shown_type = "AUTH_OK" if ok else "UNAUTHORISED"
    return {
        "id": e["id"],
        "timestamp": e["timestamp"],
        "event_type": shown_type,
        "type": shown_type,
        "severity": e.get("severity") or ("INFO" if ok else "HIGH"),
        "driver_name": driver,
        "driver": driver,
        "match_score": float(score.group(1)) if score else None,
        "liveness_result": live.group(1).upper() if live else None,
        "transmission": json.dumps({"cmd": "AUTH", "ok": 1 if ok else 0, "driver": driver}),
        "details": details,
    }


@app.route("/api/logs", methods=["GET", "POST", "DELETE"])
def audit_logs_api():
    """Face-login audit trail: GET lists it, POST adds a record, DELETE clears the event log."""
    if request.method == "DELETE":
        deleted = database.clear_events()
        # Leave one row behind so the log itself shows that it was cleared.
        database.add_event("LOGS_CLEARED", state.active_driver_name, details=f"{deleted} event(s) were cleared.")
        return jsonify({"status": "success", "message": "Audit logs cleared."})

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        event_type = str(data.get("event_type") or data.get("type") or "AUTH_OK")[:60]
        driver_name = str(data.get("driver_name") or data.get("driver") or "Unknown")[:100]
        details = str(data.get("details") or data.get("extra_details") or "")[:500]
        speed_now, lat_now, lon_now = vehicle_position()
        database.add_event(
            event_type=event_type,
            driver_name=driver_name,
            vehicle_speed=speed_now,
            latitude=lat_now,
            longitude=lon_now,
            details=details
        )
        return jsonify({"status": "success", "message": "Log entry created."})

    # GET
    try:
        limit = max(1, min(500, int(request.args.get("limit", 100))))
    except ValueError:
        return jsonify({"status": "error", "message": "limit must be a number."}), 400
    logs = [_face_log_entry(e) for e in database.get_face_events(limit=limit)]
    return jsonify({"status": "success", "logs": logs, "total": len(logs)})


@app.route("/api/logs/stats", methods=["GET"])
def audit_logs_stats_api():
    """Counts for the face-login audit trail (other events such as accident alerts are not counted)."""
    counts = database.count_face_events()
    return jsonify({
        "status": "success",
        "total_verifications": counts["authorised"] + counts["rejected"],
        "authorised_unlocks": counts["authorised"],
        "security_rejections": counts["rejected"],
        "last_telemetry_event": counts["last_timestamp"] or "--"
    })


@app.route("/api/logs/export/csv", methods=["GET"])
def audit_logs_export_csv():
    """Exports audit logs as a downloadable CSV."""
    csv_data = database.export_events_csv()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=accialert_audit_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"}
    )


def recognition_settings() -> dict:
    """The decision threshold of the recogniser that is actually in use, with the range the slider may use."""
    if use_sface():
        return {"engine": "sface", "label": "Match score threshold", "threshold": float(face_sface.threshold),
                "min": 0.20, "max": 0.80, "step": 0.01, "default": float(config.SFACE_THRESHOLD),
                "hint": "A face is accepted when its similarity score is at least this value. Higher is stricter."}
    return {"engine": "lbph", "label": "Decision distance threshold", "threshold": float(face_recognizer.threshold),
            "min": 10.0, "max": 70.0, "step": 0.5, "default": 50.0,
            "hint": "A face is accepted when its distance is below this value. Lower is stricter."}


@app.route("/api/config", methods=["GET", "POST"])
def config_endpoints():
    """Retrieve or update recognition calibration (threshold of the active recogniser, liveness on/off)."""
    settings = recognition_settings()
    if request.method == "POST":
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "Send a JSON object."}), 400
        if "threshold" in data:
            value = safe_number(data["threshold"], default=float("nan"))
            if not math.isfinite(value) or not settings["min"] <= value <= settings["max"]:
                return jsonify({"status": "error", "message":
                                f"Threshold must be a number between {settings['min']} and {settings['max']}."}), 400
            if settings["engine"] == "sface":
                face_sface.threshold = value
            else:
                face_recognizer.threshold = value
                config.MATCH_THRESHOLD = value
        if "liveness" in data:
            if not isinstance(data["liveness"], bool):
                return jsonify({"status": "error", "message": "liveness must be true or false."}), 400
            config.ENABLE_LIVENESS_BY_DEFAULT = data["liveness"]
        settings = recognition_settings()
    return jsonify({
        "status": "success",
        "threshold": settings["threshold"],
        "engine": settings["engine"],
        "slider": {k: settings[k] for k in ("label", "min", "max", "step", "default", "hint")},
        "liveness": config.ENABLE_LIVENESS_BY_DEFAULT,
        "port": config.PORT,
        "host": config.HOST
    })


@app.route("/api/serial/status", methods=["GET"])
def serial_status_api():
    """Returns ESP32 hardware serial connection state."""
    return jsonify({
        "status": "success",
        "connected": serial_mgr.is_connected(),
        "port": serial_mgr.port,
        "baudrate": serial_mgr.baudrate,
        "mode": state.mode
    })


@app.route("/api/serial/send", methods=["POST"])
def serial_send_api():
    """Send an allowed command to the ESP32."""
    data = request.get_json(silent=True) or {}
    val = data.get("value") if data.get("value") is not None else data.get("val")
    if not (data.get("command") or data.get("cmd")):
        return jsonify({"status": "error", "message": "Command required."}), 400
    try:
        cmd, val = check_command(data.get("command") or data.get("cmd"), val)
    except ValueError as err:
        return jsonify({"status": "error", "message": str(err)}), 400
    success = send_unified_command(cmd, val, source="API_SERIAL_SEND")
    return jsonify({"status": "success" if success else "error", "command": cmd, "value": val})


@app.route("/api/serial/history", methods=["GET"])
def serial_history_api():
    """Retrieve recent serial transmissions."""
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM commands_log ORDER BY id DESC LIMIT 50")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({"status": "success", "history": rows})


# FLEET TELEMETRY & DANGER ZONE APIS (Prasanth & Muniyappan)

@app.route("/api/status", methods=["GET"])
def get_system_status():
    """Return high-level system state, mode, active driver, and COM port status."""
    return jsonify({
        "status": "success",
        "mode": state.mode,
        "active_driver": state.active_driver_name,
        "active_driver_authorized": state.active_driver_auth,
        "serial_connected": serial_mgr.is_connected(),
        "serial_port": serial_mgr.port,
        "current_zone": state.current_zone_name,
        "current_zone_code": state.current_zone_code,
        "active_alerts_count": len(state.active_alerts),
        "last_telemetry_age": (round(time.time() - state.latest_telemetry["epoch"], 1)
                               if state.latest_telemetry else None),
        "timestamp": datetime.now().isoformat()
    })


@app.route("/api/telemetry/latest", methods=["GET"])
def get_latest_telemetry_api():
    """Returns the most recent single telemetry snapshot."""
    if state.latest_telemetry:
        return jsonify({"status": "success", "data": state.latest_telemetry})
    return jsonify({"status": "empty", "message": "No telemetry recorded yet."})


DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def event_filters():
    """Filters shared by the event list and the CSV export: type, start, end (YYYY-MM-DD) and search."""
    start, end = request.args.get("start", ""), request.args.get("end", "")
    return {
        "event_type": request.args.get("type") or None,
        "start_date": start if DATE_TEXT.match(start) else None,
        "end_date": end if DATE_TEXT.match(end) else None,
        "search": (request.args.get("search") or "").strip()[:100] or None,
        "severity": (request.args.get("severity") or "").strip()[:20] or None,
    }


@app.route("/api/events", methods=["GET"])
def get_events_api():
    """Event log, newest first. Query: limit (1 to 500), offset, type, severity, start, end, search."""
    try:
        limit = max(1, min(500, int(request.args.get("limit", 50))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return jsonify({"status": "error", "message": "limit and offset must be numbers."}), 400

    return jsonify({"status": "success", "data": database.get_events(limit=limit, offset=offset, **event_filters())})


@app.route("/api/events/export", methods=["GET"])
def export_events_csv_api():
    """Download the event log as CSV (same filters as the list)."""
    csv_data = database.export_events_csv(**event_filters())

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=accialert_events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "Content-Type": "text/csv; charset=utf-8"
        }
    )


@app.route("/api/zones", methods=["GET", "POST"])
def danger_zones_api():
    """GET: all danger zones in force. POST: add a zone, or update the zone with the same code."""
    if request.method == "GET":
        return jsonify({"status": "success", "zones": list(dashboard_zone_cache)})

    data = request.get_json(silent=True) or {}
    code = str(data.get("code") or "").strip()
    name = str(data.get("name") or "").strip()
    if not code or not name or data.get("latitude") is None or data.get("longitude") is None:
        return jsonify({"status": "error", "message": "code, name, latitude, and longitude are required."}), 400

    existing = zone_db.get_zone(zone_conn, code)
    try:
        zone = zone_db.upsert_zone(zone_conn, {
            "id": code, "name": name,
            "type": existing["type"] if existing else guess_zone_type(name),
            "lat": data["latitude"], "lon": data["longitude"],
            "radius_m": data.get("radius_meters", 150.0), "speed_limit": data.get("speed_limit", 40.0),
            "severity": str(data.get("severity", "MEDIUM")).lower(), "active": 1,
        })
    except (ValueError, TypeError, KeyError) as err:
        return jsonify({"status": "error", "message": str(err)}), 400

    zones_changed()
    saved = next(z for z in zone_db.list_zone_rows(zone_conn) if z["id"] == zone["id"])
    return jsonify({"status": "success", "id": saved["num"], "message": "Danger zone saved."})


GOOGLE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{20,100}$")


def google_maps_key() -> str:
    """Optional: a Google Maps key from the environment variable ACCIALERT_GOOGLE_MAPS_KEY. "" = none (the street map)."""
    key = os.environ.get("ACCIALERT_GOOGLE_MAPS_KEY", "").strip()
    return key if GOOGLE_KEY_PATTERN.match(key) else ""


@app.route("/api/maps-config", methods=["GET"])
def maps_config_api():
    """Which map to draw: Google Maps only when ACCIALERT_GOOGLE_MAPS_KEY is set, otherwise the Google-style street map."""
    key = google_maps_key()
    return no_cache(jsonify({"status": "success", "provider": "google" if key else "street", "google_key": key}))


def zone_settings_payload() -> dict:
    return {"status": "success", "alert_distance_m": zone_alert_distance(), "min": config.ZONE_ALERT_MIN_M,
            "max": config.ZONE_ALERT_MAX_M, "presets": list(config.ZONE_ALERT_PRESETS_M)}


@app.route("/api/zone-settings", methods=["GET", "POST"])
def zone_settings_api():
    """GET: the distance for the danger-zone notification. POST (admin): change it. The map shows the zones within this
    distance and a notification appears when the car (or you) gets this close to one."""
    if request.method == "GET":
        return jsonify(zone_settings_payload())
    raw = (request.get_json(silent=True) or {}).get("alert_distance_m")
    try:
        value = int(float(raw))
        if isinstance(raw, bool) or not (config.ZONE_ALERT_MIN_M <= value <= config.ZONE_ALERT_MAX_M):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message":
                        f"Choose a distance from {config.ZONE_ALERT_MIN_M} to {config.ZONE_ALERT_MAX_M} metres."}), 400
    zone_db.set_meta(zone_conn, "zone_alert_distance_m", value)
    zone_engine.prewarn_m = float(value)
    payload = zone_settings_payload()
    socketio.emit("zone_settings", payload)
    return jsonify(payload)


@app.route("/api/zones/<int:zone_id>", methods=["DELETE"])
def delete_danger_zone_api(zone_id):
    """Delete a danger zone by its number."""
    zone = zone_db.get_zone_by_num(zone_conn, zone_id)
    if zone is None:
        return jsonify({"status": "error", "message": "Zone not found."}), 404
    zone_db.delete_zone(zone_conn, zone["id"])
    zones_changed()
    return jsonify({"status": "success", "message": f"Zone {zone['id']} deleted."})


@app.route("/api/drivers", methods=["GET", "POST"])
def drivers_api():
    """GET: List drivers. POST: Add or update driver."""
    if request.method == "GET":
        drivers = database.get_all_drivers()
        return jsonify({"status": "success", "drivers": drivers})

    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    license_number = (data.get("license_number") or "").strip()
    authorized = int(data.get("authorized", 1))
    notes = data.get("notes", "")

    if not name:
        return jsonify({"status": "error", "message": "name is required."}), 400

    if not license_number:
        import time
        license_number = f"TN-09-{datetime.now().year}-{int(time.time()) % 10000:04d}"

    driver_id = database.add_or_update_driver(name, license_number, authorized, notes)

    database.add_event(
        event_type="DRIVER_ENROLLED",
        driver_name=name,
        details=f"Biometrically registered: {name} (License: {license_number})"
    )

    # A driver can only take the wheel through a face scan (/api/verify), so "make_active" is ignored here.
    return jsonify({"status": "success", "id": driver_id, "message": "Driver record saved."})


@app.route("/api/drivers/select", methods=["POST"])
def select_active_driver_api():
    """Set which registered driver is behind the wheel (the camera page calls this after a face match).
    Only drivers that exist in the driver list can be chosen."""
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify({"status": "error", "message": "Driver name required."}), 400
    known = {d["name"].strip().lower(): d for d in database.get_all_drivers()}
    driver = known.get(name.lower())
    if driver is None:
        return jsonify({"status": "error", "message": "Unknown driver. Enrol the driver first."}), 404
    authorized = 1 if safe_number(data.get("authorized"), 1, as_int=True) and driver["authorized"] else 0
    state.active_driver_name = driver["name"]
    send_unified_command("AUTH", authorized, source="DRIVER_SELECTION")   # also sets the lock state
    return jsonify({"status": "success", "active_driver": state.active_driver_name, "authorized": state.active_driver_auth})


@app.route("/api/command", methods=["POST"])
def post_command_api():
    """
    Send control command to the vehicle (ESP32).
    Payload: {"command": "STOP|RESET|ACK|AUTH|LIMIT", "value": "optional"}
    """
    data = request.get_json(silent=True) or {}
    if not data.get("command"):
        return jsonify({"status": "error", "message": "Command parameter is required."}), 400
    try:
        command, value = check_command(data.get("command"), data.get("value"))
    except ValueError as err:
        return jsonify({"status": "error", "message": str(err)}), 400

    success = send_unified_command(command, value, source="CONTROL_PANEL_API")

    return jsonify({
        "status": "success" if success else "error",
        "command": command,
        "value": value,
        "executed_on": state.mode
    })


@app.route("/api/laptop-location", methods=["GET", "POST"])
def laptop_location_api():
    """The dashboard page sends this computer's location (browser geolocation). It is used as the vehicle
    position only while the GPS module has no fix.  POST {"lat": 12.9, "lon": 80.2, "accuracy_m": 35}"""
    if request.method == "GET":
        loc = fresh_laptop_location()
        return jsonify({"status": "success", "location": None if loc is None else {k: loc[k] for k in ("lat", "lon", "accuracy_m")}})
    data = request.get_json(silent=True) or {}
    lat, lon = safe_number(data.get("lat"), float("nan")), safe_number(data.get("lon"), float("nan"))
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return jsonify({"status": "error", "message": "lat and lon must be a real position."}), 400
    acc = safe_number(data.get("accuracy_m"), float("nan"))
    accuracy = round(acc, 1) if math.isfinite(acc) and 0 <= acc <= 100000 else None
    state.laptop_location = {"lat": round(lat, 6), "lon": round(lon, 6), "accuracy_m": accuracy, "epoch": time.time()}
    return jsonify({"status": "success", "accuracy_m": accuracy})


@app.route("/api/sms/status", methods=["GET"])
def sms_status_api():
    """Is SMS set up, who gets the texts (numbers hidden), the limits, how many went out, and the last results."""
    return jsonify({"status": "success", **sms_alert.status(), "recent": database.get_sms_log(10)})


@app.route("/api/sms/enabled", methods=["POST"])
def sms_enabled_api():
    """The admin panel's SMS on/off button. Off means no text at all (alerts, SOS and tests)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("enabled"), bool):
        return jsonify({"status": "error", "message": "Send {\"enabled\": true} or {\"enabled\": false}."}), 400
    sms_alert.set_enabled(data["enabled"])
    database.add_event(event_type="SMS_SWITCH", driver_name=state.active_driver_name,
                       details="SMS sending switched " + ("ON" if data["enabled"] else "OFF") + " by the admin",
                       severity="LOW")
    return jsonify({"status": "success", **sms_alert.status()})


@app.route("/api/sms/test", methods=["POST"])
def sms_test_api():
    """Send a test text. Only from this laptop (a text costs money), and it counts toward the hourly and daily limits."""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"status": "error", "message": "The SMS test can only be started from the laptop that runs the server."}), 403
    result = sms_alert.send_test()
    return jsonify({"status": "success" if result["sent"] else "error", **result}), 200 if result["sent"] else 400


@app.route("/api/serial/ports", methods=["GET"])
def get_serial_ports_api():
    """List available COM ports detected on the system."""
    ports = SerialManager.list_available_ports()
    return jsonify({"status": "success", "ports": ports})


@app.route("/api/serial/connect", methods=["POST"])
def connect_serial_api():
    """Connect or disconnect from a specific COM port."""
    data = request.get_json() or {}
    action = data.get("action", "connect")
    port = data.get("port", config.DEFAULT_SERIAL_PORT)
    baudrate = int(data.get("baudrate", config.DEFAULT_SERIAL_BAUD))

    if action == "disconnect":
        serial_mgr.disconnect()
        return jsonify({"status": "success", "message": "Serial port disconnected."})

    success = serial_mgr.connect(port=port, baudrate=baudrate)
    if success:
        state.mode = "serial"
    return jsonify({
        "status": "success" if success else "error",
        "connected": success,
        "port": port,
        "baudrate": baudrate
    })


# Websocket event handlers

@socketio.on("connect")
def on_client_connected():
    """Send immediate initial state upon client socket connection."""
    logger.info("New WebSocket client connected.")
    emit("status", {
        "mode": state.mode,
        "driver": state.active_driver_name,
        "serial_connected": serial_mgr.is_connected()
    })
    if state.latest_telemetry:
        emit("telemetry", state.latest_telemetry)


@socketio.on("command")
def on_socket_command(payload):
    """Handle incoming command from WebSocket client."""
    if not isinstance(payload, dict) or not payload.get("command"):
        return
    try:
        cmd, val = check_command(payload.get("command"), payload.get("value"))
    except ValueError as err:
        emit("command_rejected", {"message": str(err)})
        return
    send_unified_command(cmd, val, source="SOCKETIO_CLIENT")


# Main entrypoint

def port_is_free(host, port):
    """True when nothing is listening on the port. On Windows two servers can share one port
    and the browser then talks to a random one (often an old copy), so the bind must be exclusive."""
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


@app.errorhandler(HTTPException)
def json_http_error(err):
    """API calls always get JSON, never an HTML error page the browser cannot parse."""
    if request.path.startswith("/api/"):
        return jsonify({"status": "error", "message": err.description or err.name}), err.code
    return err


if __name__ == "__main__":
    if not port_is_free(config.HOST, config.PORT):
        print(f"\nERROR: port {config.PORT} is already used by another AcciAlert server.")
        print("Two servers on one port make the page talk to an old copy (404 errors, face not detected).")
        print("Close the other server first (Task Manager > python.exe), or run:  taskkill /F /IM python.exe")
        sys.exit(1)
    print("=" * 65)
    print("   ACCIALERT - UNIFIED SAFETY, GEOFENCING & VISION AI SUITE")
    print("=" * 65)
    print(f"Server Port:        {config.PORT} (Configured strictly)")
    print(f"Driver Cockpit:     http://127.0.0.1:{config.PORT}/")
    print(f"Fleet Dashboard:    http://127.0.0.1:{config.PORT}/dashboard")
    print(f"Zone Manager:       http://127.0.0.1:{config.PORT}/zones.html (or /zm/)")
    print(f"API Health Check:   http://127.0.0.1:{config.PORT}/api/health")
    print(f"ESP32 Wi-Fi API:    http://<this-laptop-ip>:{config.PORT}/api/v1/  (register, telemetry, device/commands)")
    for waiting in device_registry.unpaired_devices():
        print(f"ESP32 not paired:   {waiting}. To connect it run:  python -m backend.tools.devices pair --id {waiting}")
    print("=" * 65)
    socketio.run(app, host=config.HOST, port=config.PORT, debug=config.DEBUG, allow_unsafe_werkzeug=True)
