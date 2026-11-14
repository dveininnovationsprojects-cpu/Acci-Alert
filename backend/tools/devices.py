"""Manage the ESP32 devices on the backend.

  python -m backend.tools.devices list
  python -m backend.tools.devices add --id ACCIALERT_ESP32_02 [--name "Second car"]
  python -m backend.tools.devices pair --id ACCIALERT_ESP32_01 [--mac AA:BB:CC:DD:EE:FF] [--minutes 15]
  python -m backend.tools.devices reset --id ACCIALERT_ESP32_01     (forget the token, pair again)
  python -m backend.tools.devices disable|enable|remove --id ...
  python -m backend.tools.devices commands [--id ...]               (recent queued commands)

"pair" opens a time-limited window. The ESP32 powers up, calls /api/v1/device/register with its MAC address and
receives its token. Give --mac when you know it (printed by the firmware on the serial monitor), so no other
device on the Wi-Fi can claim the slot.
"""

import argparse
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the backend modules import each other by plain name
import config            # noqa: E402
import database          # noqa: E402
import device_registry   # noqa: E402


def show(devices):
    if not devices:
        print("No devices.")
        return
    print(f"{'DEVICE ID':<26}{'STATUS':<10}{'MAC':<19}{'LAST SEEN':<21}PAIRING OPEN UNTIL")
    for d in devices:
        print(f"{d['device_id']:<26}{d['status']:<10}{(d['mac'] or '-'):<19}{(d['last_seen'] or '-'):<21}{d['pairing_until'] or '-'}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["list", "add", "pair", "reset", "disable", "enable", "remove", "commands"])
    parser.add_argument("--id", dest="device_id", default=config.DEVICE_ID)
    parser.add_argument("--name", default="")
    parser.add_argument("--mac")
    parser.add_argument("--minutes", type=int, default=config.DEVICE_PAIRING_MINUTES)
    args = parser.parse_args(argv)

    database.init_db()
    device_registry.ensure_default_device()
    try:
        if args.action == "list":
            show(device_registry.list_devices())
        elif args.action == "add":
            device_registry.add_device(args.device_id, args.name)
            print(f"Device {args.device_id} added. Next: pair --id {args.device_id}")
        elif args.action == "pair":
            until = device_registry.open_pairing(args.device_id, args.mac, args.minutes)
            who = f"the ESP32 with MAC {args.mac.upper()}" if args.mac else "the FIRST ESP32 that asks (no MAC given)"
            print(f"Pairing window for {args.device_id} is open until {until}.")
            print(f"Power up the ESP32 now. It will be claimed by {who}.")
            if not args.mac:
                print("Tip: use --mac so that no other device on the Wi-Fi can take the slot.")
        elif args.action == "reset":
            device_registry.reset_device(args.device_id)
            print(f"Device {args.device_id} reset. Its old token no longer works. Run 'pair' to connect it again.")
        elif args.action in ("disable", "enable"):
            device_registry.set_enabled(args.device_id, args.action == "enable")
            print(f"Device {args.device_id} {args.action}d.")
        elif args.action == "remove":
            device_registry.remove_device(args.device_id)
            print(f"Device {args.device_id} removed.")
        elif args.action == "commands":
            rows = device_registry.list_commands(args.device_id if "--id" in (argv or sys.argv) else None)
            for r in rows:
                print(f"#{r['id']:<5}{r['device_id']:<24}{r['type']:<7}{str(r['value']):<6}{r['status']:<11}tries={r['attempts']} {r['created_at']} {r['result'] or ''}")
            if not rows:
                print("No commands.")
    except ValueError as err:
        print(f"Error: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
