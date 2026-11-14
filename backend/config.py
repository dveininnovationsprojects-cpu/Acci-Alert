"""Settings for the whole backend: paths, server port, serial port, limits and starting data.
"""

import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent  # Project root
BACKEND_DIR = Path(__file__).resolve().parent      # backend/
DATA_DIR = BACKEND_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

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
DEFAULT_FACE_ENGINE = "auto"    # "auto" = SFace when models and enrolled drivers exist, else LBPH

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

# Database Configuration
DATABASE_PATH = str(DATA_DIR / "accialert.db")
DB_PATH = Path(DATABASE_PATH)

# Server
SECRET_KEY = os.environ.get("SECRET_KEY", "accialert-dev-key")
HOST = "0.0.0.0"
PORT = 5173
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
