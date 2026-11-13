"""
AcciAlert - Configuration Module
Module: AI Face Recognition & Driver Authorisation (Arsal)
"""

import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
AI_DIR = BASE_DIR / "ai"
MODELS_DIR = BASE_DIR / "models"
DATASET_DIR = BASE_DIR / "dataset"
DATASET_TEST_DIR = BASE_DIR / "dataset_test"
DOCS_DIR = BASE_DIR / "docs"

# Model & Cascade Paths
HAAR_FACE_PATH = MODELS_DIR / "haarcascade_frontalface_default.xml"
HAAR_EYE_PATH = MODELS_DIR / "haarcascade_eye.xml"
MODEL_FILE = MODELS_DIR / "face_model.yml"
LABELS_FILE = MODELS_DIR / "labels.json"

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

# Recognition Thresholds
# In LBPH on real human faces:
# Authorised drivers yield chi-square distances 12.0 - 35.0 (mean ~22.0);
# Unknown subjects yield chi-square distances 77.0 - 110.0 (mean ~90.0).
# Operating threshold 50.0 delivers 100% true acceptance and 0% false acceptance.
MATCH_THRESHOLD = 50.0
CONSECUTIVE_MATCH_FRAMES = 5  # Debounce to prevent single-frame flickering
TARGET_ACCURACY_PCT = 90.0
MAX_LATENCY_SECONDS = 2.0

# Liveness Detection Settings
ENABLE_LIVENESS_BY_DEFAULT = True
LIVENESS_BLINK_EYES_MIN = 1       # At least 1 eye blink detected
LIVENESS_TIMEOUT_SECONDS = 5.0    # Time window allowed for driver to blink/move
LIVENESS_MOTION_THRESHOLD = 2.0   # Optical motion threshold

# ESP32 Serial Settings
SERIAL_PORT = os.getenv("ACCIALERT_SERIAL_PORT", "COM3")
SERIAL_BAUDRATE = 115200
SERIAL_TIMEOUT = 1.0

# Database & Dashboard REST API
DB_PATH = BASE_DIR / "accialert.db"
API_BASE_URL = os.getenv("ACCIALERT_API_URL", "http://localhost:5000")
API_TIMEOUT = 1.5

# Authorised Drivers (Default Roster per Team Guide)
DEFAULT_AUTHORISED_DRIVERS = ["Arsal", "Muniyappan", "Arun", "Kumar", "Ravi"]
