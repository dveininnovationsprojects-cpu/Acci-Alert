"""Main server: dashboard, zone manager, face login and the ESP32 link on one port (5173).

Parts: face recognition (Arsal), danger zones and zone manager (Prasanth),
dashboard, map and simulator (Muniyappan), serial protocol (Selvamani).
Start with:  python backend/app.py
"""

import base64
import json
import logging
import math
import os
import re
import sys
import threading
import time
from datetime import datetime
from typing import Optional
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_socketio import SocketIO, emit
from flask_cors import CORS

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
    from simulator import VehicleSimulator
    import db as zone_db
    from zones import ZoneEngine, find_active_danger_zone
    from zone_api import mount_zone_manager
    from face_detector import FaceDetector
    from face_recognizer import FaceRecognizer, SFaceRecognizer
    from liveness import LivenessDetector
    from db_logger import DatabaseLogger
except ImportError:
    from backend import config
    from backend import database
    from backend.serial_link import SerialManager
    from backend.simulator import VehicleSimulator
    from backend import db as zone_db
    from backend.zones import ZoneEngine, find_active_danger_zone
    from backend.zone_api import mount_zone_manager
    from backend.face_detector import FaceDetector
    from backend.face_recognizer import FaceRecognizer, SFaceRecognizer
    from backend.liveness import LivenessDetector
    from backend.db_logger import DatabaseLogger

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

# Application State
class SystemState:
    def __init__(self):
        self.mode = "simulation"  # "simulation" or "serial"
        self.active_driver_name = "Unknown"
        self.active_driver_auth = 0
        self.latest_telemetry = None
        self.current_zone_code = "NONE"
        self.current_zone_name = "None"
        self.active_alerts = {}  # alert_type: timestamp
        self.last_logged_alerts = {}  # alert_type: timestamp
        self.zone_speed_limit_applied = None
        self.last_telemetry_saved = 0.0

state = SystemState()

# Initialize Simulator and Serial Manager
simulator = VehicleSimulator()

# Initialize AI Face Recognition Singletons (Arsal Subsystem)
face_detector = FaceDetector()
face_recognizer = FaceRecognizer()
face_sface = SFaceRecognizer()   # pretrained model, drivers enrolled live from the camera
database.sync_driver_samples(face_sface.enrolled())   # keep the driver table in step with the saved faces
liveness_detector = LivenessDetector()
db_logger = DatabaseLogger()


def on_serial_telemetry_received(packet: dict):
    """Callback when raw telemetry arrives from ESP32 hardware."""
    if state.mode == "serial":
        process_telemetry_packet(packet, source="ESP32_SERIAL")


serial_mgr = SerialManager(
    port=config.DEFAULT_SERIAL_PORT,
    baudrate=config.DEFAULT_SERIAL_BAUD,
    on_telemetry_callback=on_serial_telemetry_received
)

# Prasanth's danger-zone engine + zone manager (backend/), mounted at /zm
zone_conn = zone_db.connect()  # backend/data/accialert_zones.db (zones + zone events)
zone_db.seed_zones_from_json(zone_conn, os.path.join(os.path.dirname(os.path.abspath(__file__)), "zones.json"))
zone_engine = ZoneEngine(zone_db.list_zones(zone_conn))
mount_zone_manager(app, zone_conn, zone_engine, on_zones_changed=lambda *_: zones_changed(), prefix="/zm")
zone_db.start_purge_scheduler()


# ---------------------------------------------------------------- one zone list
# The zone manager's database is the only list of danger zones. The dashboard (map, zone tab),
# the simulator and the zone engine all read it. The old dashboard table is imported once.
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
    """Reload the zones used by the simulator and the dashboard (only active zones are in force)."""
    dashboard_zone_cache[:] = [zone_for_dashboard(z) for z in zone_db.list_zone_rows(zone_conn, active_only=True)]
    simulator.set_danger_zones(list(dashboard_zone_cache))


def zones_changed(*_):
    """Called after any change to the zones, from the zone manager or from the dashboard's zone tab."""
    zone_engine.set_zones(zone_db.list_zones(zone_conn))
    refresh_zone_cache()
    socketio.emit("zones_updated", {})


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


# Telemetry processing & alert logic

