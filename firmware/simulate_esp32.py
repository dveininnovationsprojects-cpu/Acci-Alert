"""Pretend to be the ESP32 firmware (same requests, same JSON, same order) against a running backend.

Use it to check the laptop side BEFORE the real board is connected:

    python backend/app.py                                              (terminal 1)
    python -m backend.tools.devices pair --mac AA:BB:CC:DD:EE:01       (terminal 2)
    python firmware/simulate_esp32.py http://127.0.0.1:5173 AA:BB:CC:DD:EE:01

The dashboard then switches to "Live ESP32 data". Press Ctrl+C to stop.
Options: --fix (send a GPS fix near Chennai), --alcohol, --impact, --count N (stop after N seconds)
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request


def call(method, url, body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("server")
    ap.add_argument("mac")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--alcohol", action="store_true")
    ap.add_argument("--impact", action="store_true")
    ap.add_argument("--count", type=int, default=0)
    a = ap.parse_args()
    base = a.server.rstrip("/") + "/api/v1"

    code, r = call("POST", base + "/device/register", {"mac": a.mac, "firmware": "2.0.0"})
    if code != 201:
        print("register failed:", code, r)
        print("Open the pairing window first: python -m backend.tools.devices pair --mac", a.mac)
        return 1
    token = r["token"]
    print("paired as", r["device_id"])

    done = set()
    n = 0
    while True:
        n += 1
        # 1. commands first (this is where the firmware takes its clock from)
        code, r = call("GET", base + "/device/commands?limit=10", token=token)
        if code != 200:
            print("commands failed:", code, r)
            return 1
        server_time = r["server_time"]
        assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", server_time), server_time
        locked = r["state"]["auth"] == 0
        acks = []
        for c in r["commands"]:
            if c["id"] not in done:
                done.add(c["id"])
                print("  command", c["type"], c["value"] if c["type"] != "ZONES" else "zones=%d" % c["value"]["count"])
            acks.append({"id": c["id"], "result": "executed"})
        if acks:
            code, rr = call("POST", base + "/device/commands/ack", {"acks": acks}, token)
            assert code == 200, (code, rr)

        # 2. one reading, built exactly like sendTelemetry() in the firmware
        body = {
            "timestamp": server_time,
            "sensors": {
                "alcohol_adc": 2600 if a.alcohol else 1200,
                "distance_cm": 500.0,
                "accelerometer": {"x": 0.12, "y": 0.08, "z": 9.72} if not a.impact else {"x": 30.0, "y": 5.0, "z": 20.0},
                "gyroscope": {"x": 0.02, "y": 0.01, "z": 0.03},
                "speed_rpm": 85.0 + (n % 5),
                "ir_pulse_count": 20 * n,
                "gps": {"latitude": 13.0827 if a.fix else 0.0, "longitude": 80.2707 if a.fix else 0.0,
                        "fix_valid": a.fix, "satellites": 7 if a.fix else 0},
            },
            "alerts": {"alcohol": a.alcohol, "overspeed": False, "accident": a.impact, "obstacle": False, "buzzer": a.alcohol or a.impact},
        }
        code, rr = call("POST", base + "/telemetry", body, token)
        print("%s  locked=%s  telemetry -> %s %s" % (server_time, locked, code, rr.get("status") or rr))
        if code != 201:
            print(json.dumps(rr, indent=1))
            return 1
        if a.count and n >= a.count:
            return 0
        time.sleep(1)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
