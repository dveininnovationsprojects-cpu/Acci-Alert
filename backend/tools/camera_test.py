"""Opens the webcam (tries camera 0, then 1) and shows the live picture. Press q or Esc to close.
"""

import sys
import time
import cv2


def run_camera_test(preferred_index=0):
    print("=" * 60)
    print("AcciAlert - Driver Camera Diagnostic")
    print("=" * 60)
    print(f"[*] Attempting to open webcam at index {preferred_index}...")

    cap = cv2.VideoCapture(preferred_index, cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY)
    
    active_index = preferred_index
    if not cap.isOpened():
        print(f"[!] Camera index {preferred_index} failed. Trying index 1...")
        cap = cv2.VideoCapture(1, cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY)
        active_index = 1

    if not cap.isOpened():
        print("[-] Error: No camera detected on index 0 or 1.")
        print("[!] Please check:")
        print("    1. Is the USB webcam firmly connected?")
        print("    2. Do Windows Camera Privacy Settings allow camera access to desktop apps?")
        print("    3. Is another application (Teams, Zoom, browser) using the webcam?")
        return False

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"[+] Camera connected successfully on Index {active_index}!")
    print(f"[+] Native Resolution: {width}x{height} | Target FPS: {fps}")
    print("[*] Opening live preview window. Press 'q' or 'ESC' to exit.")

    frame_count = 0
    start_time = time.time()
    current_fps = 0.0

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[-] Error reading frame from camera.")
            break

        frame_count += 1
        elapsed = time.time() - start_time
        if elapsed >= 1.0:
            current_fps = frame_count / elapsed
            frame_count = 0
            start_time = time.time()

        # Overlay diagnostic text on preview frame
        overlay_text = f"AcciAlert Driver Camera [Index {active_index}] - {width}x{height} @ {current_fps:.1f} FPS"
        cv2.rectangle(frame, (10, 10), (frame.shape[1] - 10, 45), (0, 0, 0), -1)
        cv2.putText(frame, overlay_text, (20, 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.putText(frame, "Press 'q' to Quit", (20, frame.shape[0] - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        cv2.imshow("AcciAlert - Driver Camera Test", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q') or key == 27:
            print("[*] User closed camera preview.")
            break

    cap.release()
    cv2.destroyAllWindows()
    print("[+] Camera test finished cleanly.")
    return True


if __name__ == "__main__":
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    run_camera_test(idx)
