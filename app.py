"""
AcciAlert - Project Root Entry Point
Redirects execution seamlessly to backend/app.py
Author: Muniyappan
"""

import os
import sys

# Ensure backend directory is in python path and active working directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(BASE_DIR, "backend")

sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

# Import backend application and start SocketIO server
if __name__ == "__main__":
    import app
    app.socketio.run(
        app.app,
        host=app.config.HOST,
        port=app.config.PORT,
        debug=app.config.DEBUG,
        allow_unsafe_werkzeug=True
    )

