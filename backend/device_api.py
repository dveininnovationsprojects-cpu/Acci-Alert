"""ESP32 Wi-Fi API.

    POST /api/v1/device/register          first start: trade the MAC address for a device id and token
    POST /api/v1/telemetry                send one reading
    GET  /api/v1/device/commands          collect the commands the dashboard has queued
    POST /api/v1/device/commands/ack      confirm commands (so they are not run twice)
    GET  /api/v1/telemetry                newest stored readings
    GET  /api/v1/ping                     connectivity check, no token needed

The device id is configured on the backend and the ESP32 gets its token by pairing (see device_registry.py),
so neither is written in the firmware. The token is sent as  Authorization: Bearer <token>.
The older shared key (X-API-Key, from ACCIALERT_DEVICE_API_KEY or backend/data/device_api_key.txt) still works
for telemetry only, until ACCIALERT_ALLOW_LEGACY_KEY=0 is set.

Every valid reading is stored first, then passed to the dashboard (alerts, map, charts).
"""

import hmac
import logging
import math
import re
import secrets
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

try:
    import config
    import database
    import device_registry
except ImportError:  # imported as a package
    from backend import config, database, device_registry

logger = logging.getLogger("AcciAlert.DeviceAPI")

G = 9.80665  # m/s^2 per g
_DEVICE_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")

# (path, kind, min, max). kind: int, num, bool
SENSOR_FIELDS = [
    ("alcohol_adc", "int", 0, config.DEVICE_ADC_MAX),
    ("distance_cm", "num", 0, 500),
    ("accelerometer.x", "num", -160, 160),   # m/s^2 (MPU6050 at +/-16 g)
    ("accelerometer.y", "num", -160, 160),
    ("accelerometer.z", "num", -160, 160),
    ("gyroscope.x", "num", -2000, 2000),     # degrees per second
    ("gyroscope.y", "num", -2000, 2000),
    ("gyroscope.z", "num", -2000, 2000),
    ("speed_rpm", "num", 0, 20000),
    ("ir_pulse_count", "int", 0, 2_147_483_647),
    ("gps.latitude", "num", -90, 90),
    ("gps.longitude", "num", -180, 180),
    ("gps.fix_valid", "bool", None, None),
    ("gps.satellites", "int", 0, 64),
]
ALERT_FIELDS = ["alcohol", "overspeed", "accident", "obstacle", "buzzer"]


# API key

def get_api_key() -> str:
    """Key from the environment, else from the key file (created if missing)."""
    import os
    env_key = os.environ.get(config.DEVICE_API_KEY_ENV, "").strip()
    if env_key:
        return env_key
    path = config.DEVICE_API_KEY_FILE
    try:
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    key = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key + "\n", encoding="utf-8")
    logger.warning("Created a new device API key in %s", path)
    return key


def _key_from_request() -> str:
    key = request.headers.get("X-API-Key", "").strip()
    if not key:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    return key


def _legacy_key_ok(sent: str) -> bool:
    return config.DEVICE_ALLOW_LEGACY_KEY and bool(sent) and hmac.compare_digest(sent.encode(), get_api_key().encode())


def _device_from_request() -> Optional[Dict[str, Any]]:
    """The paired device that owns the token in this request, or None."""
    return device_registry.authenticate(_key_from_request())


def _authorised() -> bool:
    return _device_from_request() is not None or _legacy_key_ok(_key_from_request())


# Validation

