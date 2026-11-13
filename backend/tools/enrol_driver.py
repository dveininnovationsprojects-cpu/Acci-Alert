"""Adds a new driver by taking face photos from the webcam, then retrains the model.

    python -m backend.tools.enrol_driver --name "Arun" --target 25
    python -m backend.tools.enrol_driver --name "Priya" --camera 0

Press c to take a photo or hold Space for a burst.
"""

import argparse
import sys
import time
from pathlib import Path
import cv2

from backend import config
from backend.face_detector import FaceDetector
from backend.train_model import train_ai_model


def enrol_driver(driver_name: str,
                 target_count: int = 25,
                 camera_index: int = 0,
                 auto_retrain: bool = True):
    print("=" * 60)
    print(f"AcciAlert - Face Enrolment for: {driver_name}")
    print("=" * 60)

    clean_name = "".join(c for c in driver_name if c.isalnum() or c in ("_", "-")).strip()
    if not clean_name:
        print("[-] Invalid driver name.")
        return False

    driver_dir = config.DATASET_DIR / clean_name
    driver_dir.mkdir(parents=True, exist_ok=True)

    # Count existing images
    existing_images = list(driver_dir.glob("*.jpg")) + list(driver_dir.glob("*.png"))
    current_count = len(existing_images)
    print(f"[*] Enrolling driver: {clean_name}")
    print(f"[*] Target sample count: {target_count} (Currently saved: {current_count})")
    print("[*] Opening camera...")

    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY)
    if not cap.isOpened():
        print(f"[!] Camera index {camera_index} failed. Trying index 1...")
        cap = cv2.VideoCapture(1, cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY)

    if not cap.isOpened():
        print("[-] Error: Could not open webcam for enrolment.")
        return False

    detector = FaceDetector()
    captured = current_count
    last_auto_capture = 0.0

    pose_prompts = [
        "1. Look directly at camera (normal light)",
        "2. Turn head slightly left (10-15 degrees)",
        "3. Turn head slightly right (10-15 degrees)",
        "4. Lean slightly closer to the camera",
        "5. Lean slightly back from the camera",
        "6. Wear glasses / alter lighting if applicable"
    ]

    print("\n[*] Controls:")
    print("    - Press 'c' to capture one frame")
    print("    - Press 'a' to toggle AUTO-CAPTURE mode")
    print("    - Press 'q' or ESC to finish / exit\n")

    auto_mode = False

    while captured < target_count:
        ret, frame = cap.read()
        if not ret:
            print("[-] Camera feed interrupted.")
            break

        det_result = detector.detect_faces(frame)
        h, w = frame.shape[:2]

        # Draw HUD overlay
        prompt_idx = min(len(pose_prompts) - 1, int((captured / target_count) * len(pose_prompts)))
        current_prompt = pose_prompts[prompt_idx]

        # Header banner
        cv2.rectangle(frame, (10, 10), (w - 10, 60), (20, 20, 20), -1)
        status_text = f"Enrolling: {clean_name} [{captured}/{target_count}] | Auto: {'ON' if auto_mode else 'OFF'}"
        cv2.putText(frame, status_text, (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)
        cv2.putText(frame, current_prompt, (20, 53),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)

        # Draw face boxes
        if det_result.face_count == 1:
            x, y, fw, fh = det_result.primary_box
            cv2.rectangle(frame, (x, y), (x + fw, y + fh), (0, 255, 0), 2)
            cv2.putText(frame, "Face OK", (x, y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

            # Auto capture if enabled (every 0.4 seconds)
            now = time.time()
            if auto_mode and (now - last_auto_capture) >= 0.4:
                captured += 1
                save_path = driver_dir / f"{captured:02d}.jpg"
                cv2.imwrite(str(save_path), frame)
                last_auto_capture = now
                print(f"[+] Saved image {captured}/{target_count} -> {save_path.name}")
        elif det_result.face_count > 1:
            cv2.putText(frame, "WARNING: Multiple faces detected! Only driver should be visible.", (20, h - 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)
        else:
            cv2.putText(frame, "No face detected - Adjust position in front of camera", (20, h - 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 165, 255), 2)

        cv2.imshow("AcciAlert - Face Enrolment", frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord('q') or key == 27:
            print("[*] Enrolment ended by user.")
            break
        elif key == ord('a'):
            auto_mode = not auto_mode
            print(f"[*] Auto-capture toggled: {'ON' if auto_mode else 'OFF'}")
        elif key == ord('c'):
            if det_result.face_count == 1:
                captured += 1
                save_path = driver_dir / f"{captured:02d}.jpg"
                cv2.imwrite(str(save_path), frame)
                print(f"[+] Manual captured: {captured}/{target_count} -> {save_path.name}")
            else:
                print("[!] Face not detected properly. Cannot capture.")

    cap.release()
    cv2.destroyAllWindows()

    print(f"\n[+] Enrolment session complete. Total images for {clean_name}: {captured}")

    if auto_retrain and captured >= 10:
        print("[*] Automatically retraining AI face recognition model with new driver data...")
        train_ai_model()

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AcciAlert Driver Face Enrolment Tool")
    parser.add_argument("--name", type=str, default="Driver_New", help="Authorised driver name")
    parser.add_argument("--target", type=int, default=25, help="Number of face images to collect (min 20)")
    parser.add_argument("--camera", type=int, default=0, help="Webcam device index")
    parser.add_argument("--no-retrain", action="store_true", help="Skip automatic retraining after capture")

    args = parser.parse_args()
    enrol_driver(args.name, target_count=args.target, camera_index=args.camera, auto_retrain=not args.no_retrain)

