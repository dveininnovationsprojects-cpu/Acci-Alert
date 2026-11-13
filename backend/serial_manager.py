"""
AcciAlert - Serial Communication Manager
Handles PySerial background telemetry reading and command transmission with ESP32.
Author: Muniyappan
"""

import json
import time
import threading
import logging
from typing import Optional, Callable, List, Dict, Any
import serial
import serial.tools.list_ports

from config import DEFAULT_SERIAL_PORT, DEFAULT_SERIAL_BAUD, SERIAL_TIMEOUT

logger = logging.getLogger("AcciAlert.Serial")
logging.basicConfig(level=logging.INFO)


class SerialManager:
    """
    Manages USB Serial connection with ESP32 microcontroller at 115200 baud.
    Runs a non-blocking background thread to parse incoming telemetry JSON packets
    and allows sending commands back to the ESP32.
    """

    def __init__(self, port: str = DEFAULT_SERIAL_PORT, baudrate: int = DEFAULT_SERIAL_BAUD,
                 on_telemetry_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.port = port
        self.baudrate = baudrate
        self.on_telemetry = on_telemetry_callback

        self.serial_conn: Optional[serial.Serial] = None
        self.is_running = False
        self.thread: Optional[threading.Thread] = None

        self.lock = threading.Lock()
        self.latest_telemetry: Optional[Dict[str, Any]] = None
        self.last_packet_time: float = 0.0
        self.packets_received: int = 0
        self.packet_errors: int = 0
        self.auto_reconnect = True

    @staticmethod
    def list_available_ports() -> List[Dict[str, str]]:
        """List all available serial COM ports on the machine."""
        ports = serial.tools.list_ports.comports()
        result = []
        for p in ports:
            result.append({
                "device": p.device,
                "description": p.description,
                "hwid": p.hwid,
                "is_esp32": "esp" in (p.description or "").lower() or "ch340" in (p.description or "").lower() or "cp210" in (p.description or "").lower()
            })
        return result

    def connect(self, port: Optional[str] = None, baudrate: Optional[int] = None) -> bool:
        """Open serial connection and start background listening thread."""
        with self.lock:
            if port:
                self.port = port
            if baudrate:
                self.baudrate = baudrate

            # Close if already open
            if self.serial_conn and self.serial_conn.is_open:
                try:
                    self.serial_conn.close()
                except Exception:
                    pass

            try:
                logger.info(f"Connecting to ESP32 on {self.port} at {self.baudrate} baud...")
                self.serial_conn = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    timeout=SERIAL_TIMEOUT,
                    write_timeout=SERIAL_TIMEOUT
                )
                self.is_running = True
                self.thread = threading.Thread(target=self._reader_loop, daemon=True, name="SerialReader")
                self.thread.start()
                logger.info(f"Successfully connected to serial port {self.port}")
                return True
            except Exception as e:
                logger.warning(f"Could not open serial port {self.port}: {e}")
                self.serial_conn = None
                return False

    def disconnect(self):
        """Disconnect serial port and terminate background reader."""
        self.is_running = False
        with self.lock:
            if self.serial_conn:
                try:
                    self.serial_conn.close()
                except Exception as e:
                    logger.error(f"Error closing serial port: {e}")
                self.serial_conn = None
        logger.info("Serial port disconnected.")

    def is_connected(self) -> bool:
        """Check if serial port is open and actively receiving."""
        return bool(self.serial_conn and self.serial_conn.is_open and self.is_running)

    def send_command(self, command: str, value: Any = None) -> bool:
        """
        Send formatted command string to ESP32.
        Supported commands:
        - AUTH (e.g. "AUTH:1" or "AUTH:0")
        - STOP ("STOP")
        - RESET ("RESET")
        - SIGNAL (e.g. "SIGNAL:RED")
        - LIMIT (e.g. "LIMIT:40")
        - ZONES (e.g. "ZONES:<payload>")
        """
        if not self.is_connected():
            logger.warning(f"Cannot send command '{command}': Serial port is not connected.")
            return False

        cmd_str = command.strip().upper()
        if value is not None and str(value).strip() != "":
            payload = f"{cmd_str}:{value}\n"
        else:
            payload = f"{cmd_str}\n"

        try:
            with self.lock:
                if self.serial_conn and self.serial_conn.is_open:
                    self.serial_conn.write(payload.encode("utf-8"))
                    self.serial_conn.flush()
                    logger.info(f"Sent command to ESP32: {payload.strip()}")
                    return True
        except Exception as e:
            logger.error(f"Failed to transmit command to serial: {e}")
            self.packet_errors += 1
            return False
        return False

    def _reader_loop(self):
        """Background thread continuously reading lines from serial and parsing JSON."""
        line_buffer = ""
        while self.is_running:
            if not self.serial_conn or not self.serial_conn.is_open:
                time.sleep(0.5)
                continue

            try:
                raw_bytes = self.serial_conn.readline()
                if not raw_bytes:
                    continue

                try:
                    line = raw_bytes.decode("utf-8", errors="replace").strip()
                except Exception:
                    continue

                if not line:
                    continue

                # Check if line looks like JSON
                if line.startswith("{") and line.endswith("}"):
                    try:
                        telemetry_data = json.loads(line)
                        with self.lock:
                            self.latest_telemetry = telemetry_data
                            self.last_packet_time = time.time()
                            self.packets_received += 1

                        if self.on_telemetry:
                            self.on_telemetry(telemetry_data)

                    except json.JSONDecodeError as err:
                        self.packet_errors += 1
                        logger.debug(f"JSON decode error: {err} | Raw: {line}")
                else:
                    logger.debug(f"Non-JSON serial line received: {line}")

            except serial.SerialException as se:
                logger.error(f"Serial read exception: {se}")
                self.packet_errors += 1
                time.sleep(1.0)
            except Exception as ex:
                logger.error(f"Unexpected serial reader error: {ex}")
                time.sleep(0.5)

    def get_status(self) -> Dict[str, Any]:
        """Return real-time diagnostic status of serial interface."""
        now = time.time()
        alive = self.is_connected() and (now - self.last_packet_time < 3.0 if self.last_packet_time > 0 else True)
        return {
            "connected": self.is_connected(),
            "alive": alive,
            "port": self.port,
            "baudrate": self.baudrate,
            "packets_received": self.packets_received,
            "packet_errors": self.packet_errors,
            "last_packet_seconds_ago": round(now - self.last_packet_time, 1) if self.last_packet_time > 0 else None,
            "latest_telemetry": self.latest_telemetry
        }