def _get(obj: Any, path: str) -> Tuple[bool, Any]:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def validate_payload(body: Any) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, str]], List[str]]:
    """Return (clean_values, errors, warnings). clean_values is None when errors is not empty."""
    errors: List[Dict[str, str]] = []
    warnings: List[str] = []

    def err(field: str, msg: str):
        errors.append({"field": field, "error": msg})

    if not isinstance(body, dict):
        return None, [{"field": "(body)", "error": "must be a JSON object"}], warnings

    # device_id
    device_id = body.get("device_id")
    if device_id is None:
        err("device_id", "is required")
    elif not isinstance(device_id, str) or not _DEVICE_ID_RE.fullmatch(device_id):
        err("device_id", "must be 1-64 characters: letters, digits, _ . -")

    # timestamp
    ts = body.get("timestamp")
    ts_text = None
    if ts is None:
        err("timestamp", "is required")
    elif not isinstance(ts, str):
        err("timestamp", "must be an ISO 8601 string, e.g. 2026-11-14T11:00:00+05:30")
    else:
        try:
            datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
            ts_text = ts.strip()
        except ValueError:
            err("timestamp", "must be an ISO 8601 string, e.g. 2026-11-14T11:00:00+05:30")

    # sensors
    sensors = body.get("sensors")
    values: Dict[str, Any] = {}
    if not isinstance(sensors, dict):
        err("sensors", "is required and must be an object")
    else:
        for path, kind, lo, hi in SENSOR_FIELDS:
            full = "sensors." + path
            found, v = _get(sensors, path)
            if not found:
                err(full, "is required")
                continue
            if kind == "bool":
                if not isinstance(v, bool):
                    err(full, "must be true or false")
                    continue
            else:
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    err(full, "must be a number")
                    continue
                if isinstance(v, float) and not math.isfinite(v):
                    err(full, "must be a finite number")
                    continue
                if kind == "int" and isinstance(v, float):
                    if v != int(v):
                        err(full, "must be a whole number")
                        continue
                    v = int(v)
                if v < lo or v > hi:
                    err(full, f"must be between {lo} and {hi} (got {v})")
                    continue
            values[path] = v

    # alerts
    alerts = body.get("alerts")
    if not isinstance(alerts, dict):
        err("alerts", "is required and must be an object")
    else:
        for name in ALERT_FIELDS:
            if name not in alerts:
                err("alerts." + name, "is required")
            elif not isinstance(alerts[name], bool):
                err("alerts." + name, "must be true or false")
            else:
                values["alert_" + name] = alerts[name]

    if errors:
        return None, errors, warnings

    values["device_id"] = device_id
    values["device_timestamp"] = ts_text

    if not values["gps.fix_valid"]:
        warnings.append("gps.fix_valid is false: coordinates are stored but not used for danger-zone checks")
    elif values["gps.satellites"] < 4:
        warnings.append("gps.fix_valid is true but fewer than 4 satellites reported")
    return values, errors, warnings


# Conversion to the dashboard's telemetry format

def to_dashboard_packet(v: Dict[str, Any]) -> Dict[str, Any]:
    """Map a validated reading to the packet format used by the alert pipeline."""
    ax, ay, az = (v["accelerometer.x"] / G, v["accelerometer.y"] / G, v["accelerometer.z"] / G)
    horizontal = math.sqrt(ax * ax + ay * ay)
    tilt = math.degrees(math.atan2(horizontal, az)) if (horizontal or az) else 0.0
    speed_kmh = v["speed_rpm"] * config.WHEEL_CIRCUMFERENCE_M * 60.0 / 1000.0
    adc_scaled = round(v["alcohol_adc"] * 1023.0 / config.DEVICE_ADC_MAX)

    events = [name.upper() for name in ("accident", "alcohol", "obstacle", "overspeed") if v["alert_" + name]]
    if v["alert_accident"]:
        state = "EMERGENCY"
    elif events:
        state = "WARNING"
    else:
        state = "RUNNING"

    return {
        "spd": speed_kmh,
        "alc": adc_scaled,
        "dist": v["distance_cm"],
        "ax": ax, "ay": ay, "az": az,
        "tilt": tilt,
        "lat": v["gps.latitude"],
        "lon": v["gps.longitude"],
        "fix": 1 if v["gps.fix_valid"] else 0,
        "state": state,
        "evt": " ".join(events),
    }


