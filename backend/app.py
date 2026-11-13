"""
AcciAlert - Master REST API & Backend Service
Driver Authorisation & Vehicle Safety Gating Subsystem
Module: Full Backend Architecture (Arsal & AcciAlert Software Team)

Endpoints:
  - Static Frontend:      GET  / (Serves frontend/index.html & assets)
  - Health & System:      GET  /api/health
  - Face Verification:    POST /api/verify
  - Liveness Detection:   POST /api/liveness
  - Driver Management:    GET  /api/drivers
                          POST /api/drivers
                          DELETE /api/drivers/{name}
  - Security Audit Trail: GET  /api/logs
                          POST /api/logs
                          DELETE /api/logs
                          GET  /api/logs/stats
                          GET  /api/logs/export/csv
  - ESP32 Serial Bridge:  GET  /api/serial/status
                          POST /api/serial/send
                          GET  /api/serial/history
  - Calibration Settings: GET  /api/config
                          POST /api/config
"""

import base64
import csv
import io
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .db_logger import DatabaseLogger
from .face_detector import FaceDetector
from .face_recognizer import FaceRecognizer
from .liveness import LivenessDetector
from .serial_comm import ESP32SerialCommunicator

# -------------------------------------------------------------
# FastAPI App Initialization
# -------------------------------------------------------------
app = FastAPI(
    title="AcciAlert Vehicle Safety & Driver Authorisation API",
    version="2.5.0",
    description="Backend microservice for Optical Face Recognition, Liveness Anti-Spoofing, Audit Logging, and ESP32 Ignition Gating."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------
# Backend Module Singletons
# -------------------------------------------------------------
detector = FaceDetector()
recognizer = FaceRecognizer(threshold=config.MATCH_THRESHOLD)
liveness_engine = LivenessDetector(detector=detector)
db = DatabaseLogger()
serial_bridge = ESP32SerialCommunicator()

# Helper to decode base64 images
def decode_base64_image(b64_str: str) -> Optional[np.ndarray]:
    try:
        if "," in b64_str:
            b64_str = b64_str.split(",", 1)[1]
        img_bytes = base64.b64decode(b64_str)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        print(f"[!] Base64 decode error: {e}")
        return None


# -------------------------------------------------------------
# Pydantic Schemas
# -------------------------------------------------------------
class VerifyRequest(BaseModel):
    image_base64: str
    liveness_check: bool = True

class LivenessRequest(BaseModel):
    image_base64: str
    reset: bool = False

class LogEventRequest(BaseModel):
    event_type: str = "AUTH_OK"
    severity: str = "INFO"
    driver_name: str = "Unknown"
    match_score: int = 0
    liveness_result: str = "PASSED"
    details: str = ""
    esp32_cmd: str = ""

class SerialSendCommand(BaseModel):
    authorised: bool
    driver: str = "Unknown"

class ConfigUpdateRequest(BaseModel):
    match_threshold: Optional[float] = None
    liveness_enabled: Optional[bool] = None

class DriverEnrolRequest(BaseModel):
    name: str
    role: str = "Authorised Driver"
    samples_base64: List[str] = []


# -------------------------------------------------------------
# System Health & Diagnostic Endpoints
# -------------------------------------------------------------
@app.get("/api/health")
def get_health():
    return {
        "status": "healthy",
        "service": "AcciAlert Driver Authorisation Subsystem",
        "version": "2.5.0",
        "timestamp": datetime.now().isoformat(),
        "model_loaded": recognizer.is_trained,
        "enrolled_drivers": list(recognizer.labels_map.values()),
        "threshold": recognizer.threshold,
        "database_connected": config.DB_PATH.exists(),
        "serial_mode": "HARDWARE" if serial_bridge.is_connected else "MOCK_BRIDGE"
    }


# -------------------------------------------------------------
# Module 1: AI Face Detection & Recognition
# -------------------------------------------------------------
@app.post("/api/verify")
def verify_driver(req: VerifyRequest):
    t_start = time.perf_counter()
    frame = decode_base64_image(req.image_base64)
    if frame is None:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")

    # 1. Optical Face Detection
    det_result = detector.detect_faces(frame)
    if det_result.face_count == 0:
        return {
            "status": "NO_FACE",
            "face_count": 0,
            "is_authorised": False,
            "driver": "Unknown",
            "similarity_pct": 0.0,
            "latency_ms": round((time.perf_counter() - t_start) * 1000.0, 2)
        }

    if det_result.face_count > 1:
        # Multiple faces hazard
        return {
            "status": "MULTIPLE_FACES",
            "face_count": det_result.face_count,
            "is_authorised": False,
            "driver": "Multiple Faces Detected",
            "similarity_pct": 0.0,
            "boxes": [{"x": b[0], "y": b[1], "width": b[2], "height": b[3]} for b in det_result.boxes],
            "latency_ms": round((time.perf_counter() - t_start) * 1000.0, 2)
        }

    # 2. Single Face Recognition
    rec_result = recognizer.predict(det_result.primary_roi)
    primary_box = det_result.primary_box

    # 3. Optional Liveness Verification
    liveness_passed = True
    liveness_status = "PASSED"
    if req.liveness_check:
        passed, l_status = liveness_engine.evaluate_frame(frame, primary_box)
        liveness_passed = passed
        liveness_status = l_status

    final_authorised = bool(rec_result.is_authorised and liveness_passed)
    latency_ms = round((time.perf_counter() - t_start) * 1000.0, 2)

    return {
        "status": "AUTHORISED" if final_authorised else "UNAUTHORISED",
        "face_count": 1,
        "is_authorised": final_authorised,
        "driver": rec_result.driver,
        "similarity_pct": rec_result.similarity_pct,
        "confidence_distance": rec_result.confidence_distance,
        "threshold": recognizer.threshold,
        "liveness": {
            "passed": liveness_passed,
            "status": liveness_status
        },
        "box": {
            "x": int(primary_box[0]),
            "y": int(primary_box[1]),
            "width": int(primary_box[2]),
            "height": int(primary_box[3])
        },
        "latency_ms": latency_ms
    }


# -------------------------------------------------------------
# Module 2: Anti-Spoofing Liveness Evaluation
# -------------------------------------------------------------
@app.post("/api/liveness")
def check_liveness(req: LivenessRequest):
    if req.reset:
        liveness_engine.reset()
        return {"status": "RESET", "passed": False}

    frame = decode_base64_image(req.image_base64)
    if frame is None:
        raise HTTPException(status_code=400, detail="Invalid image")

    det_result = detector.detect_faces(frame)
    if det_result.face_count != 1:
        return {"passed": False, "status": "NO_SINGLE_FACE", "face_count": det_result.face_count}

    passed, status = liveness_engine.evaluate_frame(frame, det_result.primary_box)
    metrics = liveness_engine.get_metrics()
    return {
        "passed": passed,
        "status": status,
        "blink_detected": liveness_engine.blink_count > 0,
        "metrics": metrics
    }


# -------------------------------------------------------------
# Module 3: Driver Enrollment & Profile Registry
# -------------------------------------------------------------
@app.get("/api/drivers")
def list_enrolled_drivers():
    drivers_list = db.get_enrolled_drivers()
    if not drivers_list:
        # Fallback to labels file
        drivers_list = [{"id": k, "name": v, "sample_count": 25, "role": "Registered Fleet Driver"} 
                        for k, v in recognizer.labels_map.items()]
    return drivers_list

@app.post("/api/drivers")
def enrol_new_driver(req: DriverEnrolRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Driver name cannot be empty")

    driver_dir = config.DATASET_DIR / name
    driver_dir.mkdir(parents=True, exist_ok=True)

    saved_count = 0
    for idx, b64_img in enumerate(req.samples_base64, start=1):
        img = decode_base64_image(b64_img)
        if img is not None:
            cv2.imwrite(str(driver_dir / f"{idx:02d}.jpg"), img)
            saved_count += 1

    # Register in DB
    db.register_enrolled_driver(name, saved_count)

    # Retrain model if samples were provided
    if saved_count > 0:
        try:
            from .train_model import train_ai_model
            train_ai_model()
            recognizer.load()
        except Exception as e:
            print(f"[!] Retraining warning: {e}")

    return {
        "success": True,
        "driver_name": name,
        "role": req.role,
        "samples_saved": saved_count,
        "message": f"Successfully enrolled driver {name} with {saved_count} samples"
    }

@app.delete("/api/drivers/{name}")
def delete_driver(name: str):
    success = db.delete_driver(name)
    driver_dir = config.DATASET_DIR / name
    if driver_dir.exists():
        import shutil
        shutil.rmtree(driver_dir, ignore_errors=True)
    return {"success": success, "deleted": name}


# -------------------------------------------------------------
# Module 4: Security & Authorization Audit Trail
# -------------------------------------------------------------
@app.get("/api/logs")
def get_audit_logs(limit: int = 150):
    logs = db.get_recent_events(limit=limit)
    return logs

@app.post("/api/logs")
def create_audit_log(req: LogEventRequest):
    success = db.log_event(
        event_type=req.event_type,
        driver_name=req.driver_name,
        confidence_pct=req.match_score,
        liveness_verified=(req.liveness_result == "PASSED"),
        status=req.event_type,
        details=req.details,
        severity=req.severity,
        esp32_command=req.esp32_cmd
    )
    return {"success": success}

@app.delete("/api/logs")
def clear_all_audit_logs():
    success = db.clear_all_events()
    return {"success": success, "message": "All audit logs cleared"}

@app.get("/api/logs/stats")
def get_audit_statistics():
    stats = db.get_statistics()
    return stats

@app.get("/api/logs/export/csv")
def export_logs_as_csv():
    logs = db.get_recent_events(limit=500)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Timestamp", "EventType", "Severity", "DriverName", "MatchScore", "Liveness", "Details", "ESP32Command"])
    for row in logs:
        writer.writerow([
            row.get("id", ""),
            row.get("timestamp", ""),
            row.get("event_type", ""),
            row.get("severity", ""),
            row.get("driver_name", ""),
            f"{row.get('confidence_pct', 0)}%",
            "PASSED" if row.get("liveness_verified") else "FAILED",
            row.get("details", ""),
            row.get("esp32_command", "")
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=accialert_audit_{int(time.time())}.csv"}
    )


# -------------------------------------------------------------
# Module 5: ESP32 Hardware UART Bridge
# -------------------------------------------------------------
@app.get("/api/serial/status")
def get_serial_status():
    return {
        "connected": serial_bridge.is_connected,
        "port": serial_bridge.port,
        "baudrate": serial_bridge.baudrate,
        "mode": "HARDWARE_UART" if serial_bridge.is_connected else "MOCK_BRIDGE"
    }

@app.post("/api/serial/send")
def send_serial_command(cmd: SerialSendCommand):
    success = serial_bridge.send_auth_decision(cmd.authorised, cmd.driver)
    history = serial_bridge.get_transmission_history()
    last_tx = history[-1] if history else {}
    return {
        "success": success,
        "command_sent": last_tx.get("payload", ""),
        "timestamp": last_tx.get("timestamp", "")
    }

@app.get("/api/serial/history")
def get_serial_history(limit: int = 50):
    return serial_bridge.get_transmission_history()[-limit:]


# -------------------------------------------------------------
# Module 6: System Calibration Settings
# -------------------------------------------------------------
@app.get("/api/config")
def get_calibration_config():
    return {
        "match_threshold": recognizer.threshold,
        "liveness_enabled": config.ENABLE_LIVENESS_BY_DEFAULT,
        "target_accuracy_pct": config.TARGET_ACCURACY_PCT,
        "max_latency_seconds": config.MAX_LATENCY_SECONDS,
        "serial_port": config.SERIAL_PORT,
        "serial_baudrate": config.SERIAL_BAUDRATE,
        "face_target_size": config.FACE_TARGET_SIZE,
        "lbph_params": {
            "radius": config.LBPH_RADIUS,
            "neighbors": config.LBPH_NEIGHBORS,
            "grid_x": config.LBPH_GRID_X,
            "grid_y": config.LBPH_GRID_Y
        }
    }

@app.post("/api/config")
def update_calibration_config(req: ConfigUpdateRequest):
    if req.match_threshold is not None:
        recognizer.threshold = float(req.match_threshold)
    return {
        "success": True,
        "match_threshold": recognizer.threshold,
        "liveness_enabled": req.liveness_enabled if req.liveness_enabled is not None else config.ENABLE_LIVENESS_BY_DEFAULT
    }


# -------------------------------------------------------------
# Static Frontend Mount (Serves frontend/ on port 5173)
# -------------------------------------------------------------
FRONTEND_DIR = config.BASE_DIR / "frontend"
if not FRONTEND_DIR.exists():
    FRONTEND_DIR = config.BASE_DIR / "web"

if FRONTEND_DIR.exists():
    # Mount frontend static files
    app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")

    @app.get("/", response_class=HTMLResponse)
    async def serve_index():
        index_file = FRONTEND_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return HTMLResponse("<h1>AcciAlert Frontend Initialising...</h1>")

    @app.get("/index.html", response_class=HTMLResponse)
    async def serve_index_html():
        return FileResponse(str(FRONTEND_DIR / "index.html"))


# -------------------------------------------------------------
# CLI Entrypoint: Run backend directly
# -------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    print("=" * 65)
    print("AcciAlert - Starting Full-Featured REST API & Web Server")
    print(f"Backend API Docs: http://localhost:5173/docs")
    print(f"Frontend Cockpit: http://localhost:5173/")
    print("=" * 65)
    uvicorn.run("backend.app:app", host="0.0.0.0", port=5173, reload=False)
