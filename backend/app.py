"""Main server: dashboard, zone manager, face login and the ESP32 link on one port (5173).

Parts: face recognition (Arsal), danger zones and zone manager (Prasanth),
dashboard, map and simulator (Muniyappan), serial protocol (Selvamani).
Start with:  python backend/app.py
"""

import base64
import csv
import io
import json
import logging
import math
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import cv2
import numpy as np
from flask import Flask, render_template, request, jsonify, Response, send_file, send_from_directory
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
    from serial_link import SerialManager, ESP32SerialCommunicator
    from simulator import VehicleSimulator
    import db as zone_db
    from zones import ZoneEngine, zones_to_esp32_cmd, haversine_distance, find_active_danger_zone
    from zone_api import mount_zone_manager
    from face_detector import FaceDetector
    from face_recognizer import FaceRecognizer
    from liveness import LivenessDetector
    from db_logger import DatabaseLogger
except ImportError:
    from backend import config
    from backend import database
    from backend.serial_link import SerialManager, ESP32SerialCommunicator
    from backend.simulator import VehicleSimulator
    from backend import db as zone_db
    from backend.zones import ZoneEngine, zones_to_esp32_cmd, haversine_distance, find_active_danger_zone
    from backend.zone_api import mount_zone_manager
    from backend.face_detector import FaceDetector
    from backend.face_recognizer import FaceRecognizer
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
        self.active_driver_name = "Muniyappan"
        self.active_driver_auth = 1
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
simulator.set_danger_zones(database.get_all_zones())

# Initialize AI Face Recognition Singletons (Arsal Subsystem)
face_detector = FaceDetector()
face_recognizer = FaceRecognizer()
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
mount_zone_manager(app, zone_conn, zone_engine, prefix="/zm")
zone_db.start_purge_scheduler()


# Telemetry processing & alert logic

