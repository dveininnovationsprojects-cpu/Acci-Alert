"""Zone + event REST API (Flask blueprint) and a tiny standalone dev server.

Muniyappan can plug this into his Flask app:

    from zone_api import create_zone_blueprint
    app.register_blueprint(create_zone_blueprint(conn, engine, on_zones_changed=send_to_esp32))

Run standalone (serves ../frontend too):  python backend/zone_api.py

Hardening: JSON-object bodies only, validated fields (zones.validate_zone),
clamped query parameters, request size cap, generic error messages (no database
internals leaked), and a failing on_zones_changed callback cannot turn a saved
change into an HTTP error. The dev server binds to 127.0.0.1 only and has no
authentication: it is meant for the local demo.
"""
import logging
import math
import sqlite3
import sys
import time
from pathlib import Path

from flask import Blueprint, Flask, Response, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import db  # noqa: E402
    from zones import ZoneEngine, zones_to_esp32_cmd  # noqa: E402
except ImportError:
    from backend import db  # noqa: E402
    from backend.zones import ZoneEngine, zones_to_esp32_cmd  # noqa: E402

log = logging.getLogger("accialert.api")

BACKEND = Path(__file__).resolve().parent
FRONTEND = BACKEND.parent / "frontend"
MAX_BODY_BYTES = 64 * 1024


def _err(msg, code=400):
    return jsonify({"error": msg}), code


def create_zone_blueprint(conn, engine=None, on_zones_changed=None):
    """on_zones_changed(esp32_cmd) is called after every edit so the serial
    thread can send the {"cmd":"ZONES"...} line to the ESP32."""
    bp = Blueprint("zones", __name__)

    def changed():
        zones = db.list_zones(conn)
        if engine is not None:
            engine.set_zones(zones)
        if on_zones_changed:
            try:
                on_zones_changed(zones_to_esp32_cmd(zones))
            except Exception:  # the change is already saved; do not fail the request
                log.exception("on_zones_changed callback failed")

    def json_object():
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else None

    def num_arg(name):
        raw = request.args.get(name)
        if raw in (None, ""):
            return None
        v = float(raw)  # ValueError -> 400 in caller
        if not math.isfinite(v):
            raise ValueError("not finite")
        return v

    @bp.after_app_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp

    @bp.app_errorhandler(413)
    def too_large(_e):
        return _err("request too large", 413)

    @bp.get("/api/zones")
    def get_zones():
        return jsonify(db.list_zones(conn))

    @bp.post("/api/zones")
    def add_zone():
        z = json_object()
        if z is None:
            return _err("body must be a JSON object")
        try:
            if db.get_zone(conn, z.get("id", "")):
                return _err("zone id already exists")
            saved = db.upsert_zone(conn, z)
        except ValueError as e:                 # our own validation messages
            return _err(str(e))
        except sqlite3.Error:
            log.exception("add_zone failed")
            return _err("database error", 500)
        changed()
        return jsonify(saved), 201

    @bp.put("/api/zones/<zid>")
    def edit_zone(zid):
        z = json_object()
        if z is None:
            return _err("body must be a JSON object")
        try:
            if not db.get_zone(conn, zid):
                return _err("not found", 404)
            z["id"] = zid
            saved = db.upsert_zone(conn, z)
        except ValueError as e:
            return _err(str(e))
        except sqlite3.Error:
            log.exception("edit_zone failed")
            return _err("database error", 500)
        changed()
        return jsonify(saved)

    @bp.delete("/api/zones/<zid>")
    def remove_zone(zid):
        try:
            if not db.delete_zone(conn, zid):
                return _err("not found", 404)
        except sqlite3.Error:
            log.exception("remove_zone failed")
            return _err("database error", 500)
        changed()
        return "", 204

    def _events():
        limit_raw = request.args.get("limit", "500")
        return db.query_events(conn, type=request.args.get("type") or None,
                               date_from=num_arg("from"), date_to=num_arg("to"),
                               limit=int(limit_raw))  # ValueError -> 400 in caller

    @bp.get("/api/events")
    def get_events():
        try:
            return jsonify(_events())
        except ValueError:
            return _err("bad filter value")
        except sqlite3.Error:
            log.exception("get_events failed")
            return _err("database error", 500)

    @bp.get("/api/zone-status")
    def zone_status():
        """Small reply for the dashboard counter: zones entered today and when the current zone was entered."""
        midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        try:
            return jsonify(db.zone_status(conn, midnight))
        except sqlite3.Error:
            log.exception("zone_status failed")
            return _err("database error", 500)

    @bp.get("/api/events.csv")
    def get_events_csv():
        try:
            text = db.events_to_csv(_events())
        except ValueError:
            return _err("bad filter value")
        except sqlite3.Error:
            log.exception("get_events_csv failed")
            return _err("database error", 500)
        return Response(text, mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=events.csv"})

    return bp


def mount_zone_manager(app, conn, engine, on_zones_changed=None, prefix="/zm"):
    """Add the zone API and the zone manager page to an existing Flask app
    (Muniyappan's app.py) WITHOUT touching its own /api/zones routes.
    API: <prefix>/api/zones, <prefix>/api/events ...   Page: <prefix>/"""
    from flask import redirect, send_from_directory
    app.register_blueprint(create_zone_blueprint(conn, engine, on_zones_changed),
                           url_prefix=prefix, name="zone_manager")

    @app.after_request
    def allow_embed(resp):  # runs before the blueprint's DENY default; lets the dashboard embed /zm/
        if request.path.startswith(prefix):
            resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        return resp

    def page():
        return send_from_directory(str(FRONTEND), "zones.html")

    def asset(name):
        if name not in ("zones.css", "zones.js"):
            return _err("not found", 404)
        return send_from_directory(str(FRONTEND), name)

    app.add_url_rule(prefix, "zm_redirect", lambda: redirect(prefix + "/"))
    app.add_url_rule(prefix + "/", "zm_page", page)
    app.add_url_rule(prefix + "/<name>", "zm_asset", asset)


def create_dev_app(db_path=db.DEFAULT_DB):
    conn = db.connect(db_path)
    db.seed_zones_from_json(conn, BACKEND / "zones.json")
    engine = ZoneEngine(db.list_zones(conn))
    app = Flask(__name__, static_folder=str(FRONTEND), static_url_path="")
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    app.register_blueprint(create_zone_blueprint(conn, engine))

    @app.get("/")
    def index():
        return app.send_static_file("zones.html")

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.start_purge_scheduler()  # drop telemetry older than 24 h, hourly
    create_dev_app().run(host="127.0.0.1", port=5000, debug=False)
