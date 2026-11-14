"""Downloads OpenCV's pretrained face models into backend/models/.

    python -m backend.tools.get_models

Source: https://github.com/opencv/opencv_zoo (Apache 2.0 licence)
  YuNet  face detector    about 0.2 MB
  SFace  face recogniser  about 37 MB
"""

import urllib.request

from backend import config

BASE = "https://github.com/opencv/opencv_zoo/raw/main/models/"
MODELS = {
    config.YUNET_FILE: BASE + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
    config.SFACE_FILE: BASE + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
}


def main():
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for path, url in MODELS.items():
        if path.exists():
            print(f"{path.name} is already there")
            continue
        print(f"Downloading {path.name} ...")
        urllib.request.urlretrieve(url, path)
        print(f"  saved {path.stat().st_size / 1e6:.1f} MB")
    print("Done. Restart the server, then enrol drivers from the dashboard.")


if __name__ == "__main__":
    main()