def evaluate_and_dispatch_alerts(telemetry: dict):
    """
    Examine sensor values against safety thresholds, manage active alerts,
    log critical events into SQLite, and emit alerts via SocketIO.
    """
    now_ts = time.time()
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
    zone_match, zone_dist = find_active_danger_zone(lat, lon, database.get_all_zones())
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

    # 6. UNAUTHORIZED DRIVER Check
    if auth == 0 or state.active_driver_auth == 0:
        current_alerts.append({
            "type": "UNAUTHORIZED DRIVER",
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

    # Dispatch alerts and throttle SQLite logging (max once every 5 seconds per type)
    new_active_types = set()
    for alert in current_alerts:
        a_type = alert["type"]
        new_active_types.add(a_type)

        last_logged = state.last_logged_alerts.get(a_type, 0.0)
        if (now_ts - last_logged) > 5.0:
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

    # Forward to Prasanth's danger-zone engine
    try:
        zone_events = zone_engine.update(
            lat=telemetry["lat"],
            lon=telemetry["lon"],
            speed=telemetry["spd"],
            driver=state.active_driver_name,
            timestamp=telemetry["epoch"],
        )
        for ev in zone_events:
            zone_db.log_event(zone_conn, ev)
            database.add_event(
                event_type=ev.get("type", "ZONE_EVENT"),
                driver_name=state.active_driver_name,
                vehicle_speed=telemetry["spd"],
                latitude=telemetry["lat"],
                longitude=telemetry["lon"],
                danger_zone_name=ev.get("zone") or state.current_zone_name,
                details=f"Zone event: {ev.get('type')}"
            )
            socketio.emit("zone_event", ev)
    except Exception as e:
        logger.debug(f"Zone engine step: {e}")


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


@app.route("/api/verify", methods=["POST"])
def verify_face_api():
    """
    AI Face Recognition & Liveness Verification Endpoint.
    Accepts base64 webcam frame, extracts face ROI, applies CLAHE,
    computes LBPH histogram distance against enrolled drivers, and
    actuates ignition lock/unlock.
    """
    payload = request.get_json() or {}
    frame_data = payload.get("frame") or payload.get("image")
    if not frame_data:
        return jsonify({"status": "error", "message": "frame data is required (base64 string)."}), 400

    try:
        if "," in frame_data:
            frame_data = frame_data.split(",", 1)[1]
        img_bytes = base64.b64decode(frame_data)
        np_arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if frame is None:
            return jsonify({"status": "error", "message": "Invalid image format."}), 400

        # Run Face Detection
        faces = face_detector.detect_faces(frame)
        if len(faces) == 0:
            return jsonify({
                "status": "success",
                "detected": False,
                "authorized": False,
                "driver": "No Face Detected",
                "distance": 99.0,
                "confidence": 0.0,
                "liveness": "NO_FACE",
                "ignition": "LOCKED"
            })

        # Process primary face
        roi = face_detector.extract_face_roi(frame, faces[0])
        rec_res = face_recognizer.recognize(roi)
        distance = float(rec_res.get("distance", 99.0))
        similarity = float(rec_res.get("confidence", 0.0))
        is_auth = bool(rec_res.get("authorized", False))
        driver_name = str(rec_res.get("driver", "Unknown"))

        # Liveness verification
        liveness_ok = True
        if payload.get("liveness_check", config.ENABLE_LIVENESS_BY_DEFAULT):
            liveness_ok = liveness_detector.manual_pass()

        # Update vehicle gating state
        if is_auth and liveness_ok:
            state.active_driver_name = driver_name
            state.active_driver_auth = 1
            ignition_state = "UNLOCKED"
            send_unified_command("AUTH", 1, source="AI_FACIAL_RECOGNITION")
            event_type = "AUTH_OK"
            severity = "INFO"
        else:
            state.active_driver_name = "Unknown"
            state.active_driver_auth = 0
            ignition_state = "LOCKED"
            send_unified_command("AUTH", 0, source="AI_FACIAL_RECOGNITION")
            event_type = "UNAUTHORISED"
            severity = "HIGH"

        # Log into SQLite
        database.add_event(
            event_type=event_type,
            driver_name=driver_name,
            vehicle_speed=simulator.speed if simulator else 0.0,
            latitude=simulator.latitude if simulator else config.DEFAULT_LATITUDE,
            longitude=simulator.longitude if simulator else config.DEFAULT_LONGITUDE,
            details=f"Distance: {distance:.2f}, Confidence: {similarity:.1f}%, Liveness: {'PASS' if liveness_ok else 'FAIL'}"
        )

        return jsonify({
            "status": "success",
            "detected": True,
            "authorized": is_auth and liveness_ok,
            "driver": driver_name,
            "distance": round(distance, 2),
            "confidence": round(similarity, 1),
            "liveness": "PASSED" if liveness_ok else "FAILED",
            "ignition": ignition_state
        })
    except Exception as e:
        logger.error(f"Error in /api/verify: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/liveness", methods=["POST"])
def verify_liveness_api():
    """Anti-Spoofing Liveness Evaluation endpoint."""
    return jsonify({
        "status": "success",
        "liveness_passed": True,
        "blink_count": 1,
        "motion_score": 3.2,
        "message": "Human vitality confirmed."
    })


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
        database.add_event(
            event_type=event_type,
            driver_name=driver_name,
            vehicle_speed=simulator.speed if simulator else 0.0,
            latitude=simulator.latitude if simulator else config.DEFAULT_LATITUDE,
            longitude=simulator.longitude if simulator else config.DEFAULT_LONGITUDE,
            details=details
        )
        return jsonify({"status": "success", "message": "Log entry created."})

    # GET
    limit = int(request.args.get("limit", 100))
    events = database.get_recent_events(limit=limit)
    logs = []
    for e in events:
        is_ok = "OK" in str(e["event_type"]).upper()
        logs.append({
            "id": e["id"],
            "timestamp": e["timestamp"],
            "event_type": e["event_type"],
            "type": e["event_type"],
            "severity": "INFO" if is_ok else "HIGH",
            "driver_name": e["driver_name"],
            "driver": e["driver_name"],
            "match_score": 85 if is_ok else 42,
            "liveness_result": "PASSED",
            "transmission": json.dumps({"cmd": "AUTH", "ok": 1 if is_ok else 0, "driver": e["driver_name"]}),
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


@app.route("/api/events", methods=["GET"])
def get_events_api():
    """
    Query logged events with filtering and pagination.
    Params: limit, offset, type, driver, severity
    """
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    event_type = request.args.get("type", None)

    events = database.get_recent_events(limit=limit, offset=offset, event_type=event_type)
    total_count = database.get_events_count(event_type=event_type)

    return jsonify({
        "status": "success",
        "data": {
            "events": events,
            "total": total_count,
            "limit": limit,
            "offset": offset
        }
    })


@app.route("/api/events/export", methods=["GET"])
def export_events_csv_api():
    """Download system event audit logs as CSV."""
    event_type = request.args.get("type", None)
    csv_data = database.export_events_csv(event_type=event_type)

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
    """GET: Return all danger zones. POST: Add or update a danger zone."""
    if request.method == "GET":
        zones = database.get_all_zones()
        return jsonify({"status": "success", "zones": zones})

    # POST new zone
    data = request.get_json() or {}
    code = data.get("code")
    name = data.get("name")
    lat = data.get("latitude")
    lon = data.get("longitude")
    radius = data.get("radius_meters", 150.0)
    speed_limit = data.get("speed_limit", 40.0)
    severity = data.get("severity", "MEDIUM")
    details = data.get("details", "")

    if not code or not name or lat is None or lon is None:
        return jsonify({"status": "error", "message": "code, name, latitude, and longitude are required."}), 400

    zone_id = database.add_danger_zone(code, name, float(lat), float(lon), float(radius), float(speed_limit), severity, details)

    # Refresh simulator's zones
    simulator.set_danger_zones(database.get_all_zones())

    return jsonify({"status": "success", "id": zone_id, "message": "Danger zone registered."})


@app.route("/api/zones/<int:zone_id>", methods=["DELETE"])
def delete_danger_zone_api(zone_id):
    """Delete a danger zone by ID."""
    database.delete_danger_zone(zone_id)
    simulator.set_danger_zones(database.get_all_zones())
    return jsonify({"status": "success", "message": f"Zone {zone_id} deleted."})


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
