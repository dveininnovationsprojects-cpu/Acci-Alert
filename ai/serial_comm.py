"""
AcciAlert - ESP32 Serial Communication Module
Module: AI Face Recognition & Driver Authorisation (Arsal)

Features:
  - Baud rate: 115200
  - Protocol: JSON line messages (e.g. {"cmd":"AUTH","ok":1,"driver":"Arun"})
  - Automatic port discovery for ESP32 / CH340 / CP210x devices
  - Graceful Mock/Simulation mode when ESP32 hardware is not plugged in
"""

import json
import time
from typing import Optional, List, Dict, Any
import serial
from serial.tools import list_ports

from ai import config


class ESP32SerialCommunicator:
    """Manages serial transmission of driver authentication status to ESP32."""

    def __init__(self, port: Optional[str] = None, baudrate: int = config.SERIAL_BAUDRATE, timeout: float = config.SERIAL_TIMEOUT):
        self.port = port or config.SERIAL_PORT
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_conn: Optional[serial.Serial] = None
        self.is_mock = False
        self.last_sent_payload: Optional[Dict[str, Any]] = None
        self.sent_history: List[Dict[str, Any]] = []

        self._connect()

    def _find_esp32_port(self) -> Optional[str]:
        """Scans system COM ports for likely ESP32 / UART adapters."""
        ports = list(list_ports.comports())
        for p in ports:
            desc = p.description.lower()
            hwid = p.hwid.lower()
            if any(k in desc or k in hwid for k in ["ch340", "cp210", "usb-serial", "esp32", "ftdi"]):
                print(f"[*] Detected prospective ESP32 device on: {p.device} ({p.description})")
                return p.device
        return None

    def _connect(self):
        """Attempts to open physical serial connection; falls back to Mock mode if not present."""
        target_port = self.port
        try:
            print(f"[*] Connecting to ESP32 on port {target_port} at {self.baudrate} baud...")
            self.serial_conn = serial.Serial(target_port, self.baudrate, timeout=self.timeout)
            time.sleep(2.0)  # Allow ESP32 reboot delay after DTR toggle
            print(f"[+] Successfully connected to ESP32 on {target_port}!")
            self.is_mock = False
            return
        except Exception as e:
            # Try auto-detecting
            auto_port = self._find_esp32_port()
            if auto_port and auto_port != target_port:
                try:
                    print(f"[*] Trying auto-detected port {auto_port}...")
                    self.serial_conn = serial.Serial(auto_port, self.baudrate, timeout=self.timeout)
                    time.sleep(2.0)
                    self.port = auto_port
                    self.is_mock = False
                    print(f"[+] Successfully connected to ESP32 on {auto_port}!")
                    return
                except Exception:
                    pass

            print(f"[!] Warning: Could not connect to physical serial port '{target_port}'.")
            print("[!] Running in MOCK SERIAL mode (messages will be logged to console and stored in history).")
            self.is_mock = True
            self.serial_conn = None

    def send_auth(self, ok: int, driver: str) -> bool:
        """
        Sends AUTH command to ESP32.
        Format: {"cmd":"AUTH","ok":1,"driver":"Arun"}\n
        or:     {"cmd":"AUTH","ok":0,"driver":"Unknown"}\n
        """
        payload = {
            "cmd": "AUTH",
            "ok": int(ok),
            "driver": str(driver)
        }
        json_str = json.dumps(payload, separators=(',', ':')) + "\n"
        self.last_sent_payload = payload
        self.sent_history.append({"time": time.time(), "payload": payload})

        if not self.is_mock and self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.write(json_str.encode("utf-8"))
                self.serial_conn.flush()
                print(f"[SERIAL OUT] Transmitted: {json_str.strip()}")
                return True
            except Exception as err:
                print(f"[-] Serial write error: {err}. Switching to mock mode.")
                self.is_mock = True
                return False
        else:
            print(f"[MOCK SERIAL OUT] Simulated ESP32 command: {json_str.strip()}")
            return True

    def close(self):
        """Closes serial connection."""
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
                print("[*] Serial connection closed.")
            except Exception:
                pass
        self.serial_conn = None

