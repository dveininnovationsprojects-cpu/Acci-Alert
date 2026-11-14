"""Build backend/zones_tamilnadu.json (default danger zones) from the Tamil Nadu accident CSV.

Run:  python -m backend.tools.build_tn_zones path/to/updated_dataset.csv

What it does
  * one zone per district centre; a city row and its district row with the same place are merged
    (the deaths are added together) so two circles never sit on top of each other
  * fixes district coordinates that are wrong in the CSV (see FIXED_COORDS)
  * severity comes from the 2021 deaths, the speed limit from the place type
"""

import csv
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "zones_tamilnadu.json"

# The CSV has wrong or rounded coordinates for these places (Salem is in Oregon, USA, in the file).
FIXED_COORDS = {
    "salem": (11.6643, 78.1460),
    "thiruvannamalai": (12.2253, 79.0747),
    "thirupathur": (12.4955, 78.5729),
    "pudukkottai": (10.3833, 78.8001),
    "ramanathapuram": (9.3639, 78.8395),
    "the nilgiris": (11.4064, 76.6932),
    "dindigul": (10.3624, 77.9695),
}

RADIUS_M = 5000
CITY_LIMIT = 40      # km/h inside a city
DISTRICT_LIMIT = 50  # km/h on district roads


def severity_for(deaths_2021):
    if deaths_2021 >= 500:
        return "critical"
    if deaths_2021 >= 350:
        return "high"
    if deaths_2021 >= 200:
        return "medium"
    return "low"


def key_of(row):
    return re.sub(r"\s+", " ", re.sub(r"\bcity\b", "", row["district"], flags=re.I)).strip().lower()


def build(csv_path):
    places = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            key = key_of(row)
            is_city = "city" in row["district"].lower()
            place = places.setdefault(key, {"name": re.sub(r"\s+", " ", re.sub(r"\bcity\b", "", row["district"], flags=re.I)).strip(),
                                            "deaths": 0, "city": False, "lat": None, "lon": None})
            place["deaths"] += int(row["2021_fatal"])
            place["city"] = place["city"] or is_city
            if place["lat"] is None or is_city:      # the city row has the town-centre coordinates
                place["lat"], place["lon"] = float(row["latitude"]), float(row["longitude"])
    zones = []
    for n, (key, p) in enumerate(sorted(places.items(), key=lambda kv: -kv[1]["deaths"]), start=1):
        lat, lon = FIXED_COORDS.get(key, (p["lat"], p["lon"]))
        zones.append({
            "id": f"TN{n:02d}",
            "name": f"{p['name']} - {p['deaths']} road deaths (2021)",
            "type": "blackspot",
            "lat": round(lat, 6), "lon": round(lon, 6),
            "radius_m": RADIUS_M,
            "speed_limit": CITY_LIMIT if p["city"] else DISTRICT_LIMIT,
            "severity": severity_for(p["deaths"]),
            "active": 1,
        })
    return zones


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python -m backend.tools.build_tn_zones path/to/updated_dataset.csv")
    zones = build(sys.argv[1])
    OUT.write_text(json.dumps({
        "_note": "Default Tamil Nadu accident zones, built from the district road-accident CSV (2020-2021). "
                 "Circles are around the district centre, so they mark an area to be careful in, not one junction.",
        "zones": zones}, indent=2), encoding="utf-8")
    print(f"Wrote {len(zones)} zones to {OUT}")
