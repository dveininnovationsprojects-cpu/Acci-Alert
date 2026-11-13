"""
AcciAlert - Configuration Module
Smart Driver Safety and Accident Prevention System
Author: Muniyappan
"""

import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Database Configuration
DATABASE_PATH = os.path.join(DATA_DIR, "accialert.db")

# Flask & SocketIO Configuration
SECRET_KEY = os.environ.get("SECRET_KEY", "accialert-secret-key-2026-muniyappan")
HOST = "0.0.0.0"
PORT = 5000
DEBUG = False

# Telemetry & Sampling
TELEMETRY_INTERVAL = 1.0  # seconds between telemetry broadcasts
CHART_HISTORY_SECONDS = 60  # seconds of rolling chart history

# Serial Communication Defaults
DEFAULT_SERIAL_BAUD = 115200
DEFAULT_SERIAL_PORT = "COM3"
SERIAL_TIMEOUT = 1.0  # seconds

# Safety & Sensor Alert Thresholds
THRESHOLDS = {
    "speed": {
        "warning": 60.0,       # km/h
        "critical": 80.0,      # km/h (overspeed alert)
    },
    "alcohol": {
        "caution": 350,        # ADC reading
        "danger": 600,         # ADC reading (alcohol alert)
        "max_adc": 1023
    },
    "obstacle": {
        "caution": 100,        # cm
        "danger": 40,          # cm (obstacle alert)
    },
    "tilt": {
        "warning": 20.0,       # degrees
        "accident": 45.0,      # degrees (accident / rollover)
    },
    "impact_acceleration": 2.5  # g (vector magnitude sqrt(ax^2+ay^2+az^2))
}

# GPS Default Coordinates (Chennai, Tamil Nadu - Anna Salai / Guindy Corridor)
DEFAULT_LATITUDE = 13.0827
DEFAULT_LONGITUDE = 80.2707

# Initial Seed Danger Zones
INITIAL_DANGER_ZONES = [
    {
        "code": "Z01",
        "name": "Anna Salai School Zone",
        "latitude": 13.0827,
        "longitude": 80.2707,
        "radius_meters": 150.0,
        "speed_limit": 30.0,
        "severity": "HIGH",
        "details": "School zone active with frequent pedestrian crossings."
    },
    {
        "code": "Z02",
        "name": "Guindy Sharp Curve",
        "latitude": 13.0067,
        "longitude": 80.2023,
        "radius_meters": 200.0,
        "speed_limit": 40.0,
        "severity": "CRITICAL",
        "details": "Accident-prone hairpin turn with high historical collision rate."
    },
    {
        "code": "Z03",
        "name": "Koyambedu Junction",
        "latitude": 13.0694,
        "longitude": 80.1948,
        "radius_meters": 250.0,
        "speed_limit": 35.0,
        "severity": "MEDIUM",
        "details": "Heavy bus traffic and pedestrian crosswalks."
    },
    {
        "code": "Z04",
        "name": "Marina Beach Promenade",
        "latitude": 13.0500,
        "longitude": 80.2824,
        "radius_meters": 300.0,
        "speed_limit": 40.0,
        "severity": "LOW",
        "details": "High tourist density and low visibility evenings."
    }
]

# Initial Seed Drivers
INITIAL_DRIVERS = [
    {
        "name": "Muniyappan",
        "license_number": "TN-02-2023-009871",
        "authorized": 1,
        "notes": "Primary Certified Fleet Driver"
    },
    {
        "name": "Karthik Raja",
        "license_number": "TN-07-2021-004312",
        "authorized": 1,
        "notes": "Relief Driver - Night Shift"
    },
    {
        "name": "Unknown Driver",
        "license_number": "UNREGISTERED",
        "authorized": 0,
        "notes": "Guest or Unauthorized Operator"
    }
]

