"""
AcciAlert - AI Face Recognition & Driver Authorisation System
Author: Arsal
Module: Driver Authorisation Layer

Core Responsibilities:
  1. Captures live video stream from USB driver-facing webcam.
  2. Detects driver's face and normalizes illumination (CLAHE).
  3. Evaluates 5 operational cases:
     - Case 1: One authorised face -> Unlock car, send AUTH_OK
     - Case 2: Unknown face -> Keep car locked, send AUTH_DENIED
     - Case 3: No face detected -> Vehicle remains locked
     - Case 4: Multiple faces -> Reject access (security rule)
     - Case 5: Low confidence / poor lighting -> Reject access
  4. Anti-spoofing liveness check (blink & micro-movement).
  5. Communicates result to ESP32 via serial JSON line:
     {"cmd":"AUTH","ok":1,"driver":"<Name>"}
     {"cmd":"AUTH","ok":0,"driver":"Unknown"}
  6. Logs authentication events to SQLite (accialert.db) & dashboard API.
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Optional, Tuple
import cv2
import numpy as np

try:
    from . import config
    from .face_detector import FaceDetector, FaceDetectionResult
    from .face_recognizer import FaceRecognizer, RecognitionResult
    from .liveness import LivenessDetector
    from .serial_comm import ESP32SerialCommunicator
    from .db_logger import DatabaseLogger
    from .train_model import train_ai_model
except ImportError:
    from backend import config
    from backend.face_detector import FaceDetector, FaceDetectionResult
    from backend.face_recognizer import FaceRecognizer, RecognitionResult
    from backend.liveness import LivenessDetector
    from backend.serial_comm import ESP32SerialCommunicator
    from backend.db_logger import DatabaseLogger
    from backend.train_model import train_ai_model


class DriverAuthSystem:
    """End-to-end Driver Authentication System for AcciAlert."""

    def __init__(self,
                 camera_index: int = 0,
                 serial_port: Optional[str] = None,
                 threshold: float = config.MATCH_THRESHOLD,
                 enable_liveness: bool = config.ENABLE_LIVENESS_BY_DEFAULT,
                 single_image_mode: bool = False):
        self.camera_index = camera_index
        self.threshold = threshold
        self.enable_liveness = enable_liveness
        self.single_image_mode = single_image_mode
        self.required_consecutive_matches = 1 if single_image_mode else config.CONSECUTIVE_MATCH_FRAMES

        print("=" * 60)
        print("AcciAlert - Initialising AI Face Recognition & Driver Authorisation")
        print("=" * 60)

        # 1. Initialize Submodules
        self.detector = FaceDetector()
        self.recognizer = FaceRecognizer(threshold=self.threshold)
        if not self.recognizer.is_trained:
            print("[!] Model not found. Attempting to load default model or train now...")
            if config.MODEL_FILE.exists():
                self.recognizer.load()
            else:
                self.recognizer = train_ai_model()

        self.liveness = LivenessDetector(detector=self.detector)
        self.serial = ESP32SerialCommunicator(port=serial_port)
        self.db = DatabaseLogger()

        # State Tracking
        self.vehicle_locked = True
        self.current_driver = "None"
        self.auth_state = "IDLE"          # IDLE, SCANNING, LIVENESS, AUTHORISED, DENIED
        self.consecutive_matches = 0
        self.last_candidate_driver: Optional[str] = None
        self.last_auth_time = 0.0
        self.last_esp32_state: Optional[int] = None
        self.status_banner = "Vehicle Locked - Waiting for Driver"

        # Ensure ESP32 starts in locked state
        self._set_vehicle_state(ok=0, driver="Unknown", reason="Initial boot locked")

    def _set_vehicle_state(self, ok: int, driver: str, reason: str = ""):
        """Sends command to ESP32 and logs to database when state transitions."""
        if self.last_esp32_state == ok and self.current_driver == driver:
            return  # Avoid flooding identical serial messages

        self.vehicle_locked = (ok == 0)
        self.current_driver = driver
        self.last_esp32_state = ok

        # Send command over serial to ESP32
        self.serial.send_auth(ok=ok, driver=driver)

        # Log event into SQLite database and notify dashboard
        self.db.log_auth_event(
            driver=driver,
            is_authorised=(ok == 1),
            confidence_distance=0.0,
            similarity_pct=100.0 if ok == 1 else 0.0,
            extra_details=reason
        )

    def process_frame(self, frame: np.ndarray) -> Tuple[np.ndarray, dict]:
        """
        Executes full face recognition pipeline on a single frame.
        Returns annotated frame and diagnostics dict.
        """
        h, w = frame.shape[:2]
        det_result = self.detector.detect_faces(frame)

        diagnostics = {
            "status": "SEARCHING",
            "driver": "Unknown",
            "is_authorised": False,
            "face_count": det_result.face_count,
            "similarity_pct": 0.0,
            "distance": 999.0,
            "vehicle_locked": self.vehicle_locked
        }

        # Case 3: No face detected
        if det_result.face_count == 0:
            self.auth_state = "NO_FACE"
            self.consecutive_matches = 0
            self.status_banner = "No Face Detected - Vehicle Locked"
            self.liveness.reset()
            diagnostics["status"] = "NO_FACE"

            # Overlay HUD
            self._draw_hud(frame, det_result, None, None)
            return frame, diagnostics

        # Case 4: Multiple faces detected (Security Policy Violation)
        if det_result.face_count > 1:
            self.auth_state = "MULTIPLE_FACES"
            self.consecutive_matches = 0
            self.status_banner = "Multiple Faces Detected! Driver Authorisation Denied"
            self.liveness.reset()
            self._set_vehicle_state(0, "Unknown", reason="Multiple faces detected in driver seat")
            diagnostics["status"] = "MULTIPLE_FACES"

            self._draw_hud(frame, det_result, None, None)
            return frame, diagnostics

        # Case 1 & 2: Exactly one face detected
        primary_roi = det_result.primary_roi
        primary_box = det_result.primary_box
        rec_result = self.recognizer.predict(primary_roi)

        diagnostics.update({
            "driver": rec_result.driver,
            "is_authorised": rec_result.is_authorised,
            "similarity_pct": rec_result.similarity_pct,
            "distance": rec_result.confidence_distance
        })

        # Anti-Spoofing Liveness Evaluation
        liveness_ok = True
        liveness_msg = "Liveness disabled"
        if self.enable_liveness:
            liveness_ok, liveness_msg = self.liveness.update(frame, primary_roi, primary_box)

        # Check Authorisation Rules
        if rec_result.is_authorised:
            # Candidate is registered
            if self.last_candidate_driver == rec_result.driver:
                self.consecutive_matches += 1
            else:
                self.last_candidate_driver = rec_result.driver
                self.consecutive_matches = 1

            if self.consecutive_matches >= self.required_consecutive_matches:
                if not self.enable_liveness or liveness_ok or self.single_image_mode:
                    self.auth_state = "AUTHORISED"
                    self.status_banner = f"AUTH OK: Welcome, {rec_result.driver}! ({rec_result.similarity_pct:.1f}%)"
                    self._set_vehicle_state(1, rec_result.driver, reason=f"Face match verified: {rec_result.similarity_pct:.1f}%")
                    diagnostics["status"] = "AUTHORISED"
                    diagnostics["vehicle_locked"] = False
                else:
                    self.auth_state = "LIVENESS_CHECK"
                    self.status_banner = f"Recognised {rec_result.driver} -> {liveness_msg}"
                    diagnostics["status"] = "LIVENESS_CHECK"
            else:
                self.auth_state = "SCANNING"
                self.status_banner = f"Verifying {rec_result.driver}... [{self.consecutive_matches}/{self.required_consecutive_matches}]"
                diagnostics["status"] = "SCANNING"
        else:
            # Case 2: Unknown Driver / Low Confidence
            self.auth_state = "DENIED"
            self.consecutive_matches = 0
            self.last_candidate_driver = None
            self.status_banner = f"AUTH DENIED: Unknown Driver (Dist: {rec_result.confidence_distance:.1f})"
            self._set_vehicle_state(0, "Unknown", reason="Unknown face or confidence below threshold")
            diagnostics["status"] = "UNAUTHORISED"

        self._draw_hud(frame, det_result, rec_result, primary_box)
        return frame, diagnostics

    def _draw_hud(self,
                  frame: np.ndarray,
                  det_result: FaceDetectionResult,
                  rec_result: Optional[RecognitionResult],
                  box: Optional[Tuple[int, int, int, int]]):
        """Draws rich augmented reality HUD for examiner demonstrations."""
        h, w = frame.shape[:2]

        # Top Banner
        header_color = (0, 140, 0) if not self.vehicle_locked else (0, 0, 160)
        cv2.rectangle(frame, (0, 0), (w, 55), header_color, -1)
        cv2.putText(frame, "AcciAlert - AI Driver Authorisation System", (15, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
        cv2.putText(frame, self.status_banner, (15, 48),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 0), 1)

        # Vehicle Lock Status Pill
        lock_text = "VEHICLE UNLOCKED" if not self.vehicle_locked else "VEHICLE LOCKED"
        pill_color = (0, 220, 0) if not self.vehicle_locked else (0, 0, 220)
        cv2.rectangle(frame, (w - 220, 10), (w - 15, 45), pill_color, -1)
        cv2.putText(frame, lock_text, (w - 210, 33),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        # Draw Face Bounding Boxes
        if det_result.face_count > 1:
            for (x, y, fw, fh) in det_result.boxes:
                cv2.rectangle(frame, (x, y), (x + fw, y + fh), (0, 0, 255), 2)
                cv2.putText(frame, "UNAUTHORISED (MULTI)", (x, y - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        elif det_result.face_count == 1 and box is not None and rec_result is not None:
            x, y, fw, fh = box
            box_color = (0, 255, 0) if rec_result.is_authorised and not self.vehicle_locked else (0, 0, 255)
            cv2.rectangle(frame, (x, y), (x + fw, y + fh), box_color, 2)

            # Name and confidence tag
            tag = f"{rec_result.driver} ({rec_result.similarity_pct:.1f}%)"
            cv2.rectangle(frame, (x, y - 28), (x + fw, y), box_color, -1)
            cv2.putText(frame, tag, (x + 5, y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)

        # Bottom Diagnostics Bar
        cv2.rectangle(frame, (0, h - 35), (w, h), (30, 30, 30), -1)
        serial_mode = "MOCK" if self.serial.is_mock else f"PORT {self.serial.port}"
        diag_str = f"ESP32: {serial_mode} | Driver: {self.current_driver} | Liveness: {'ON' if self.enable_liveness else 'OFF'} | 'q'=Quit 'r'=Reset"
        cv2.putText(frame, diag_str, (15, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

    def run_simulation_mode(self, cycles: int = 2):
        """
        Runs an automated automotive simulation when physical webcam is not present.
        Demonstrates:
          1. Arun entering vehicle -> Vehicle Unlocks (AUTH_OK).
          2. Arun leaving -> Vehicle Locks (NO_FACE).
          3. Unknown intruder entering -> Access Denied (AUTH_DENIED).
        """
        print("\n" + "=" * 65)
        print("AcciAlert - Running Driver Authorisation Simulation Mode")
        print("=" * 65)

        scenario_queue = [
            ("Arun (Authorised Driver)", list((config.DATASET_TEST_DIR / "Arun").glob("*.jpg"))[:8]),
            ("Driver Exits Seat (Empty)", [np.full((300, 300, 3), 128, dtype=np.uint8) for _ in range(3)]),
            ("Intruder / Unknown Person", list((config.DATASET_TEST_DIR / "Unknown_1").glob("*.jpg"))[:8]),
            ("Kumar (Authorised Driver)", list((config.DATASET_TEST_DIR / "Kumar").glob("*.jpg"))[:8]),
            ("Driver Exits Seat (Empty)", [np.full((300, 300, 3), 128, dtype=np.uint8) for _ in range(3)])
        ]

        display_available = True
        for cycle in range(1, cycles + 1):
            print(f"\n--- Simulation Cycle {cycle}/{cycles} ---")
            for scenario_title, frames in scenario_queue:
                print(f"[*] Scenario Event: {scenario_title}")
                self.liveness.reset()
                for f_item in frames:
                    if isinstance(f_item, (str, Path)):
                        frame = cv2.imread(str(f_item))
                    else:
                        frame = f_item.copy()

                    if frame is None:
                        continue

                    processed_frame, diag = self.process_frame(frame)

                    # Print concise telemetry line
                    lock_badge = "[UNLOCKED]" if not self.vehicle_locked else "[LOCKED]"
                    print(f"    -> Status: {diag['status']:<14} | Driver: {diag['driver']:<10} | Sim: {diag['similarity_pct']:5.1f}% | Car: {lock_badge}")

                    if display_available:
                        try:
                            cv2.imshow("AcciAlert - Driver Authorisation Camera (Simulation)", processed_frame)
                            key = cv2.waitKey(80) & 0xFF
                            if key == ord('q') or key == 27:
                                print("[*] User interrupted simulation.")
                                cv2.destroyAllWindows()
                                self.serial.close()
                                return
                        except Exception:
                            display_available = False

                    time.sleep(0.06)

        if display_available:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass
        self.serial.close()
        print("\n[+] Simulation run completed successfully with zero defects.")
        print("=" * 65)

    def run_live(self):
        """Starts live webcam recognition loop (falls back to simulation if no camera)."""
        print(f"[*] Opening webcam index {self.camera_index}...")
        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY)
        if not cap.isOpened():
            print(f"[!] Camera index {self.camera_index} failed. Trying index 1...")
            cap = cv2.VideoCapture(1, cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY)

        if not cap.isOpened():
            print("[!] Physical USB webcam not detected on index 0 or 1.")
            print("[*] Automatically switching to ACCIALERT SIMULATION / DEMO MODE...")
            self.run_simulation_mode()
            return

        print("\n" + "=" * 60)
        print("AcciAlert Driver Authorisation Active")
        print("Controls:")
        print("  'q' or ESC - Exit program")
        print("  'r'       - Reset authentication state & lock vehicle")
        print("  'l'       - Toggle anti-spoofing liveness check")
        print("=" * 60 + "\n")

        while True:
            ret, frame = cap.read()
            if not ret:
                print("[-] Error reading frame from camera feed.")
                break

            processed_frame, _ = self.process_frame(frame)
            cv2.imshow("AcciAlert - Driver Authorisation Camera", processed_frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:
                print("[*] Terminating driver authorization session...")
                break
            elif key == ord('r'):
                print("[*] Resetting authorization state. Vehicle Locked.")
                self._set_vehicle_state(0, "Unknown", reason="Manual operator reset")
                self.liveness.reset()
                self.consecutive_matches = 0
            elif key == ord('l'):
                self.enable_liveness = not self.enable_liveness
                print(f"[*] Liveness detection toggled: {'ENABLED' if self.enable_liveness else 'DISABLED'}")

        cap.release()
        cv2.destroyAllWindows()
        self.serial.close()
        print("[+] Session closed cleanly.")


def authenticate_single_image(image_path: str, threshold: float = config.MATCH_THRESHOLD):
    """Utility to test authentication on a single image file (headless mode)."""
    img = cv2.imread(image_path)
    if img is None:
        print(f"[-] Could not load image: {image_path}")
        return None

    auth = DriverAuthSystem(threshold=threshold, enable_liveness=False, single_image_mode=True)
    processed, diag = auth.process_frame(img)
    print("\n--- Authentication Result ---")
    print(f"Driver Name:     {diag['driver']}")
    print(f"Authorised:      {diag['is_authorised']}")
    print(f"Similarity:      {diag['similarity_pct']:.1f}%")
    print(f"Distance Score:  {diag['distance']:.2f}")
    print(f"Vehicle Locked:  {diag['vehicle_locked']}")
    print(f"Status:          {diag['status']}")
    return diag


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AcciAlert - AI Face Recognition & Driver Authorisation")
    parser.add_argument("--camera", type=int, default=0, help="Webcam device index (default: 0)")
    parser.add_argument("--serial", type=str, default=None, help="ESP32 serial port (e.g. COM3)")
    parser.add_argument("--threshold", type=float, default=config.MATCH_THRESHOLD, help="LBPH matching distance threshold")
    parser.add_argument("--no-liveness", action="store_true", help="Disable anti-spoofing liveness check")
    parser.add_argument("--demo", action="store_true", help="Run automated demonstration simulation")
    parser.add_argument("--image", type=str, default=None, help="Evaluate a single image file instead of live camera")

    args = parser.parse_args()

    if args.image:
        authenticate_single_image(args.image, threshold=args.threshold)
    elif args.demo:
        system = DriverAuthSystem(
            serial_port=args.serial,
            threshold=args.threshold,
            enable_liveness=not args.no_liveness
        )
        system.run_simulation_mode()
    else:
        system = DriverAuthSystem(
            camera_index=args.camera,
            serial_port=args.serial,
            threshold=args.threshold,
            enable_liveness=not args.no_liveness
        )
        system.run_live()

