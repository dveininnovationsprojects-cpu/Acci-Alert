"""Settings for the whole backend: paths, server port, serial port, limits and starting data.
"""

import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent  # Project root
BACKEND_DIR = Path(__file__).resolve().parent      # backend/
DATA_DIR = Path(os.environ.get("ACCIALERT_DATA_DIR") or BACKEND_DIR / "data")   # tests point this at a temp folder
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_DIR = BASE_DIR / "database"
DATABASE_DIR.mkdir(parents=True, exist_ok=True)

AI_DIR = BACKEND_DIR
MODELS_DIR = BACKEND_DIR / "models"
DATASET_DIR = BACKEND_DIR / "dataset"
DATASET_TEST_DIR = BACKEND_DIR / "dataset_test"
DOCS_DIR = BASE_DIR / "docs"
FRONTEND_DIR = BASE_DIR / "frontend"

# Model & Cascade Paths (Arsal - Vision AI)
HAAR_FACE_PATH = MODELS_DIR / "haarcascade_frontalface_default.xml"
HAAR_EYE_PATH = MODELS_DIR / "haarcascade_eye.xml"
MODEL_FILE = MODELS_DIR / "face_model.yml"
LABELS_FILE = MODELS_DIR / "labels.json"

# Pretrained OpenCV face models (download with: python -m backend.tools.get_models)
YUNET_FILE = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
SFACE_FILE = MODELS_DIR / "face_recognition_sface_2021dec.onnx"
FACE_GALLERY_FILE = DATA_DIR / "face_gallery.npz"   # face signatures saved at enrolment (not in Git)

# Face Detection Settings
FACE_MIN_SIZE = (50, 50)
FACE_SCALE_FACTOR = 1.05
FACE_MIN_NEIGHBORS = 2
FACE_TARGET_SIZE = (150, 150)  # Standardized ROI for LBPH

# Face Recognition Settings (LBPH)
LBPH_RADIUS = 1
LBPH_NEIGHBORS = 8
LBPH_GRID_X = 8
LBPH_GRID_Y = 8

# SFace settings: cosine similarity, higher is a better match (OpenCV suggests 0.363)
SFACE_THRESHOLD = 0.40
SFACE_MIN_DETECT_SCORE = 0.80   # ignore weak face detections
SFACE_MIN_FACE_PX = 80          # ignore faces smaller than this many pixels wide
ENROL_MIN_FRAMES = 10           # live enrolment needs at least this many usable frames
DEFAULT_FACE_ENGINE = "auto"    # "auto" = SFace when its models are downloaded, else the old LBPH model

# Recognition Thresholds
MATCH_THRESHOLD = 50.0
CONSECUTIVE_MATCH_FRAMES = 5
TARGET_ACCURACY_PCT = 90.0
MAX_LATENCY_SECONDS = 2.0

# Liveness Detection Settings
ENABLE_LIVENESS_BY_DEFAULT = True
LIVENESS_BLINK_EYES_MIN = 1
LIVENESS_TIMEOUT_SECONDS = 5.0
LIVENESS_MOTION_THRESHOLD = 2.0
LIVENESS_MOVE_FRACTION = 0.015   # the face must move about 1.5% of the frame width to count as a live person
LIVENESS_WINDOW_SECONDS = 6.0    # movement is measured over the last few seconds
LIVENESS_HOLD_SECONDS = 8.0      # a passed check stays valid this long while the face is in view

