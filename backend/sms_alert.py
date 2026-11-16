"""SMS alerts through Circuit Digest Cloud (free SMS API for India).

A text goes out when one of these starts: ACCIDENT, ALCOHOL DETECTED, UNAUTHORISED DRIVER (the car moves
with no authorised driver) or UNAUTHORISED LOGIN (a face was shown to the camera and rejected).

The service sends a ready-made message (a "template") with two empty slots, var1 and var2. Each value is at most
30 letters or digits. Which template to use, and what goes into var1 and var2, is set in the settings.

Account details are never in the code. They come from the environment variables
    ACCIALERT_SMS_API_KEY   ACCIALERT_SMS_TEMPLATE_ID   ACCIALERT_SMS_TO   (ACCIALERT_SMS_ENABLED=0 switches it off)
or from backend/data/sms_settings.json (created by `python -m backend.tools.sms setup`, not committed).
The environment wins over the file. With no settings, nothing is sent and nothing breaks.

It cannot spam: the same alert type is texted once per cooldown, and the number of messages per hour, per day and
per month is capped (the free plan has a monthly limit). The counts come from the database, so a restart does not
reset them. Sending runs in a background thread with a short timeout, so a slow or failing service never delays
the telemetry.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    import config
    import database
except ImportError:  # imported as a package
    from backend import config, database

logger = logging.getLogger("AcciAlert.SMS")

DEFAULT_API_URL = "https://www.circuitdigest.cloud/api/v1/send_sms"
RUN_IN_BACKGROUND = True            # tests switch this off to send in the calling thread
VALUE_SOURCES = ("alert", "alert_text", "driver", "speed", "time", "location")
VALUE_STYLES = ("plain", "bare", "free")   # plain: words only. bare: link without https://. free: full https link
_lock = threading.Lock()
_last_attempt: Dict[str, float] = {}
_warned: Dict[str, float] = {}

# What the service is told for each alert (letters and digits only, no spaces)
ALERT_WORDS = {
    "ACCIDENT": "Accident",
    "CAR CRASHED": "CarCrashed",
    "ALCOHOL DETECTED": "Alcohol",
    "UNAUTHORISED DRIVER": "UnauthorisedDriver",
    "UNAUTHORISED LOGIN": "UnauthorisedLogin",
    "SOS": "SOS",
    "TEST": "Test",
}

# Readable wording, for templates like 101 "Your {var1} is currently at {var2}."
ALERT_PHRASES = {
    "ACCIDENT": "vehicle accident",
    "CAR CRASHED": "crashed car",
    "ALCOHOL DETECTED": "driver alcohol alert",
    "UNAUTHORISED DRIVER": "unauthorised driver",
    "UNAUTHORISED LOGIN": "unauthorised start attempt",
    "SOS": "SOS emergency",
    "TEST": "AcciAlert test",
}


# Settings

def mask(number: str) -> str:
    return number[:3] + "*" * max(0, len(number) - 7) + number[-4:] if len(number) > 7 else "****"


def normalise_number(value: Any) -> Optional[str]:
    """An Indian mobile number as 91 + 10 digits (the form the service wants), or None."""
    digits = re.sub(r"[\s\-()+]", "", str(value or ""))
    if re.fullmatch(r"[6-9]\d{9}", digits):
        return "91" + digits
    if re.fullmatch(r"91[6-9]\d{9}", digits):
        return digits
    return None


def _split_numbers(value: Any) -> List[str]:
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def _switch_file():
    """The admin panel's on/off button is kept in its own small file next to sms_settings.json."""
    return config.SMS_SETTINGS_FILE.with_name("sms_switch.json")


def _switch_value() -> Optional[bool]:
    try:
        value = json.loads(_switch_file().read_text(encoding="utf-8")).get("enabled")
        return value if isinstance(value, bool) else None
    except (OSError, ValueError, AttributeError):
        return None


def set_enabled(on: bool) -> None:
    """Switch SMS sending on or off (kept after a restart). The API key and numbers are not touched."""
    path = _switch_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabled": bool(on)}), encoding="utf-8")
    logger.info("SMS sending switched %s from the admin panel", "ON" if on else "OFF")


