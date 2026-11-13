"""Danger-zone engine (requirements section 8.3).

Feed it GPS fixes with update(lat, lon, speed_kmh, fix); it returns the list of
events produced by that fix. Pure Python, no I/O, so it is easy to unit test
and the same logic can be mirrored in the ESP32 firmware.

Per zone:
  ENTER  when distance <= radius and the vehicle was outside
  EXIT   when distance >  radius + HYSTERESIS_M and the vehicle was inside
  PREWARN (ZONE_APPROACH) once when radius < distance <= radius + PREWARN_M
  OVERSPEED_IN_ZONE once per overspeed episode while inside (re-armed when
  speed falls back to the limit or below)

Integration contract (what other modules rely on):
  * update() NEVER raises. Bad input returns [] so the serial thread survives.
  * No GPS fix (fix == 0), non-numeric / non-finite / out-of-range coordinates,
    or the (0, 0) "no fix yet" position are IGNORED: no events, state is held.
    This prevents a false EXIT + duplicate ENTER when GPS drops out mid-zone.
  * set_zones() skips invalid zones (logged) instead of raising.
  * All public methods are thread-safe (serial thread vs Flask request threads).
"""
import logging
import math
import re
import threading

log = logging.getLogger("accialert.zones")

EARTH_RADIUS_M = 6371000.0
HYSTERESIS_M = 5.0
PREWARN_M = 20.0
MAX_ESP32_ZONES = 20  # keeps the ZONES serial line small; confirm with firmware owner

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
VALID_SEVERITIES = tuple(SEVERITY_RANK)
ZONE_FIELDS = ("id", "name", "type", "lat", "lon", "radius_m", "speed_limit", "severity", "active")

ZONE_ENTER = "ZONE_ENTER"
ZONE_EXIT = "ZONE_EXIT"
ZONE_APPROACH = "ZONE_APPROACH"
OVERSPEED_IN_ZONE = "OVERSPEED_IN_ZONE"

_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")


# ------------------------------------------------------------- validation
def _finite(v):
    """Return v as a finite float, or None (rejects bool, None, NaN, inf, junk)."""
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def valid_position(lat, lon):
    """Return (lat, lon) as floats if usable, else None.
    (0, 0) is rejected: the ESP32 reports it when there is no GPS fix yet."""
    la, lo = _finite(lat), _finite(lon)
    if la is None or lo is None:
        return None
    if not (-90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0):
        return None
    if la == 0.0 and lo == 0.0:
        return None
    return la, lo


def validate_zone(z):
    """Return a cleaned copy of zone dict z, or raise ValueError (safe message)."""
    if not isinstance(z, dict):
        raise ValueError("zone must be a JSON object")
    missing = [f for f in ZONE_FIELDS if f not in z]
    if missing:
        raise ValueError(f"zone missing fields: {missing}")

    zid = z["id"]
    if not isinstance(zid, str) or not _ID_RE.fullmatch(zid):
        raise ValueError("id must be 1-32 characters: letters, digits, _ or -")
    name = z["name"]
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 100):
        raise ValueError("name must be 1-100 characters")
    ztype = z["type"]
    if not isinstance(ztype, str) or not (1 <= len(ztype.strip()) <= 32):
        raise ValueError("type must be 1-32 characters")

    lat, lon = _finite(z["lat"]), _finite(z["lon"])
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("lat must be -90..90 and lon -180..180")
    radius = _finite(z["radius_m"])
    if radius is None or not (0 < radius <= 100000):
        raise ValueError("radius_m must be greater than 0 and at most 100000")
    limit = _finite(z["speed_limit"])
    if limit is None or not (0 <= limit <= 300):
        raise ValueError("speed_limit must be 0..300 km/h")
    sev = z["severity"]
    if not isinstance(sev, str) or sev.strip().lower() not in SEVERITY_RANK:
        raise ValueError("severity must be low, medium, high or critical")
    active = z["active"]
    if isinstance(active, str):
        active = active.strip().lower() in ("1", "true", "yes", "on")

    return {"id": zid, "name": name.strip(), "type": ztype.strip(), "lat": lat, "lon": lon,
            "radius_m": radius, "speed_limit": int(round(limit)),
            "severity": sev.strip().lower(), "active": int(bool(active))}


# ---------------------------------------------------------------- helpers
def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def zones_to_esp32_cmd(zones, max_zones=MAX_ESP32_ZONES):
    """Build the {"cmd":"ZONES",...} line for the ESP32 backup logic (7.2).
    Only valid, active zones; most severe first; capped at max_zones."""
    good = []
    for z in zones:
        try:
            v = validate_zone(z)
        except ValueError:
            continue
        if v["active"]:
            good.append(v)
    good.sort(key=lambda v: (-SEVERITY_RANK[v["severity"]], v["id"]))
    if len(good) > max_zones:
        log.warning("ZONES command capped at %d of %d active zones", max_zones, len(good))
        good = good[:max_zones]
    return {"cmd": "ZONES", "list": [
        {"id": v["id"], "lat": round(v["lat"], 6), "lon": round(v["lon"], 6),
         "r": v["radius_m"], "lim": v["speed_limit"]} for v in good]}