def evaluate_and_dispatch_alerts(telemetry: dict, now: Optional[float] = None):
    """
    Examine sensor values against safety thresholds, manage active alerts,
    log critical events into SQLite, and emit alerts via SocketIO.
    """
    now_ts = time.time() if now is None else now
    current_alerts = []

    spd = float(telemetry.get("spd", 0.0))
    alc = int(telemetry.get("alc", 0))
    dist = float(telemetry.get("dist", 999))
    ax = float(telemetry.get("ax", 0.0))
    ay = float(telemetry.get("ay", 0.0))
    az = float(telemetry.get("az", 1.0))
    tilt = float(telemetry.get("tilt", 0.0))
    auth = int(telemetry.get("auth", 1))
    signal = str(telemetry.get("signal", "GREEN")).upper()
    zone_code = str(telemetry.get("zone", "NONE"))
    evt_field = str(telemetry.get("evt", "")).upper()
    lat = float(telemetry.get("lat", config.DEFAULT_LATITUDE))
    lon = float(telemetry.get("lon", config.DEFAULT_LONGITUDE))

    # Calculate 3D acceleration vector magnitude
    accel_magnitude = math.sqrt(ax * ax + ay * ay + az * az)

    # 1. ACCIDENT Check
    is_accident = (
        tilt >= config.THRESHOLDS["tilt"]["accident"] or
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
    is_obstacle = (
        dist <= config.THRESHOLDS["obstacle"]["danger"] or
        "OBSTACLE" in evt_field
    )
    if is_obstacle:
        current_alerts.append({
            "type": "OBSTACLE DETECTED",
            "severity": "CRITICAL",
            "title": "CRITICAL PROXIMITY ALERT",
            "message": f"Obstacle within danger zone: {dist:.1f} cm! Collision imminent.",
            "sound": "alarm"
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
    zone_match, zone_dist = find_active_danger_zone(lat, lon, dashboard_zone_cache)
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
    else:
        state.current_zone_code = "NONE"
        state.current_zone_name = "None"
        if state.zone_speed_limit_applied is not None:
            # Lift zone limit
            send_unified_command("LIMIT", 0, source="ZONE_ENGINE")
            state.zone_speed_limit_applied = None

    # 6. UNAUTHORISED DRIVER Check
    if auth == 0 or state.active_driver_auth == 0:
        current_alerts.append({
            "type": "UNAUTHORISED DRIVER",
            "severity": "CRITICAL",
            "title": "SECURITY BREACH",
            "message": f"Vehicle operated by unauthorized subject ({state.active_driver_name})!",
            "sound": "alarm"
        })

    # 7. RED SIGNAL Check
    if signal == "RED":
        current_alerts.append({
            "type": "RED SIGNAL",
            "severity": "CRITICAL",
            "title": "TRAFFIC VIOLATION DETECTED",
            "message": "Vehicle passed or approached active RED Traffic Light!",
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
                details=alert["message"]
            )
            state.last_logged_alerts[a_type] = now_ts

        # Emit live alert via SocketIO
        socketio.emit("alert", alert)

    # Prune alerts that cleared
    state.active_alerts = {k: v for k, v in state.active_alerts.items() if k in new_active_types}
    for a in current_alerts:
        state.active_alerts[a["type"]] = now_ts


def process_telemetry_packet(packet: dict, source: str = "SIMULATOR"):
    """Standardizes, decorates, and broadcasts a telemetry packet."""
    telemetry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": time.time(),
        "source": source,
        "spd": round(float(packet.get("spd", 0.0)), 1),
        "alc": int(packet.get("alc", 0)),
        "dist": round(float(packet.get("dist", 150.0)), 1),
        "ax": round(float(packet.get("ax", 0.0)), 2),
        "ay": round(float(packet.get("ay", 0.0)), 2),
        "az": round(float(packet.get("az", 1.0)), 2),
        "tilt": round(float(packet.get("tilt", 0.0)), 1),
        "lat": float(packet.get("lat", config.DEFAULT_LATITUDE)),
        "lon": float(packet.get("lon", config.DEFAULT_LONGITUDE)),
        "fix": int(packet.get("fix", 1)),
        "state": str(packet.get("state", "RUNNING")),
        "auth": int(packet.get("auth", state.active_driver_auth)),
        "signal": str(packet.get("signal", "GREEN")),
        "zone": str(packet.get("zone", state.current_zone_code)),
        "evt": str(packet.get("evt", "NONE")),
        "driver": state.active_driver_name,
        "zone_name": state.current_zone_name
    }

    state.latest_telemetry = telemetry

    # Run alert detection logic
    evaluate_and_dispatch_alerts(telemetry)

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
        for ev in zone_engine.update(telemetry["lat"], telemetry["lon"], telemetry["spd"], telemetry["fix"]):
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
    """Speed, latitude and longitude of the car right now: the last packet received (ESP32 or
    simulator), or the default start point if nothing has arrived yet."""
    t = state.latest_telemetry
    if t:
        return t["spd"], t["lat"], t["lon"]
    return 0.0, config.DEFAULT_LATITUDE, config.DEFAULT_LONGITUDE


def background_telemetry_loop():
    """Background worker generating ticks when running in Simulation mode."""
    logger.info("Background telemetry thread started.")
    while True:
        try:
            if state.mode == "simulation":
                raw_pkt = simulator.step()
                process_telemetry_packet(raw_pkt, source="SIMULATOR")
            time.sleep(config.TELEMETRY_INTERVAL)
        except Exception as e:
            logger.error(f"Error in background telemetry loop: {e}")
            time.sleep(1.0)


def start_background_telemetry():
    t = threading.Thread(target=background_telemetry_loop, daemon=True)
    t.start()


def send_unified_command(command: str, value=None, source: str = "UI"):
    """
    Unified command dispatcher.
    Sends command to Simulator OR ESP32 Serial Hardware depending on active mode.
    """
    logger.info(f"Command Dispatched [{source} -> {state.mode}]: {command} = {value}")
    database.log_command(f"{command}:{value}" if value is not None else command, source=source)

    # 1. Update Simulator
    simulator.handle_command(command, value)

    # 2. Update Serial Hardware if connected
    if serial_mgr.is_connected():
        serial_mgr.send_command(command, value)

    # Broadcast command ack to frontend
    socketio.emit("command_executed", {
        "command": command,
        "value": value,
        "mode": state.mode,
        "source": source
    })
    return True


# Static assets & frontend pages

@app.route("/")
def index():
    """Main page."""
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/dashboard")
def dashboard_view():
    """Fleet dashboard page."""
    return send_from_directory(FRONTEND_DIR, "dashboard.html")


@app.route("/auth.html")
def auth_page():
    """Serves the Driver Authorisation (face recognition) page."""
    return send_from_directory(FRONTEND_DIR, "auth.html")


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


@app.route("/css/<path:filename>")
def serve_css(filename):
    return send_from_directory(os.path.join(FRONTEND_DIR, "css"), filename)


@app.route("/js/<path:filename>")
def serve_js(filename):
    return send_from_directory(os.path.join(FRONTEND_DIR, "js"), filename)


@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(os.path.join(FRONTEND_DIR, "static"), filename)


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
    try:
        images = [decode_frame(f) for f in frames]
        name = face_sface.clean_name(payload["name"])
        owner = next((d["name"] for d in database.get_all_drivers()
                      if d.get("license_number") == license_number and d["name"] != name), None)
        if owner:
            return jsonify({"status": "error", "message": f"License number {license_number} already belongs to {owner}."}), 400
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
    database.add_event("DRIVER_ENROLLED", result["name"], 0.0, config.DEFAULT_LATITUDE, config.DEFAULT_LONGITUDE,
                       "None", f"Enrolled from live camera: {result['accepted']} frames, license {license_number}, photos in {result['folder']}")
    return jsonify({"status": "success", **result})


def recognise(frame):
    """Find the face in a frame and match it.
    Returns (face_count, box [x, y, w, h] in pixels or None, driver name, recognised, confidence %, note)."""
    if use_sface():
        found = face_sface.identify(frame)
        if found["face_count"] == 0:
            return 0, None, "Unknown", False, 0.0, "No face found. Look at the camera."
        if found["face_count"] > 1:
            return found["face_count"], None, "Unknown", False, 0.0, "Only one person should be in view."
        if not face_sface.gallery:
            return 1, found["box"], "Unknown", False, 0.0, "No driver is enrolled yet. Use Enrol Drivers."
        note = "" if found["authorized"] else "Face not recognised."
        return 1, found["box"], found["driver"], found["authorized"], max(0.0, found["score"]) * 100.0, note

    # Old LBPH model (used only when the SFace models are not downloaded)
    det = face_detector.detect_faces(frame)
    if det.face_count == 0 or det.primary_roi is None:
        return 0, None, "Unknown", False, 0.0, "No face found. Look at the camera."
    if det.face_count > 1:
        return det.face_count, None, "Unknown", False, 0.0, "Only one person should be in view."
    result = face_recognizer.recognize(det.primary_roi)
    box = [int(v) for v in det.primary_box]
    note = "" if result["authorized"] else "Face not recognised."
    return 1, box, str(result["driver"]), bool(result["authorized"]), float(result["confidence"]), note


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
        face_count, box, driver_name, recognised, confidence, note = recognise(frame)

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

        unlocked = recognised and live
        shown_driver = driver_name if unlocked else "Unknown"
        if unlocked:
            note = f"Welcome, {driver_name}."

        # Lock or unlock and write to the log only when the result changes, not on every frame
        current = (unlocked, shown_driver)
        if current != getattr(state, "last_face_result", None):
            state.last_face_result = current
            state.active_driver_name = shown_driver
            state.active_driver_auth = 1 if unlocked else 0
            send_unified_command("AUTH", 1 if unlocked else 0, source="AI_FACIAL_RECOGNITION")
            if unlocked or face_count == 1:
                speed_now, lat_now, lon_now = vehicle_position()
                database.add_event(
                    event_type="AUTH_OK" if unlocked else "UNAUTHORISED DRIVER",
                    driver_name=shown_driver,
                    vehicle_speed=speed_now,
                    latitude=lat_now,
                    longitude=lon_now,
                    details=f"Match {confidence:.0f}%, liveness {liveness.lower()}"
                )

        return jsonify({
            "status": "success",
            "detected": face_count == 1,
            "face_count": face_count,
            "authorized": unlocked,
            "driver": shown_driver,
            "distance": round(100.0 - confidence, 2),
            "confidence": round(confidence, 1),
            "liveness": liveness,
            "ignition": "UNLOCKED" if unlocked else "LOCKED",
            "box": [box[0] / frame_w, box[1] / frame_h, box[2] / frame_w, box[3] / frame_h] if box else None,
            "message": note,
        })
    except Exception as e:
        logger.error(f"Error in /api/verify: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/logs", methods=["GET", "POST", "DELETE"])
def audit_logs_api():
    """Audit Trail Endpoint: GET listing, POST new record, DELETE clear logs."""
    if request.method == "DELETE":
        conn = database.get_db_connection()
        conn.execute("DELETE FROM events")
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Audit logs cleared."})

    if request.method == "POST":
        data = request.get_json() or {}
        event_type = data.get("event_type") or data.get("type") or "AUTH_OK"
        driver_name = data.get("driver_name") or data.get("driver") or "Unknown"
        details = data.get("details") or data.get("extra_details") or ""
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
    limit = int(request.args.get("limit", 100))
    events = database.get_recent_events(limit=limit)
    logs = []
    for e in events:
        is_ok = "OK" in str(e["event_type"]).upper()
        driver_val = e["driver_name"] or "Unknown"
        logs.append({
            "id": e["id"],
            "timestamp": e["timestamp"],
            "event_type": e["event_type"],
            "type": e["event_type"],
            "severity": "INFO" if is_ok else "HIGH",
            "driver_name": driver_val,
            "driver": driver_val,
            "match_score": 85 if is_ok else 42,
            "liveness_result": "PASSED",
            "transmission": json.dumps({"cmd": "AUTH", "ok": 1 if is_ok else 0, "driver": driver_val}),
            "details": e["details"] or ""
        })
    return jsonify({"status": "success", "logs": logs, "total": len(logs)})


@app.route("/api/logs/stats", methods=["GET"])
def audit_logs_stats_api():
    """Returns aggregate security metrics for the audit trail."""
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM events")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM events WHERE event_type LIKE '%OK%'")
    auth_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM events WHERE event_type NOT LIKE '%OK%'")
    unauth_count = cursor.fetchone()[0]
    cursor.execute("SELECT timestamp FROM events ORDER BY id DESC LIMIT 1")
    last_row = cursor.fetchone()
    last_ts = last_row[0] if last_row else "--"
    conn.close()

    return jsonify({
        "status": "success",
        "total_verifications": total,
        "authorised_unlocks": auth_count,
        "security_rejections": unauth_count,
        "last_telemetry_event": last_ts
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


@app.route("/api/config", methods=["GET", "POST"])
def config_endpoints():
    """Retrieve or update recognition calibration parameters."""
    if request.method == "POST":
        data = request.get_json() or {}
        if "threshold" in data:
            config.MATCH_THRESHOLD = float(data["threshold"])
        if "liveness" in data:
            config.ENABLE_LIVENESS_BY_DEFAULT = bool(data["liveness"])
        return jsonify({
            "status": "success",
            "threshold": config.MATCH_THRESHOLD,
            "liveness": config.ENABLE_LIVENESS_BY_DEFAULT
        })
    return jsonify({
        "status": "success",
        "threshold": config.MATCH_THRESHOLD,
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
    """Send arbitrary command to ESP32 / Simulator."""
    data = request.get_json() or {}
    cmd = data.get("command") or data.get("cmd")
    val = data.get("value") or data.get("val")
    if not cmd:
        return jsonify({"status": "error", "message": "Command required."}), 400
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
    }


@app.route("/api/events", methods=["GET"])
def get_events_api():
    """Event log, newest first. Query: limit (1 to 500), offset, type, start, end, search."""
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
    name = data.get("name")
    license_number = data.get("license_number")
    authorized = int(data.get("authorized", 1))
    notes = data.get("notes", "")

    if not name or not license_number:
        return jsonify({"status": "error", "message": "name and license_number are required."}), 400

    driver_id = database.add_or_update_driver(name, license_number, authorized, notes)

    if data.get("make_active", False):
        state.active_driver_name = name
        state.active_driver_auth = authorized
        send_unified_command("AUTH", authorized, source="DRIVER_SELECTION")

    return jsonify({"status": "success", "id": driver_id, "message": "Driver record saved."})


@app.route("/api/drivers/select", methods=["POST"])
def select_active_driver_api():
    """Set which driver is currently behind the wheel."""
    data = request.get_json() or {}
    name = data.get("name")
    authorized = int(data.get("authorized", 1))

    if name:
        state.active_driver_name = name
        state.active_driver_auth = authorized
        send_unified_command("AUTH", authorized, source="DRIVER_SELECTION")
        return jsonify({"status": "success", "active_driver": state.active_driver_name, "authorized": state.active_driver_auth})
    return jsonify({"status": "error", "message": "Driver name required."}), 400


@app.route("/api/command", methods=["POST"])
def post_command_api():
    """
    Send control command to vehicle (ESP32 or Simulator).
    Payload: {"command": "STOP|RESET|SIGNAL|AUTH|LIMIT|ACK", "value": "optional"}
    """
    data = request.get_json() or {}
    command = data.get("command")
    value = data.get("value", None)

    if not command:
        return jsonify({"status": "error", "message": "Command parameter is required."}), 400

    success = send_unified_command(command, value, source="CONTROL_PANEL_API")

    return jsonify({
        "status": "success" if success else "error",
        "command": command,
        "value": value,
        "executed_on": state.mode
    })


@app.route("/api/mode", methods=["POST"])
def toggle_mode_api():
    """Toggle between simulation and hardware serial mode."""
    data = request.get_json() or {}
    target_mode = data.get("mode", "simulation")
    if target_mode in ["simulation", "serial"]:
        state.mode = target_mode
        return jsonify({"status": "success", "mode": state.mode})
    return jsonify({"status": "error", "message": "Invalid mode. Choose 'simulation' or 'serial'."}), 400


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


@app.route("/api/simulation/scenario", methods=["POST"])
def trigger_simulation_scenario_api():
    """
    Trigger a specific alert scenario in the mock generator.
    Scenarios: accident, alcohol, obstacle, overspeed, unauthorized, red_signal, danger_zone, normal
    """
    data = request.get_json() or {}
    scenario = data.get("scenario", "normal")
    duration = int(data.get("duration", 15))

    simulator.trigger_scenario(scenario, duration_ticks=duration)
    return jsonify({"status": "success", "scenario": scenario, "duration": duration})


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
    cmd = payload.get("command")
    val = payload.get("value")
    if cmd:
        send_unified_command(cmd, val, source="SOCKETIO_CLIENT")


# Main entrypoint

# Start the background telemetry loop
start_background_telemetry()

if __name__ == "__main__":
    print("=" * 65)
    print("   ACCIALERT - UNIFIED SAFETY, GEOFENCING & VISION AI SUITE")
    print("=" * 65)
    print(f"Server Port:        5173 (Configured strictly)")
    print(f"Driver Cockpit:     http://localhost:5173/")
    print(f"Fleet Dashboard:    http://localhost:5173/dashboard")
    print(f"Zone Manager:       http://localhost:5173/zones.html (or /zm/)")
    print(f"API Health Check:   http://localhost:5173/api/health")
    print("=" * 65)
    socketio.run(app, host=config.HOST, port=config.PORT, debug=config.DEBUG, allow_unsafe_werkzeug=True)
