"""
AcciAlert - Flask Backend Server & WebSocket Core
Integrates SQLite, PySerial, Vehicle Simulator, and SocketIO for live dashboard.
Author: Muniyappan
"""

import os
import sys
import time
import math
import logging
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response, send_file
from flask_socketio import SocketIO, emit
from flask_cors import CORS

import config
import database
from geo_utils import haversine_distance, find_active_danger_zone
from serial_manager import SerialManager
from simulator import VehicleSimulator

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
)
logger = logging.getLogger("AcciAlert.App")

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


def on_serial_telemetry_received(packet: dict):
    """Callback when raw telemetry arrives from ESP32 hardware."""
    if state.mode == "serial":
        process_telemetry_packet(packet, source="ESP32_SERIAL")


serial_mgr = SerialManager(
    port=config.DEFAULT_SERIAL_PORT,
    baudrate=config.DEFAULT_SERIAL_BAUD,
    on_telemetry_callback=on_serial_telemetry_received
)

# ---- Prasanth's danger-zone engine + zone manager (backend/), mounted at /zm ----
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as zone_db
from zones import ZoneEngine
from zone_api import mount_zone_manager

zone_conn = zone_db.connect()  # backend/data/accialert_zones.db (zones + zone events)
zone_db.seed_zones_from_json(zone_conn, os.path.join(os.path.dirname(os.path.abspath(__file__)), "zones.json"))
zone_engine = ZoneEngine(zone_db.list_zones(zone_conn))
mount_zone_manager(app, zone_conn, zone_engine, prefix="/zm")
zone_db.start_purge_scheduler()


# =====================================================================
# TELEMETRY PROCESSING & ALERT LOGIC
# =====================================================================

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
            "title": "ALCOHOL DETECTED!",
            "message": f"Alcohol sensor concentration exceeded threshold! Level: {alc} ADC",
            "sound": "alcohol"
        })

    # 3. UNAUTHORISED DRIVER Check
    is_unauth = (
        auth == 0 or
        "UNAUTH" in evt_field
    )
    if is_unauth:
        current_alerts.append({
            "type": "UNAUTHORISED DRIVER",
            "severity": "HIGH",
            "title": "UNAUTHORISED DRIVER!",
            "message": "Vehicle operator has not been verified or authorization revoked.",
            "sound": "unauthorized"
        })

    # 4. OBSTACLE Check
    is_obstacle = (
        dist <= config.THRESHOLDS["obstacle"]["danger"] or
        "OBSTACLE" in evt_field
    )
    if is_obstacle:
        current_alerts.append({
            "type": "OBSTACLE DETECTED",
            "severity": "HIGH",
            "title": "OBSTACLE DETECTED!",
            "message": f"Proximity warning! Obstacle within {dist:.0f} cm.",
            "sound": "obstacle"
        })

    # Check Danger Zone match from database
    zones = database.get_all_zones()
    matched_zone, dist_to_center = find_active_danger_zone(lat, lon, zones)
    zone_name = matched_zone["name"] if matched_zone else "None"
    speed_limit = float(matched_zone["speed_limit"]) if matched_zone else config.THRESHOLDS["speed"]["critical"]

    # 5. DANGER ZONE Check
    if matched_zone:
        # Check if this is a fresh entry
        if state.current_zone_code != matched_zone["code"]:
            state.current_zone_code = matched_zone["code"]
            state.current_zone_name = matched_zone["name"]
            current_alerts.append({
                "type": "DANGER ZONE",
                "severity": matched_zone.get("severity", "HIGH"),
                "title": f"DANGER ZONE: {matched_zone['name']}",
                "message": f"Vehicle entered {matched_zone['name']}. Speed limit enforced: {matched_zone['speed_limit']} km/h.",
                "sound": "zone"
            })

            # Automatically apply the zone speed limit through ESP32 command interface
            send_unified_command("LIMIT", matched_zone["speed_limit"], source="AUTO_GEO_LIMIT")
            state.zone_speed_limit_applied = matched_zone["code"]
    else:
        if state.current_zone_code != "NONE":
            # Just exited a danger zone
            logger.info(f"Vehicle exited danger zone {state.current_zone_code}")
            database.add_event(
                event_type="ZONE EXIT",
                driver_name=state.active_driver_name,
                vehicle_speed=spd,
                latitude=lat,
                longitude=lon,
                danger_zone_name=state.current_zone_name,
                details=f"Vehicle exited zone {state.current_zone_name}. Resumed standard limit."
            )
            state.current_zone_code = "NONE"
            state.current_zone_name = "None"
            state.zone_speed_limit_applied = None
            send_unified_command("LIMIT", 60.0, source="AUTO_GEO_LIMIT_RESET")

    # 6. OVERSPEED Check
    effective_limit = speed_limit if matched_zone else config.THRESHOLDS["speed"]["critical"]
    if spd > effective_limit or "OVERSPEED" in evt_field:
        current_alerts.append({
            "type": "OVERSPEED",
            "severity": "HIGH",
            "title": "OVERSPEED WARNING!",
            "message": f"Vehicle speed {spd:.1f} km/h exceeds limit of {effective_limit:.0f} km/h{' in ' + zone_name if matched_zone else ''}.",
            "sound": "overspeed"
        })

    # 7. TRAFFIC SIGNAL STOP Check
    if signal == "RED" and spd > 10.0:
        current_alerts.append({
            "type": "TRAFFIC SIGNAL STOP",
            "severity": "HIGH",
            "title": "TRAFFIC SIGNAL RED!",
            "message": f"Vehicle is in motion ({spd:.1f} km/h) approaching RED traffic signal.",
            "sound": "signal"
        })

    # Process and log alert transitions
    for alert in current_alerts:
        alert_type = alert["type"]
        last_logged = state.last_logged_alerts.get(alert_type, 0.0)

        # Debounce: Log to SQLite at most once every 8 seconds per alert type unless critical accident
        debounce_interval = 4.0 if alert_type == "ACCIDENT" else 8.0
        if (now_ts - last_logged) > debounce_interval:
            state.last_logged_alerts[alert_type] = now_ts
            database.add_event(
                event_type=alert_type,
                driver_name=state.active_driver_name if auth == 1 else "Unknown Driver",
                vehicle_speed=spd,
                latitude=lat,
                longitude=lon,
                danger_zone_name=zone_name,
                details=alert["message"]
            )
            # Emit WebSocket alert immediately
            socketio.emit("alert", alert)
            logger.warning(f"ALERT DISPATCHED: [{alert['severity']}] {alert['title']} - {alert['message']}")

    return current_alerts


