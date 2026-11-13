"""Serial link to the ESP32 (115200 baud, one JSON message per line).

Two classes live here:
  SerialManager          reads telemetry from the ESP32 and sends dashboard commands
  ESP32SerialCommunicator sends the driver login result (AUTH) after face recognition
"""

import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

import serial
import serial.tools.list_ports

try:
    from . import config
except ImportError:
    import config

logger = logging.getLogger("AcciAlert.Serial")

# Words that show up in the name of an ESP32 USB adapter
ESP32_HINTS = ("esp", "ch340", "cp210", "usb-serial", "ftdi")


class SerialManager:
    """Reads telemetry lines from the ESP32 in a background thread."""

    def __init__(self, port: str = config.DEFAULT_SERIAL_PORT,
                 baudrate: int = config.DEFAULT_SERIAL_BAUD,
                 on_telemetry_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.port = port
        self.baudrate = baudrate
        self.on_telemetry = on_telemetry_callback

        self.serial_conn: Optional[serial.Serial] = None
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.lock = threading.Lock()

        self.latest_telemetry: Optional[Dict[str, Any]] = None
        self.last_packet_time = 0.0
        self.packets_received = 0
        self.packet_errors = 0
        self.auto_reconnect = True

    @staticmethod
    def list_available_ports() -> List[Dict[str, str]]:
        """Return the COM ports on this computer."""
        result = []
        for p in serial.tools.list_ports.comports():
            name = (p.description or "").lower()
            result.append({
                "device": p.device,
                "description": p.description,
                "hwid": p.hwid,
                "is_esp32": any(hint in name for hint in ESP32_HINTS[:3]),
            })
        return result

    def connect(self, port: Optional[str] = None, baudrate: Optional[int] = None) -> bool:
        """Open the port and start the reader thread. Returns False if it fails."""
        with self.lock:
            if port:
                self.port = port
            if baudrate:
                self.baudrate = baudrate

            if self.serial_conn and self.serial_conn.is_open:
                try:
                    self.serial_conn.close()
                except Exception:
                    pass

            try:
                logger.info("Connecting to ESP32 on %s at %s baud", self.port, self.baudrate)
                self.serial_conn = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    timeout=config.SERIAL_TIMEOUT,
                    write_timeout=config.SERIAL_TIMEOUT,
                )
                self.is_running = True
                self.thread = threading.Thread(target=self._reader_loop, daemon=True, name="SerialReader")
                self.thread.start()
                return True
            except Exception as err:
                logger.warning("Could not open serial port %s: %s", self.port, err)
                self.serial_conn = None
                return False

    def disconnect(self):
        """Stop the reader thread and close the port."""
        self.is_running = False
        with self.lock:
            if self.serial_conn:
                try:
                    self.serial_conn.close()
                except Exception as err:
                    logger.error("Error closing serial port: %s", err)
                self.serial_conn = None
        logger.info("Serial port disconnected")

    def is_connected(self) -> bool:
        return bool(self.serial_conn and self.serial_conn.is_open and self.is_running)

    def send_command(self, command: str, value: Any = None) -> bool:
        """Send one command line such as STOP, RESET, SIGNAL:RED, LIMIT:40 or AUTH:1."""
        if not self.is_connected():
            logger.warning("Cannot send '%s': serial port is not connected", command)
            return False

        text = command.strip().upper()
        if value is not None and str(value).strip() != "":
            text = f"{text}:{value}"

        try:
            with self.lock:
                if self.serial_conn and self.serial_conn.is_open:
                    self.serial_conn.write((text + "\n").encode("utf-8"))
                    self.serial_conn.flush()
                    logger.info("Sent to ESP32: %s", text)
                    return True
        except Exception as err:
            logger.error("Failed to send command: %s", err)
            self.packet_errors += 1
        return False

    def _reader_loop(self):
        """Read lines, keep the JSON ones and pass them to the callback."""
        while self.is_running:
            if not self.serial_conn or not self.serial_conn.is_open:
                time.sleep(0.5)
                continue

            try:
                raw = self.serial_conn.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                if not (line.startswith("{") and line.endswith("}")):
                    logger.debug("Ignoring non-JSON line: %s", line)
                    continue

                try:
                    packet = json.loads(line)
                except json.JSONDecodeError as err:
                    self.packet_errors += 1
                    logger.debug("Bad JSON (%s): %s", err, line)
                    continue

                with self.lock:
                    self.latest_telemetry = packet
                    self.last_packet_time = time.time()
                    self.packets_received += 1
                if self.on_telemetry:
                    self.on_telemetry(packet)

            except serial.SerialException as err:
                logger.error("Serial read error: %s", err)
                self.packet_errors += 1
                time.sleep(1.0)
            except Exception as err:
                logger.error("Unexpected serial error: %s", err)
                time.sleep(0.5)

    def get_status(self) -> Dict[str, Any]:
        """Numbers for the dashboard status panel."""
        now = time.time()
        if self.last_packet_time > 0:
            alive = self.is_connected() and now - self.last_packet_time < 3.0
            seconds_ago = round(now - self.last_packet_time, 1)
        else:
            alive = self.is_connected()
            seconds_ago = None
        return {
            "connected": self.is_connected(),
            "alive": alive,
            "port": self.port,
            "baudrate": self.baudrate,
            "packets_received": self.packets_received,
            "packet_errors": self.packet_errors,
            "last_packet_seconds_ago": seconds_ago,
            "latest_telemetry": self.latest_telemetry,
        }