def _db_row(v: Dict[str, Any], received_at: str) -> Dict[str, Any]:
    return {
        "device_id": v["device_id"],
        "device_timestamp": v["device_timestamp"],
        "received_at": received_at,
        "alcohol_adc": v["alcohol_adc"],
        "distance_cm": float(v["distance_cm"]),
        "accel_x": float(v["accelerometer.x"]), "accel_y": float(v["accelerometer.y"]),
        "accel_z": float(v["accelerometer.z"]),
        "gyro_x": float(v["gyroscope.x"]), "gyro_y": float(v["gyroscope.y"]), "gyro_z": float(v["gyroscope.z"]),
        "speed_rpm": float(v["speed_rpm"]),
        "ir_pulse_count": v["ir_pulse_count"],
        "latitude": float(v["gps.latitude"]), "longitude": float(v["gps.longitude"]),
        "gps_fix": int(v["gps.fix_valid"]),
        "gps_satellites": v["gps.satellites"],
        "alert_alcohol": int(v["alert_alcohol"]), "alert_overspeed": int(v["alert_overspeed"]),
        "alert_accident": int(v["alert_accident"]), "alert_obstacle": int(v["alert_obstacle"]),
        "buzzer": int(v["alert_buzzer"]),
    }


# Blueprint

def create_device_blueprint(on_reading: Optional[Callable[[Dict[str, Any]], Optional[List[str]]]] = None,
                            get_state: Optional[Callable[[], Dict[str, Any]]] = None,
                            on_register: Optional[Callable[[], None]] = None):
    """on_reading(dashboard_packet) feeds the live dashboard and returns the active alert names.
    get_state() returns the vehicle state the ESP32 must follow, for example {"auth": 0}.
    on_register() is called after an ESP32 registers (the server uses it to queue the zone list)."""
    bp = Blueprint("device_api", __name__, url_prefix="/api/v1")

    def error(status: int, code: str, message: str, **extra):
        body = {"status": "error", "code": code, "message": message}
        body.update(extra)
        return jsonify(body), status

    @bp.get("/ping")
    def ping():
        return jsonify({"status": "ok", "service": "AcciAlert", "server_time": datetime.now().astimezone().isoformat(timespec="seconds")})

    def unauthorised(what="API key"):
        resp = error(401, "unauthorized", f"Missing or invalid {what}. Send the header Authorization: Bearer <device token>.")
        resp[0].headers["WWW-Authenticate"] = 'Bearer realm="AcciAlert"'
        return resp

    @bp.post("/telemetry")
    def post_telemetry():
        device = _device_from_request()
        if device is None and not _legacy_key_ok(_key_from_request()):
            return unauthorised("device token")
        if request.content_length is not None and request.content_length > config.DEVICE_MAX_PAYLOAD_BYTES:
            return error(413, "payload_too_large", f"Body must be at most {config.DEVICE_MAX_PAYLOAD_BYTES} bytes.")
        if not request.mimetype == "application/json":
            return error(415, "unsupported_media_type", "Content-Type must be application/json.")
        body = request.get_json(silent=True)
        if body is None:
            return error(400, "invalid_json", "Body is empty or is not valid JSON.")

        if device is not None and isinstance(body, dict):
            # The device id belongs to the token. The body may leave it out, but it must not name another device.
            if body.get("device_id") not in (None, device["device_id"]):
                return error(403, "device_mismatch", "device_id in the body does not belong to this token.")
            body = dict(body, device_id=device["device_id"])

        values, errors, warnings = validate_payload(body)
        if errors:
            return error(422, "validation_failed", "One or more fields are invalid.", errors=errors)
        if device is not None:
            device_registry.touch(device["device_id"], request.remote_addr or "")

        received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            row_id = database.add_device_telemetry(_db_row(values, received_at))
        except Exception:
            logger.exception("Could not store device telemetry")
            return error(500, "storage_error", "Reading was valid but could not be stored. Try again.")

        try:
            database.purge_device_telemetry(config.DEVICE_TELEMETRY_RETENTION_HOURS)
        except Exception:
            logger.exception("Telemetry purge failed")

        processed, active = False, []
        if on_reading:
            try:
                active = on_reading(to_dashboard_packet(values)) or []
                processed = True
            except Exception:
                logger.exception("Dashboard processing failed for device reading %s", row_id)

        return jsonify({
            "status": "success",
            "id": row_id,
            "device_id": values["device_id"],
            "received_at": received_at,
            "stored": True,
            "dashboard_updated": processed,
            "server_alerts": active,
            "warnings": warnings,
            "commands_pending": device_registry.pending_count(values["device_id"]),
        }), 201

    @bp.post("/device/register")
    def register_device():
        if request.content_length is not None and request.content_length > 1024:
            return error(413, "payload_too_large", "Body must be at most 1024 bytes.")
        body = request.get_json(silent=True) if request.mimetype == "application/json" else None
        if not isinstance(body, dict):
            return error(400, "invalid_json", "Send JSON like {\"mac\": \"AA:BB:CC:DD:EE:FF\", \"firmware\": \"1.0.0\"}.")
        result, problem = device_registry.register(body.get("mac"), body.get("firmware"), request.remote_addr or "?")
        if problem:
            status, code, message = problem
            return error(status, code, message)
        if on_register:
            try:
                on_register()
            except Exception:
                logger.exception("on_register failed")
        return jsonify({
            "status": "success",
            "device_id": result["device_id"],
            "token": result["token"],
            "token_type": "Bearer",
            "message": "Store the token in flash (Preferences). It is shown only once.",
        }), 201

    @bp.get("/device/commands")
    def get_commands():
        device = _device_from_request()
        if device is None:
            return unauthorised("device token")
        try:
            limit = int(request.args.get("limit", 10))
        except ValueError:
            return error(400, "bad_request", "limit must be a whole number.")
        device_registry.touch(device["device_id"], request.remote_addr or "")
        commands = device_registry.fetch_commands(device["device_id"], limit=limit)
        state = {"auth": 0}
        if get_state:
            try:
                state = get_state()
            except Exception:
                logger.exception("Could not read the vehicle state")
        return jsonify({
            "status": "success",
            "device_id": device["device_id"],
            "server_time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "state": state,
            "count": len(commands),
            "commands": commands,
        })

    @bp.post("/device/commands/ack")
    def ack_commands():
        device = _device_from_request()
        if device is None:
            return unauthorised("device token")
        if not request.mimetype == "application/json":
            return error(415, "unsupported_media_type", "Content-Type must be application/json.")
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return error(400, "invalid_json", "Body is empty or is not valid JSON.")
        acks = body.get("acks") if "acks" in body else ([body] if "id" in body else None)
        problems = []
        if not isinstance(acks, list) or not acks:
            problems.append({"field": "acks", "error": "must be a non-empty list of {id, result}"})
        elif len(acks) > 50:
            problems.append({"field": "acks", "error": "at most 50 per request"})
        else:
            for n, a in enumerate(acks):
                where = f"acks[{n}]"
                if not isinstance(a, dict):
                    problems.append({"field": where, "error": "must be an object"})
                    continue
                if isinstance(a.get("id"), bool) or not isinstance(a.get("id"), int) or a["id"] < 1:
                    problems.append({"field": where + ".id", "error": "must be a whole number from the commands list"})
                if a.get("result") not in device_registry.ACK_RESULTS:
                    problems.append({"field": where + ".result", "error": "must be one of " + ", ".join(device_registry.ACK_RESULTS)})
                if "detail" in a and not isinstance(a["detail"], str):
                    problems.append({"field": where + ".detail", "error": "must be text"})
        if problems:
            return error(422, "validation_failed", "One or more fields are invalid.", errors=problems)
        outcome = device_registry.acknowledge(device["device_id"], acks)
        return jsonify({"status": "success", **outcome})

    @bp.get("/telemetry")
    def list_telemetry():
        if not _authorised():
            return error(401, "unauthorized", "Missing or invalid API key. Send the header X-API-Key.")
        try:
            limit = max(1, min(500, int(request.args.get("limit", 50))))
        except ValueError:
            return error(400, "bad_request", "limit must be a whole number.")
        rows = database.get_device_telemetry(limit=limit, device_id=request.args.get("device_id"))
        return jsonify({"status": "success", "count": len(rows), "readings": rows})

    @bp.errorhandler(Exception)
    def unexpected(exc):
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            return error(exc.code or 500, "http_error", exc.description or exc.name)
        logger.exception("Unhandled error in device API")
        return error(500, "server_error", "Unexpected server error.")

    return bp