def process_telemetry_packet(raw_pkt: dict, source: str = "SIMULATOR"):
    """
    Standardize telemetry packet, inject driver and system details,
    evaluate alerts, and broadcast to all connected WebSocket clients.
    """
    if not isinstance(raw_pkt, dict):
        return

    # Enrich telemetry with current server state
    enriched = dict(raw_pkt)
    enriched["source"] = source
    enriched["server_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    enriched["driver_name"] = state.active_driver_name if enriched.get("auth", 1) == 1 else "Unknown Driver"
    enriched["esp32_connected"] = serial_mgr.is_connected()
    enriched["mode"] = state.mode

    # Store telemetry once per second (rolling 24 h, purged hourly) for charts and replay
    now = time.time()
    if now - state.last_telemetry_saved >= 1.0:
        state.last_telemetry_saved = now
        try:
            zone_db.log_telemetry(zone_conn, speed=raw_pkt.get("spd"), alcohol=raw_pkt.get("alc"),
                                  distance=raw_pkt.get("dist"), tilt=raw_pkt.get("tilt"),
                                  lat=raw_pkt.get("lat"), lon=raw_pkt.get("lon"),
                                  state=raw_pkt.get("state"), zone_id=raw_pkt.get("zone"))
        except Exception:
            logger.exception("telemetry save failed")

    # Danger-zone engine (backend/zones.py): never raises, ignores bad/no-GPS fixes
    for zev in zone_engine.update(raw_pkt.get("lat"), raw_pkt.get("lon"),
                                  raw_pkt.get("spd", 0), raw_pkt.get("fix", 1)):
        zone_db.log_event(zone_conn, zev["type"], zev["severity"], driver=enriched["driver_name"],
                          speed=zev["speed"], lat=zev["lat"], lon=zev["lon"],
                          zone_id=zev["zone_id"], details=zev["details"])
        if zev["type"] in ("ZONE_ENTER", "ZONE_EXIT", "OVERSPEED_IN_ZONE"):  # show in the dashboard event table
            database.add_event(zev["type"], enriched["driver_name"], zev["speed"], zev["lat"], zev["lon"],
                               zev["zone_name"], zev["details"])
        socketio.emit("zone_event", zev)

    # Run alert detection engine
    active_alerts = evaluate_and_dispatch_alerts(enriched)
    enriched["active_alerts"] = active_alerts

    state.latest_telemetry = enriched

    # Broadcast via Flask-SocketIO 'telemetry' event
    socketio.emit("telemetry", enriched)


def send_unified_command(command: str, value=None, source: str = "UI") -> bool:
    """
    Send command to the currently active interface (Simulator or physical ESP32)
    and record in command audit log.
    """
    cmd_upper = command.strip().upper()
    cmd_str = f"{cmd_upper}:{value}" if value is not None and str(value).strip() != "" else cmd_upper

    logger.info(f"Executing command: {cmd_str} (Source: {source}, Mode: {state.mode})")

    success = False
    if state.mode == "simulation":
        simulator.handle_command(cmd_upper, value)
        success = True
    else:
        success = serial_mgr.send_command(cmd_upper, value)

    # If command affects driver auth
    if cmd_upper == "AUTH":
        try:
            state.active_driver_auth = int(value) if value is not None else (0 if state.active_driver_auth == 1 else 1)
        except Exception:
            state.active_driver_auth = 1 if state.active_driver_auth == 0 else 0

    database.log_command(cmd_str, source=source, status="SUCCESS" if success else "FAILED")
    return success


# =====================================================================
# BACKGROUND TELEMETRY LOOP
# =====================================================================

def telemetry_worker_loop():
    """
    Background worker thread running continuously.
    In simulation mode, steps the simulator every 1 second and broadcasts telemetry.
    In serial mode, monitors serial connection health.
    """
    logger.info("Background telemetry worker loop started.")
    while True:
        try:
            if state.mode == "simulation":
                # Refresh danger zones cache for simulator
                simulator.set_danger_zones(database.get_all_zones())
                pkt = simulator.step()
                process_telemetry_packet(pkt, source="SIMULATOR")
            else:
                # Serial mode: if no packet has arrived for a while, emit heartbeat
                if state.latest_telemetry is None:
                    # Send placeholder idle telemetry
                    idle_pkt = {
                        "t": int(time.time() * 1000),
                        "spd": 0.0,
                        "alc": 150,
                        "dist": 200,
                        "ax": 0.0,
                        "ay": 0.0,
                        "az": 1.0,
                        "tilt": 0.0,
                        "lat": config.DEFAULT_LATITUDE,
                        "lon": config.DEFAULT_LONGITUDE,
                        "fix": 0,
                        "state": "READY",
                        "auth": state.active_driver_auth,
                        "signal": "GREEN",
                        "zone": "NONE",
                        "evt": ""
                    }
                    process_telemetry_packet(idle_pkt, source="SERIAL_WAITING")
        except Exception as e:
            logger.error(f"Error in telemetry loop: {e}", exc_info=True)

        time.sleep(config.TELEMETRY_INTERVAL)


# Start telemetry thread as background task
telemetry_thread = None


def start_background_telemetry():
    global telemetry_thread
    if telemetry_thread is None or not telemetry_thread.is_alive():
        import threading
        telemetry_thread = threading.Thread(target=telemetry_worker_loop, daemon=True, name="TelemetryWorker")
        telemetry_thread.start()


# =====================================================================
# REST API ENDPOINTS
# =====================================================================

@app.route("/")
def index():
    """Serve the primary dashboard interface."""
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def get_system_status():
    """Return overall operational status of system, serial port, and mode."""
    serial_status = serial_mgr.get_status()
    available_ports = SerialManager.list_available_ports()

    return jsonify({
        "status": "success",
        "mode": state.mode,
        "active_driver": state.active_driver_name,
        "driver_authorized": state.active_driver_auth,
        "current_zone": state.current_zone_name,
        "serial": serial_status,
        "available_ports": available_ports,
        "latest_telemetry": state.latest_telemetry,
        "server_time": datetime.now().isoformat()
    })


@app.route("/api/mode", methods=["POST"])
def set_system_mode():
    """Switch system between Simulation Mode and Real ESP32 Mode."""
    data = request.get_json() or {}
    new_mode = str(data.get("mode", "")).lower()

    if new_mode not in ("simulation", "serial"):
        return jsonify({"status": "error", "message": "Invalid mode. Must be 'simulation' or 'serial'."}), 400

    state.mode = new_mode

    if new_mode == "serial":
        port = data.get("port", config.DEFAULT_SERIAL_PORT)
        baud = int(data.get("baudrate", config.DEFAULT_SERIAL_BAUD))
        serial_mgr.connect(port=port, baudrate=baud)
    else:
        serial_mgr.disconnect()
        simulator.reset_to_normal()

    logger.info(f"System mode switched to: {state.mode.upper()}")
    socketio.emit("status", {"mode": state.mode, "serial_connected": serial_mgr.is_connected()})

    return jsonify({
        "status": "success",
        "mode": state.mode,
        "serial_connected": serial_mgr.is_connected()
    })


@app.route("/api/events", methods=["GET"])
def get_events_api():
    """
    Retrieve stored events from SQLite with filtering.
    Query parameters:
    - limit: int (default 100)
    - offset: int (default 0)
    - type: string (e.g. 'ACCIDENT', 'ALCOHOL DETECTED', etc.)
    - start_date: string (YYYY-MM-DD)
    - end_date: string (YYYY-MM-DD)
    - search: string
    """
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    event_type = request.args.get("type", None)
    start_date = request.args.get("start_date", None)
    end_date = request.args.get("end_date", None)
    search = request.args.get("search", None)

    data = database.get_events(
        limit=limit,
        offset=offset,
        event_type=event_type,
        start_date=start_date,
        end_date=end_date,
        search=search
    )
    return jsonify({"status": "success", "data": data})


@app.route("/api/events/export", methods=["GET"])
def export_events_csv_api():
    """Export filtered events history as downloadable CSV file."""
    event_type = request.args.get("type", None)
    start_date = request.args.get("start_date", None)
    end_date = request.args.get("end_date", None)

    csv_data = database.export_events_csv(
        event_type=event_type,
        start_date=start_date,
        end_date=end_date
    )

    filename = f"accialert_events_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/api/zones", methods=["GET", "POST"])