# Head-turn check for the face login and for enrolment: turn RIGHT, then LEFT, then look STRAIGHT (a photo cannot).
# Each login uses a one-time request, so a login cannot be replayed.
AUTH_REQUIRE_CHALLENGE = True
CHALLENGE_TTL_SECONDS = 60       # a request is valid this long, and only once
CHALLENGE_MAX_OPEN = 50          # unused requests kept at the same time
ENROL_REQUIRE_TURN = os.environ.get("ACCIALERT_ENROL_TURN", "on").lower() != "off"     # "off" only for the automatic tests
HEAD_TURN_MIN = 0.18             # the nose must move this far (as a part of the eye distance) from "straight" to count as a turn
HEAD_STRAIGHT_MAX = 0.12         # and the head is "straight" again when it is back within this
HEAD_FRONTAL_MAX = 0.14          # pictures within this of straight are the ones used to recognise the face
HEAD_BASE_PICTURES = 4           # the first pictures show the driver looking straight: that is the starting point
HEAD_RUN = 2                     # a turn counts after this many pictures in a row
HEAD_MIN_PICTURES = 12           # at least this many pictures with a usable face

# Database Configuration
if os.environ.get("ACCIALERT_DATA_DIR"):
    DATABASE_PATH = str(DATA_DIR / "accialert.db")
else:
    DATABASE_PATH = str(DATABASE_DIR / "accialert.db") if (DATABASE_DIR / "accialert.db").exists() else str(DATA_DIR / "accialert.db")
DB_PATH = Path(DATABASE_PATH)

# Server
SECRET_KEY = os.environ.get("SECRET_KEY", "accialert-dev-key")
HOST = "0.0.0.0"
PORT = int(os.environ.get("ACCIALERT_PORT", 5173))   # the demo port; override only for a second test copy
DEBUG = False

# Telemetry & Sampling
TELEMETRY_INTERVAL = 1.0  # seconds between telemetry broadcasts
CHART_HISTORY_SECONDS = 60  # seconds of rolling chart history

# Serial Communication Defaults (ESP32 UART 115200 baud)
DEFAULT_SERIAL_BAUD = 115200
DEFAULT_SERIAL_PORT = "COM3"
SERIAL_PORT = os.getenv("ACCIALERT_SERIAL_PORT", DEFAULT_SERIAL_PORT)
SERIAL_BAUDRATE = 115200
SERIAL_TIMEOUT = 1.0

# REST API Settings
API_BASE_URL = os.getenv("ACCIALERT_API_URL", f"http://localhost:{PORT}")
API_TIMEOUT = 1.5

# Safety & Sensor Alert Thresholds (Fleet Telemetry)
THRESHOLDS = {
    "speed": {
        "warning": 60.0,       # km/h
        "critical": 80.0,      # km/h (overspeed alert)
    },
    "alcohol": {
        "caution": 180,        # ADC reading (0-1023 scale)
        "danger": 220,         # ADC reading (alcohol alert) = firmware trigger: clean-air baseline + 350 on the 12-bit ADC
        "max_adc": 1023
    },
    "obstacle": {
        "caution": 40,         # cm
        "danger": 25,          # cm (obstacle alert) = firmware warning distance
    },
    "tilt": {
        "warning": 115.0,      # degrees
        "accident": 130.0,     # degrees (accident / rollover); this car's sensor board reads about 100 degrees when it stands normally
    },
    "impact_acceleration": 4.0  # g (vector magnitude sqrt(ax^2+ay^2+az^2)); 2.5 g was set off by simply handling the car
}

# ESP32 Wi-Fi telemetry API (POST /api/v1/telemetry)
DEVICE_API_KEY_FILE = DATA_DIR / "device_api_key.txt"  # created on first start if no env key is set
DEVICE_API_KEY_ENV = "ACCIALERT_DEVICE_API_KEY"
DEVICE_MAX_PAYLOAD_BYTES = 4096
DEVICE_TELEMETRY_RETENTION_HOURS = 24      # rolling history kept in the database
WHEEL_CIRCUMFERENCE_M = 0.204              # miniature car wheel (about 65 mm); speed_rpm -> km/h
DEVICE_ADC_MAX = 4095                      # ESP32 ADC is 12-bit; the alarm limits above are on a 0-1023 scale

