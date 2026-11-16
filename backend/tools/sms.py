"""Set up and test the SMS alerts (Circuit Digest Cloud, free SMS API for India). The API key stays on this laptop
(backend/data/sms_settings.json, never committed) and is never printed.

  python -m backend.tools.sms setup      asks for the API key (hidden), the template ID and the phone numbers
  python -m backend.tools.sms status     is it set up, who gets the texts (numbers hidden), limits, how many were sent
  python -m backend.tools.sms test       sends one test text to the recipients (counts toward the limits)
  python -m backend.tools.sms disable    stop sending (enable turns it on again)
  python -m backend.tools.sms log        the last messages and whether the service accepted them

Before you start (circuitdigest.cloud): create the account, add and verify the recipient mobile numbers on the
dashboard, and pick a template. A template is a fixed message with two slots, var1 and var2. By default this
software puts the alert name in var1 (Accident, Alcohol, UnauthorisedDriver, UnauthorisedLogin) and the driver's name
in var2. Change that with "values" (alert, driver, speed or time) in backend/data/sms_settings.json.
"""

import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # the backend modules import each other by plain name
import config      # noqa: E402
import database    # noqa: E402
import sms_alert   # noqa: E402


def read_file() -> dict:
    try:
        data = json.loads(config.SMS_SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_file(data: dict) -> None:
    config.SMS_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.SMS_SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def show_status() -> None:
    s = sms_alert.status()
    print(f"Provider:    {s['provider']}")
    print(f"Set up:      {'yes' if s['configured'] else 'NO'}")
    print(f"Switched on: {'yes' if s['enabled'] else 'no'}")
    print(f"Template ID: {s['template_id'] or '-'}   (var1 = {s['values']['var1']}, var2 = {s['values']['var2']})")
    print(f"Recipients:  {', '.join(s['recipients']) or '-'}")
    for problem in s["problems"]:
        print(f"  problem: {problem}")
    for warning in s["warnings"]:
        print(f"  note: {warning}")
    lim = s["limits"]
    print(f"Limits:      same alert once per {lim['cooldown_s']} s, {lim['max_per_hour']}/hour, {lim['max_per_day'] or 'no limit'}/day, {lim['max_per_month']}/month")
    print(f"Sent:        {s['sent_last_hour']} in the last hour, {s['sent_last_day']} in the last day, {s['sent_last_month']} in the last 30 days")
    print(f"Texts for:   {', '.join(s['alert_types'])}")


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    action = args[0] if args else "status"
    database.init_db()

    if action == "setup":
        print("SMS setup (Circuit Digest Cloud). Find the values on your circuitdigest.cloud dashboard.")
        data = read_file()
        data["provider"] = "circuitdigest"
        key = getpass.getpass("API key (hidden, press Enter to keep the saved one): ").strip()
        if key:
            data["api_key"] = key
        data["template_id"] = input("Template ID (a number, like 103): ").strip()
        data["to_numbers"] = [n.strip() for n in input("Mobile numbers to text, separated by commas (10 digits, verified on the dashboard): ").split(",") if n.strip()]
        data.setdefault("var1", "alert")
        data.setdefault("var2", "driver")
        data["enabled"] = True
        write_file(data)
        print()
        show_status()
        if sms_alert.load_settings()["configured"]:
            print("\nSaved. Send a test with:  python -m backend.tools.sms test")
            return 0
        print("\nSaved, but something above needs fixing. Run setup again.")
        return 1
    if action == "status":
        show_status()
        return 0
    if action == "test":
        sms_alert.RUN_IN_BACKGROUND = False        # wait for the service's answer and show it
        result = sms_alert.send_test()
        print(result["message"])
        for row in result.get("results", []):
            print(f"  {row['recipient']}: {row['status']} {row['detail'] or ''}")
        return 0 if result["sent"] else 1
    if action in ("enable", "disable"):
        data = read_file()
        data["enabled"] = action == "enable"
        write_file(data)
        print(f"SMS alerts {action}d.")
        return 0
    if action == "log":
        rows = database.get_sms_log(20)
        for r in rows:
            print(f"{r['timestamp']}  {r['alert_type']:<20}{r['recipient']:<16}{r['status']:<8}{r['detail'] or ''}")
        if not rows:
            print("No messages yet.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