def danger_zones_api():
    """
    GET: Retrieve list of all danger zones.
    POST: Create a new danger zone or update an existing one.
    """
    if request.method == "GET":
        zones = database.get_all_zones()
        return jsonify({"status": "success", "zones": zones})

    # POST - Create or Update
    data = request.get_json() or {}
    zone_id = data.get("id")
    code = data.get("code")
    name = data.get("name")
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    radius_meters = data.get("radius_meters", 150.0)
    speed_limit = data.get("speed_limit", 30.0)
    severity = data.get("severity", "MEDIUM")
    details = data.get("details", "")

    if not all([code, name, latitude is not None, longitude is not None]):
        return jsonify({"status": "error", "message": "code, name, latitude, and longitude are required."}), 400

    try:
        if zone_id:
            # Update
            success = database.update_zone(
                zone_id=int(zone_id),
                code=code,
                name=name,
                latitude=float(latitude),
                longitude=float(longitude),
                radius_meters=float(radius_meters),
                speed_limit=float(speed_limit),
                severity=severity,
                details=details
            )
            return jsonify({"status": "success" if success else "error", "message": "Danger zone updated."})
        else:
            # Create
            new_id = database.add_zone(
                code=code,
                name=name,
                latitude=float(latitude),
                longitude=float(longitude),
                radius_meters=float(radius_meters),
                speed_limit=float(speed_limit),
                severity=severity,
                details=details
            )
            # Update simulator's cache
            simulator.set_danger_zones(database.get_all_zones())
            return jsonify({"status": "success", "id": new_id, "message": "Danger zone created."})
    except Exception as e:
        logger.error(f"Error saving danger zone: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/zones/<int:zone_id>", methods=["DELETE"])