# Device identity and commands (the firmware holds no key and no device id in its source code)
DEVICE_ID = os.environ.get("ACCIALERT_DEVICE_ID", "ACCIALERT_ESP32_01")   # the device created on first start
DEVICE_PAIRING_MINUTES = 15                # how long a pairing window stays open
DEVICE_COMMAND_TTL_S = 60                  # a command nobody collected in this time is dropped
DEVICE_COMMAND_REDELIVER_S = 5             # an unacknowledged command is offered again after this many seconds
DEVICE_COMMAND_MAX_ATTEMPTS = 5            # then it is marked failed
# Admin panel (enrol drivers, danger zones, controls, event history)
ADMIN_PASSWORD = os.environ.get("ACCIALERT_ADMIN_PASSWORD", "1234")
ADMIN_AUTH_REQUIRED = os.environ.get("ACCIALERT_ADMIN_AUTH", "on").lower() != "off"     # "off" only for the automatic tests
ADMIN_SESSION_TTL_S = 1800        # the admin is logged out after 30 minutes
ADMIN_MAX_WRONG = 5               # wrong passwords from one address ...
ADMIN_LOCKOUT_S = 60              # ... lock that address for this long

DEVICE_MAX_ZONES = 100                     # a ZONES command carries at most this many zones
DEVICE_ALLOW_LEGACY_KEY = os.environ.get("ACCIALERT_ALLOW_LEGACY_KEY", "1") != "0"   # shared key, telemetry only

# SMS alerts through Circuit Digest Cloud. The account details are NOT written here: they come from the environment
# (ACCIALERT_SMS_API_KEY, ACCIALERT_SMS_TEMPLATE_ID, ACCIALERT_SMS_TO)
# or from backend/data/sms_settings.json, which `python -m backend.tools.sms setup` creates (never committed).
SMS_SETTINGS_FILE = DATA_DIR / "sms_settings.json"
SMS_ALERT_TYPES = ("ACCIDENT", "ALCOHOL DETECTED", "UNAUTHORISED DRIVER", "UNAUTHORISED LOGIN", "SOS")
SMS_SOS_COOLDOWN_S = 30     # the SOS button may text again after this long (it ignores the hourly and daily limits, not the monthly one)
SMS_COOLDOWN_S = 300          # the same alert type is texted at most once in this time
SMS_MAX_PER_HOUR = 6          # messages (each recipient counts) per hour, all types together
SMS_MAX_PER_DAY = 20          # and per day
SMS_MAX_PER_MONTH = 90        # the free Circuit Digest plan allows about 100 SMS a month
SMS_MAX_RECIPIENTS = 5        # numbers that get the text
SMS_TIMEOUT_S = 8

# Laptop location: shown as the car position while the GPS module has no fix
LAPTOP_LOCATION_MAX_AGE_S = 120        # a location older than this is not used
ZONE_ALERT_DEFAULT_M = 1000            # a danger zone is announced (notification, map) when the car comes this close to it
ZONE_ALERT_MIN_M = 50
ZONE_ALERT_MAX_M = 5000
ZONE_ALERT_PRESETS_M = (100, 250, 500, 1000, 2000, 5000)
LAPTOP_ZONE_MAX_ACCURACY_M = 200       # danger zones use the laptop location only when it is this accurate

# GPS Default Coordinates (Chennai Corridor)
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
        "name": "Arsal",
        "license_number": "TN-09-2024-001290",
        "authorized": 1,
        "notes": "AI Vision Lead & Verified Driver"
    },
    {
        "name": "Arun",
        "license_number": "TN-01-2022-005612",
        "authorized": 1,
        "notes": "Fleet Driver Roster"
    },
    {
        "name": "Kumar",
        "license_number": "TN-03-2021-008945",
        "authorized": 1,
        "notes": "Fleet Driver Roster"
    },
    {
        "name": "Ravi",
        "license_number": "TN-05-2020-003319",
        "authorized": 1,
        "notes": "Fleet Driver Roster"
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

DEFAULT_AUTHORISED_DRIVERS = ["Arsal", "Muniyappan", "Arun", "Kumar", "Ravi"]