class ZoneEngine:
    def __init__(self, zones=(), hysteresis_m=HYSTERESIS_M, prewarn_m=PREWARN_M):
        self.hysteresis_m = hysteresis_m
        self.prewarn_m = prewarn_m
        self._lock = threading.RLock()
        self._zones = {}
        self._inside = set()      # zone ids the vehicle is currently inside
        self._warned = set()      # zone ids already pre-warned this approach
        self._overspeed = set()   # zone ids with an overspeed event already raised
        self.set_zones(zones)

    # zone management (works without restarting the server)
    def set_zones(self, zones):
        """Replace the zone list. State for zones that still exist is kept.
        Invalid zones are skipped; returns a list of rejection messages."""
        new, rejected = {}, []
        for z in zones:
            try:
                v = validate_zone(z)
            except ValueError as e:
                zid = z.get("id") if isinstance(z, dict) else "?"
                rejected.append(f"{zid}: {e}")
                log.warning("zone %s rejected: %s", zid, e)
                continue
            new[v["id"]] = v
        with self._lock:
            self._zones = new
            for s in (self._inside, self._warned, self._overspeed):
                s.intersection_update(self._zones)
        return rejected

    @property
    def zones(self):
        with self._lock:
            return list(self._zones.values())

    @property
    def current_zone(self):
        """Most severe active zone the vehicle is currently inside, or None."""
        with self._lock:
            inside = [self._zones[i] for i in self._inside if self._zones[i]["active"]]
            if not inside:
                return None
            return max(inside, key=lambda z: SEVERITY_RANK[z["severity"]])

    def effective_limit(self):
        """Lowest speed limit among zones we are inside, or None."""
        with self._lock:
            lims = [self._zones[i]["speed_limit"] for i in self._inside]
            return min(lims) if lims else None

    # main entry point
    def update(self, lat, lon, speed_kmh=0.0, fix=1):
        """Process one GPS fix. Never raises. Returns a list of event dicts."""
        try:
            if not fix:                       # fix == 0: GPS has no lock, hold state
                return []
            pos = valid_position(lat, lon)
            if pos is None:                   # garbled / out of range / (0, 0)
                log.debug("ignored unusable position %r, %r", lat, lon)
                return []
            spd = _finite(speed_kmh)
            if spd is None or spd < 0:
                spd = 0.0
            with self._lock:
                return self._update(pos[0], pos[1], spd)
        except Exception:                     # the serial thread must survive
            log.exception("zone engine update failed")
            return []

    def _update(self, lat, lon, speed_kmh):
        events = []
        for zid, z in self._zones.items():
            if not z["active"]:
                # A disabled zone must not leave the vehicle "stuck" inside it.
                if zid in self._inside:
                    events.append(self._event(ZONE_EXIT, "low", z, lat, lon, speed_kmh,
                                              "zone disabled"))
                self._inside.discard(zid)
                self._warned.discard(zid)
                self._overspeed.discard(zid)
                continue

            d = haversine_m(lat, lon, z["lat"], z["lon"])
            r = z["radius_m"]
            inside = zid in self._inside

            if not inside:
                if d <= r:
                    self._inside.add(zid)
                    self._warned.discard(zid)
                    events.append(self._event(
                        ZONE_ENTER, z["severity"], z, lat, lon, speed_kmh,
                        f"{z['name']} - limit {z['speed_limit']} km/h", distance_m=d))
                    inside = True
                elif d <= r + self.prewarn_m:
                    if zid not in self._warned:
                        self._warned.add(zid)
                        events.append(self._event(
                            ZONE_APPROACH, "low", z, lat, lon, speed_kmh,
                            f"Approaching danger zone: {z['name']}", distance_m=d))
                elif zid in self._warned:
                    self._warned.discard(zid)  # left the pre-warn ring: re-arm
            elif d > r + self.hysteresis_m:
                self._inside.discard(zid)
                self._overspeed.discard(zid)
                events.append(self._event(
                    ZONE_EXIT, "low", z, lat, lon, speed_kmh,
                    f"Left {z['name']}", distance_m=d))
                inside = False

            if inside:
                if speed_kmh > z["speed_limit"]:
                    if zid not in self._overspeed:
                        self._overspeed.add(zid)
                        events.append(self._event(
                            OVERSPEED_IN_ZONE, "high", z, lat, lon, speed_kmh,
                            f"{speed_kmh:.1f} km/h in {z['name']} (limit {z['speed_limit']})",
                            distance_m=d))
                else:
                    self._overspeed.discard(zid)
        return events

    @staticmethod
    def _event(type_, severity, zone, lat, lon, speed, details, distance_m=None):
        return {
            "type": type_,
            "severity": severity,
            "zone_id": zone["id"],
            "zone_name": zone["name"],
            "speed_limit": zone["speed_limit"],
            "critical": zone["severity"] == "critical",
            "lat": lat,
            "lon": lon,
            "speed": speed,
            "details": details,
            "distance_m": distance_m,
        }


# --------------------------------------------------- map helpers (used by the simulator and dashboard)
# haversine_distance is the same calculation as haversine_m; kept under the old name for callers.
haversine_distance = haversine_m


def calculate_bearing(lat1, lon1, lat2, lon2):
    """Compass bearing in degrees (0-360) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def move_coordinate(lat, lon, bearing_deg, distance_m):
    """Point reached by travelling distance_m metres from (lat, lon) on the given bearing."""
    ang = distance_m / EARTH_RADIUS_M
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(ang) + math.cos(p1) * math.sin(ang) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(ang) * math.cos(p1),
                         math.cos(ang) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def find_active_danger_zone(lat, lon, zones):
    """Dashboard zones use latitude / longitude / radius_meters keys.
    Returns (zone, distance_m) for the closest zone containing the point, else (None, None)."""
    best, best_dist = None, float("inf")
    for zone in zones:
        try:
            dist = haversine_m(lat, lon, float(zone.get("latitude", 0)), float(zone.get("longitude", 0)))
            if dist <= float(zone.get("radius_meters", 100)) and dist < best_dist:
                best, best_dist = zone, dist
        except (ValueError, TypeError):
            continue
    return best, (best_dist if best else None)