def delete_danger_zone_api(zone_id: int):
    """Delete a danger zone by ID."""
    success = database.delete_zone(zone_id)
    if success:
        simulator.set_danger_zones(database.get_all_zones())
        return jsonify({"status": "success", "message": f"Zone {zone_id} deleted."})
    return jsonify({"status": "error", "message": f"Zone {zone_id} not found."}), 404


@app.route("/api/drivers", methods=["GET", "POST"])
def drivers_api():
    """
    GET: Retrieve list of drivers.
    POST: Add or update driver authorization.
    """
    if request.method == "GET":
        drivers = database.get_all_drivers()
        return jsonify({
            "status": "success",
            "drivers": drivers,
            "active_driver": state.active_driver_name,
            "authorized": state.active_driver_auth
        })

    data = request.get_json() or {}
    name = data.get("name")
    license_number = data.get("license_number")
    authorized = int(data.get("authorized", 1))
    notes = data.get("notes", "")

    if not name or not license_number:
        return jsonify({"status": "error", "message": "name and license_number are required."}), 400

    driver_id = database.add_or_update_driver(name, license_number, authorized, notes)

    # If this is marked as active driver
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


# =====================================================================
# WEBSOCKET EVENT HANDLERS
# =====================================================================

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


# =====================================================================
# MAIN ENTRYPOINT
# =====================================================================

# Start the background telemetry loop
start_background_telemetry()

if __name__ == "__main__":
    logger.info(f"Starting AcciAlert Server on http://{config.HOST}:{config.PORT}")
    socketio.run(app, host=config.HOST, port=config.PORT, debug=config.DEBUG, allow_unsafe_werkzeug=True)