def load_settings() -> Dict[str, Any]:
    """Settings from the file, overridden by the environment. Returns a dict with a `problems` list."""
    data: Dict[str, Any] = {}
    try:
        data = json.loads(config.SMS_SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        logger.warning("sms_settings.json could not be read")

    env = os.environ.get
    api_key = (env("ACCIALERT_SMS_API_KEY") or data.get("api_key") or "").strip()
    template_id = str(env("ACCIALERT_SMS_TEMPLATE_ID") or data.get("template_id") or "").strip()
    raw_numbers = _split_numbers(env("ACCIALERT_SMS_TO") or data.get("to_numbers"))
    # Order: the environment variable, then the on/off button in the admin panel, then sms_settings.json, then on
    switch = _switch_value()
    if env("ACCIALERT_SMS_ENABLED") is not None:
        enabled = env("ACCIALERT_SMS_ENABLED") != "0"
    elif switch is not None:
        enabled = switch
    else:
        enabled = data.get("enabled", True)
    api_url = (data.get("api_url") or DEFAULT_API_URL).strip()

    problems, warnings = [], []
    if not api_key:
        problems.append("API key missing")
    if not re.fullmatch(r"\d{1,6}", template_id):
        problems.append("template ID missing (a number like 103)")
    numbers = [normalise_number(n) for n in raw_numbers]
    if any(n is None for n in numbers):
        problems.append("a phone number is not an Indian mobile number (10 digits starting 6-9)")
    good = [n for n in numbers if n]
    if not good:
        problems.append("no recipient number")
    if len(good) > config.SMS_MAX_RECIPIENTS:      # still works: the first ones are used
        warnings.append(f"only the first {config.SMS_MAX_RECIPIENTS} recipients are texted")
        good = good[:config.SMS_MAX_RECIPIENTS]
    if not api_url.lower().startswith("https://"):
        warnings.append("the API address is not https: the key travels unencrypted")
    var1, var2 = data.get("var1", "alert"), data.get("var2", "driver")
    style = data.get("value_style", "plain")
    if style not in VALUE_STYLES:
        problems.append(f"value_style must be one of {', '.join(VALUE_STYLES)}")
    for name, v in (("var1", var1), ("var2", var2)):
        if v not in VALUE_SOURCES:
            problems.append(f"{name} must be one of {', '.join(VALUE_SOURCES)}")

    return {
        "enabled": bool(enabled), "api_key": api_key, "template_id": template_id, "api_url": api_url,
        "to_numbers": good, "problems": problems, "warnings": warnings, "configured": not problems,
        "var1": var1, "var2": var2, "value_style": style,
        "cooldown_s": int(data.get("cooldown_s", config.SMS_COOLDOWN_S)),
        "max_per_hour": int(data.get("max_per_hour", config.SMS_MAX_PER_HOUR)),
        "max_per_day": int(data.get("max_per_day", config.SMS_MAX_PER_DAY)),
        "max_per_month": int(data.get("max_per_month", config.SMS_MAX_PER_MONTH)),
    }


def status() -> Dict[str, Any]:
    """What the dashboard and the setup tool may show. Never the API key or the full numbers."""
    s = load_settings()
    return {
        "provider": "Circuit Digest Cloud",
        "configured": s["configured"], "enabled": s["enabled"], "active": s["configured"] and s["enabled"],
        "template_id": s["template_id"], "values": {"var1": s["var1"], "var2": s["var2"], "style": s["value_style"]},
        "recipients": [mask(n) for n in s["to_numbers"]],
        "problems": s["problems"], "warnings": s["warnings"],
        "limits": {"cooldown_s": s["cooldown_s"], "max_per_hour": s["max_per_hour"],
                   "max_per_day": s["max_per_day"], "max_per_month": s["max_per_month"]},
        "sent_last_hour": database.count_sms_since(3600), "sent_last_day": database.count_sms_since(86400),
        "sent_last_month": database.count_sms_since(30 * 86400),
        "alert_types": list(config.SMS_ALERT_TYPES),
    }


# Message values

def _letters_digits(text: Any, limit: int = 30) -> str:
    """Plain style: letters, digits and single spaces, at most 30 (the service's tutorial: alphanumeric, 30)."""
    kept = "".join(c if (c.isascii() and (c.isalnum() or c == " ")) else " " for c in str(text or ""))
    return " ".join(kept.split())[:limit].strip()

def _free_text(text: Any, limit: int = 100) -> str:
    """Free style: readable text and web links, with no control characters."""
    cleaned = "".join(" " if (ord(c) < 32 or ord(c) == 127) else c for c in str(text or ""))
    return " ".join(cleaned.split())[:limit]

def maps_link(info: Dict[str, Any], style: str = "free") -> str:
    """The car's position: a full Google Maps link (free), a link without https:// (bare), or the coordinates in
    words (plain: no link and no punctuation)."""
    lat, lon = info.get("lat"), info.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        if style == "free":
            return f"https://maps.google.com/?q={lat:.5f},{lon:.5f}"
        if style == "bare":
            return f"maps.google.com/?q={lat:.5f},{lon:.5f}"
        north_south = "N" if lat >= 0 else "S"
        east_west = "E" if lon >= 0 else "W"
        return f"lat {abs(lat):.4f} {north_south} lon {abs(lon):.4f} {east_west}".replace(".", " ")
    return "unknown location"

def build_variables(alert_type: str, info: Optional[Dict[str, Any]], settings: Dict[str, Any]) -> Tuple[str, str]:
    """The two values that fill the template. Chosen in the settings: alert, alert_text, driver, speed, time, location."""
    info = info or {}
    style = settings.get("value_style", "plain")
    clean = _letters_digits if style == "plain" else _free_text

    def value(source: str) -> str:
        if source == "alert":
            return ALERT_WORDS.get(alert_type, _letters_digits(alert_type.title()))
        if source == "alert_text":
            return ALERT_PHRASES.get(alert_type, alert_type.lower())
        if source == "driver":
            return clean(info.get("driver")) or "Unknown"
        if source == "speed":
            speed = info.get("speed")
            return f"{speed:.0f}" if isinstance(speed, (int, float)) else "0"
        if source == "location":
            return maps_link(info, style)
        return datetime.now().strftime("%H%M")

    fallback = ("Alert", "Unknown")
    return clean(value(settings["var1"])) or fallback[0], clean(value(settings["var2"])) or fallback[1]


# Sending

def _post(settings: Dict[str, Any], number: str, var1: str, var2: str) -> Dict[str, Any]:
    resp = requests.post(
        settings["api_url"], params={"ID": settings["template_id"]},
        headers={"Content-Type": "application/json", "Authorization": settings["api_key"]},
        data=json.dumps({"mobiles": number, "var1": var1, "var2": var2}), timeout=config.SMS_TIMEOUT_S)
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    refused = payload.get("success") is False or str(payload.get("status", "")).lower() in ("error", "failed", "fail")
    return {"ok": resp.status_code in (200, 201) and not refused, "status_code": resp.status_code, "payload": payload}


def explain_error(result: Dict[str, Any]) -> str:
    payload = result.get("payload") or {}
    code = result.get("status_code")
    text = str(payload.get("message") or payload.get("error") or payload.get("detail") or "")[:150]
    hints = {
        400: "the service rejected the request (check the template ID, and that the values use letters and digits only)",
        401: "the API key was not accepted",
        403: "the API key was refused, or this number is not verified on the Circuit Digest dashboard",
        404: "the API address or the template ID is wrong",
        429: "a limit was reached (the free plan allows a limited number of SMS per month)",
    }
    hint = hints.get(code, "")
    return f"HTTP {code}: " + " - ".join(p for p in (hint, text) if p) if (hint or text) else f"HTTP {code}: no details"


_send_lock = threading.Lock()      # one text at a time to the service
_last_post_at = 0.0


def _post_spaced(settings: Dict[str, Any], number: str, var1: str, var2: str) -> Dict[str, Any]:
    """The service refuses texts that arrive too close together ("SMS rate limit exceeded. Please wait 7.3 seconds").
    So texts go out one at a time with a gap (SMS_MIN_GAP_S), and a refusal that says how long to wait is tried again."""
    global _last_post_at
    result: Dict[str, Any] = {}
    for _attempt in range(3):
        with _send_lock:
            pause = _last_post_at + config.SMS_MIN_GAP_S - time.time()
            if pause > 0:
                time.sleep(pause)
            result = _post(settings, number, var1, var2)
            _last_post_at = time.time()
        if result["ok"]:
            return result
        message = str((result.get("payload") or {}).get("message") or "")
        wait = re.search(r"wait\s+([\d.]+)\s*second", message, re.IGNORECASE)
        if result.get("status_code") == 429 and wait:
            time.sleep(min(float(wait.group(1)) + 1.0, 30.0))
            continue
        break
    return result


def _send_all(settings: Dict[str, Any], alert_type: str, var1: str, var2: str, log_ids: Dict[str, int]) -> None:
    for number in settings["to_numbers"]:
        log_id = log_ids[number]
        try:
            result = _post_spaced(settings, number, var1, var2)
            if result["ok"]:
                database.update_sms_log(log_id, "sent", f"accepted ({var1}, {var2})")
                logger.info("SMS %s sent to %s", alert_type, mask(number))
            else:
                detail = explain_error(result)
                database.update_sms_log(log_id, "failed", detail)
                logger.warning("SMS %s to %s failed: %s", alert_type, mask(number), detail)
        except requests.RequestException as err:
            database.update_sms_log(log_id, "failed", f"network error: {type(err).__name__}")
            logger.warning("SMS %s to %s failed: network error %s", alert_type, mask(number), type(err).__name__)
        except Exception:
            database.update_sms_log(log_id, "failed", "unexpected error")
            logger.exception("SMS send failed")


def _warn_once(key: str, message: str) -> None:
    if time.time() - _warned.get(key, 0) > 3600:
        _warned[key] = time.time()
        logger.warning(message)


def notify(alert_type: str, info: Optional[Dict[str, Any]] = None, force: bool = False) -> bool:
    """Text the recipients about an alert. Returns True when a message was queued.
    Never raises. `force` (used by the test message) skips the alert-type list and the cooldown, not the caps."""
    try:
        alert_type = str(alert_type).upper()
        if not force and alert_type not in config.SMS_ALERT_TYPES:
            return False
        settings = load_settings()
        if not settings["enabled"]:
            return False
        if not settings["configured"]:
            _warn_once("config", "SMS alerts are not set up (" + "; ".join(settings["problems"]) + "). Run: python -m backend.tools.sms setup")
            return False

        with _lock:
            now = time.time()
            sos = alert_type == "SOS"          # an SOS press is urgent: short cooldown, and the hourly and daily limits do not stop it
            cooldown = config.SMS_SOS_COOLDOWN_S if sos else settings["cooldown_s"]
            # The limits that urgent alerts ignore: SOS ignores the hourly and daily ones, an accident or a crash only the
            # hourly one (an hour of test alerts must not silence the text that matters). Daily and monthly still protect
            # the service's quota.
            skipped_caps = ("max_per_hour", "max_per_day") if sos else (("max_per_hour",) if alert_type in config.SMS_NO_HOURLY_CAP else ())
            if not force and now - _last_attempt.get(alert_type, 0) < cooldown:
                return False
            n = len(settings["to_numbers"])
            # "Daily" means today since midnight (how the service counts it), not a rolling 24 hours: a text sent
            # yesterday afternoon must not block an alert this morning.
            since_midnight = (datetime.now() - datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()
            for key, seconds, label in (("max_per_hour", 3600, "hourly"), ("max_per_day", since_midnight + 1, "daily"),
                                        ("max_per_month", 30 * 86400, "monthly")):
                if key in skipped_caps:
                    continue
                if settings[key] > 0 and database.count_sms_since(seconds) + n > settings[key]:       # 0 = no limit
                    _warn_once(label, f"SMS {label} limit ({settings[key]}) reached: {alert_type} not texted")
                    return False
            _last_attempt[alert_type] = now
            log_ids = {num: database.add_sms_log(alert_type, mask(num), "queued") for num in settings["to_numbers"]}

        var1, var2 = build_variables(alert_type, info, settings)
        if RUN_IN_BACKGROUND:
            threading.Thread(target=_send_all, args=(settings, alert_type, var1, var2, log_ids), daemon=True).start()
        else:
            _send_all(settings, alert_type, var1, var2, log_ids)
        return True
    except Exception:
        logger.exception("SMS notify failed")
        return False


def send_test() -> Dict[str, Any]:
    """A test text to the recipients (counts toward the caps). Returns what happened."""
    settings = load_settings()
    if not settings["configured"]:
        return {"sent": False, "message": "SMS is not set up: " + "; ".join(settings["problems"])}
    if not settings["enabled"]:
        return {"sent": False, "message": "SMS alerts are switched off."}
    queued = notify("TEST", {"driver": "AcciAlert", "lat": 13.0827, "lon": 80.2707}, force=True)
    if not queued:
        return {"sent": False, "message": "Not sent: a limit was reached."}
    rows = database.get_sms_log(len(settings["to_numbers"]))
    ok = all(r["status"] == "sent" for r in rows) if not RUN_IN_BACKGROUND else None
    return {"sent": True, "message": "Test message queued." if ok is None else ("Test message sent." if ok else "The service refused it: " + (rows[0]["detail"] or "")), "results": rows}


def reset_for_tests() -> None:
    with _lock:
        _last_attempt.clear()
        _warned.clear()