class ESP32SerialCommunicator:
    """Tells the ESP32 whether the driver passed face recognition.

    If no ESP32 is plugged in it runs in mock mode: messages are printed and
    kept in sent_history so the rest of the system still works.
    """

    def __init__(self, port: Optional[str] = None, baudrate: int = config.SERIAL_BAUDRATE,
                 timeout: float = config.SERIAL_TIMEOUT):
        self.port = port or config.SERIAL_PORT
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_conn: Optional[serial.Serial] = None
        self.is_mock = False
        self.last_sent_payload: Optional[Dict[str, Any]] = None
        self.sent_history: List[Dict[str, Any]] = []
        self._connect()

    def _find_esp32_port(self) -> Optional[str]:
        """Look for a COM port that belongs to an ESP32 style USB adapter."""
        for p in serial.tools.list_ports.comports():
            text = f"{p.description} {p.hwid}".lower()
            if any(hint in text for hint in ESP32_HINTS):
                print(f"Found a possible ESP32 on {p.device} ({p.description})")
                return p.device
        return None

    def _open(self, port: str) -> bool:
        try:
            self.serial_conn = serial.Serial(port, self.baudrate, timeout=self.timeout)
            time.sleep(2.0)  # the ESP32 reboots when the port opens
            self.port = port
            self.is_mock = False
            print(f"Connected to ESP32 on {port}")
            return True
        except Exception:
            return False

    def _connect(self):
        if self._open(self.port):
            return
        auto_port = self._find_esp32_port()
        if auto_port and auto_port != self.port and self._open(auto_port):
            return
        print(f"No ESP32 on {self.port}. Using mock serial: messages are only printed.")
        self.is_mock = True
        self.serial_conn = None

    def send_auth(self, ok: int, driver: str) -> bool:
        """Send {"cmd":"AUTH","ok":1,"driver":"Arun"} to the ESP32."""
        payload = {"cmd": "AUTH", "ok": int(ok), "driver": str(driver)}
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        self.last_sent_payload = payload
        self.sent_history.append({"time": time.time(), "payload": payload})

        if self.is_mock or not (self.serial_conn and self.serial_conn.is_open):
            print(f"[mock serial] {line.strip()}")
            return True

        try:
            self.serial_conn.write(line.encode("utf-8"))
            self.serial_conn.flush()
            print(f"[serial] {line.strip()}")
            return True
        except Exception as err:
            print(f"Serial write failed ({err}). Switching to mock mode.")
            self.is_mock = True
            return False

    def close(self):
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
            except Exception:
                pass
        self.serial_conn = None
