"""Fake car data for the dashboard when no ESP32 is connected.
Moves a car along the road, produces sensor values and reacts to the control buttons.
"""

import random
import time
from typing import Dict, Any, List
from zones import haversine_distance, calculate_bearing, move_coordinate, find_active_danger_zone


# Predefined realistic driving circuit around Chennai connecting Anna Salai,
# Guindy, Marina, and Koyambedu corridors.
SIMULATION_WAYPOINTS = [
    (13.0827, 80.2707),  # Anna Salai / Central (Z01 vicinity)
    (13.0750, 80.2600),  # Thousand Lights
    (13.0600, 80.2500),  # T. Nagar approach
    (13.0400, 80.2300),  # Saidapet
    (13.0067, 80.2023),  # Guindy Flyover / Sharp Curve (Z02 vicinity)
    (13.0200, 80.2150),  # Ashok Nagar
    (13.0694, 80.1948),  # Koyambedu Junction (Z03 vicinity)
    (13.0750, 80.2200),  # Kilpauk
    (13.0850, 80.2500),  # Perambur Barracks
    (13.0500, 80.2824),  # Marina Beach Promenade (Z04 vicinity)
    (13.0700, 80.2850),  # Santhome / Fort area
    (13.0827, 80.2707)   # Back to Start
]


class VehicleSimulator:
    """
    Simulates real-time telemetry matching the ESP32 JSON protocol:
    {"t":123456,"spd":18.5,"alc":812,"dist":54,"ax":0.12,"ay":0.03,"az":1.01,"tilt":4,"lat":13.0827,"lon":80.2707,"fix":1,"state":"RUNNING","auth":1,"signal":"GREEN","zone":"Z03","evt":""}
    """

    def __init__(self):
        self.enabled = True
        self.tick = int(time.time() * 1000)

        # Vehicle position and navigation
        self.waypoint_index = 0
        self.current_lat = SIMULATION_WAYPOINTS[0][0]
        self.current_lon = SIMULATION_WAYPOINTS[0][1]
        self.target_lat = SIMULATION_WAYPOINTS[1][0]
        self.target_lon = SIMULATION_WAYPOINTS[1][1]
        self.heading = 0.0

        # Physical dynamics
        self.speed = 36.0          # Current speed in km/h
        self.target_speed = 45.0   # Desired cruising speed
        self.speed_limit = 60.0    # Current effective speed limit
        self.state = "RUNNING"     # LOCKED / READY / RUNNING / WARNING / EMERGENCY
        self.auth = 1              # 1 = Authorized, 0 = Unauthorized
        self.signal = "GREEN"      # GREEN / YELLOW / RED
        self.active_zone_code = "NONE"
        self.gps_fix = 1           # 1 = 3D Fix

        # Sensor readings
        self.alcohol_adc = 180     # Normal baseline ~150-250
        self.obstacle_dist = 180   # cm
        self.ax = 0.02             # g
        self.ay = 0.01             # g
        self.az = 1.00             # g (normal gravity)
        self.tilt = 3.0            # degrees
        self.current_event = ""

        # Scenario overrides (for testing)
        self.scenario_timer = 0
        self.active_scenario = None

        # Danger zones cache for real-time intersection
        self.danger_zones: List[Dict[str, Any]] = []

    @property
    def latitude(self) -> float:
        return float(self.current_lat)

    @property
    def longitude(self) -> float:
        return float(self.current_lon)

    def set_danger_zones(self, zones: List[Dict[str, Any]]):
        """Update simulator's awareness of configured danger zones."""
        self.danger_zones = zones

    def trigger_scenario(self, scenario_name: str, duration_ticks: int = 15):
        """
        Manually trigger an alert condition for demonstration/testing.
        Available scenarios:
        'accident', 'alcohol', 'obstacle', 'overspeed', 'unauthorized', 'red_signal', 'danger_zone', 'normal'
        """
        self.active_scenario = scenario_name.lower()
        self.scenario_timer = duration_ticks

        if self.active_scenario == "accident":
            self.tilt = 58.5
            self.ax = 2.85
            self.ay = 1.95
            self.az = 0.45
            self.speed = 0.0
            self.target_speed = 0.0
            self.state = "EMERGENCY"
            self.current_event = "ACCIDENT"

        elif self.active_scenario == "alcohol":
            self.alcohol_adc = 845
            self.state = "WARNING"
            self.current_event = "ALCOHOL DETECTED"

        elif self.active_scenario == "obstacle":
            self.obstacle_dist = 28
            self.state = "WARNING"
            self.current_event = "OBSTACLE DETECTED"

        elif self.active_scenario == "overspeed":
            self.speed = 88.5
            self.target_speed = 88.5
            self.state = "WARNING"
            self.current_event = "OVERSPEED"

        elif self.active_scenario == "unauthorized":
            self.auth = 0
            self.state = "LOCKED"
            self.current_event = "UNAUTHORISED DRIVER"

        elif self.active_scenario == "red_signal":
            self.signal = "RED"
            self.target_speed = 0.0
            self.current_event = "TRAFFIC SIGNAL STOP"

        elif self.active_scenario == "danger_zone":
            # Teleport vehicle right near first danger zone
            if self.danger_zones:
                target_zone = self.danger_zones[0]
                self.current_lat = target_zone["latitude"]
                self.current_lon = target_zone["longitude"]
                self.active_zone_code = target_zone.get("code", "Z01")
                self.speed_limit = float(target_zone.get("speed_limit", 30))
                self.target_speed = self.speed_limit
                self.current_event = "DANGER ZONE"

        elif self.active_scenario == "normal":
            self.reset_to_normal()

    def reset_to_normal(self):
        """Reset all sensor overrides to normal safe cruising conditions."""
        self.active_scenario = None
        self.scenario_timer = 0
        self.tilt = random.uniform(2.0, 4.5)
        self.ax = round(random.uniform(-0.05, 0.05), 2)
        self.ay = round(random.uniform(-0.04, 0.04), 2)
        self.az = round(random.uniform(0.98, 1.02), 2)
        self.alcohol_adc = random.randint(160, 230)
        self.obstacle_dist = random.randint(140, 260)
        self.auth = 1
        self.signal = "GREEN"
        self.state = "RUNNING"
        self.speed_limit = 60.0
        self.target_speed = 45.0
        self.current_event = ""

    def handle_command(self, command: str, value: Any = None):
        """Respond to commands received from API or Control Panel."""
        cmd = command.strip().upper()

        if cmd == "STOP":
            self.state = "EMERGENCY"
            self.speed = 0.0
            self.target_speed = 0.0
            self.current_event = "EMERGENCY STOP"

        elif cmd == "RESET":
            self.reset_to_normal()

        elif cmd == "AUTH":
            try:
                self.auth = int(value) if value is not None else (0 if self.auth == 1 else 1)
            except (ValueError, TypeError):
                self.auth = 1 if self.auth == 0 else 0
            if self.auth == 0:
                self.state = "LOCKED"
                self.speed = 0.0
                self.target_speed = 0.0
            else:
                if self.state == "LOCKED":
                    self.state = "RUNNING"
                    self.target_speed = 45.0

        elif cmd == "SIGNAL":
            sig_val = str(value).strip().upper() if value else "GREEN"
            if sig_val in ("RED", "YELLOW", "GREEN"):
                self.signal = sig_val
                if sig_val == "RED":
                    self.target_speed = 0.0
                elif sig_val == "YELLOW":
                    self.target_speed = min(self.target_speed, 20.0)
                else:
                    self.target_speed = self.speed_limit

        elif cmd == "LIMIT":
            try:
                new_limit = float(value)
                self.speed_limit = new_limit
                self.target_speed = min(self.target_speed, new_limit)
            except (ValueError, TypeError):
                pass

        elif cmd == "ACK":
            # Acknowledge current active event
            self.current_event = ""
            if self.state == "WARNING":
                self.state = "RUNNING"

    def step(self) -> Dict[str, Any]:
        """
        Advance vehicle simulation by 1 step (called every TELEMETRY_INTERVAL).
        Calculates position, updates sensors, detects zones, and returns packet.
        """
        self.tick += 1000

        # Check scenario timeout
        if self.scenario_timer > 0:
            self.scenario_timer -= 1
            if self.scenario_timer == 0 and self.active_scenario != "accident":
                self.reset_to_normal()

        # Manage vehicle speed & physics
        if self.state in ("EMERGENCY", "LOCKED"):
            self.speed = max(0.0, self.speed - 15.0)  # fast braking
        elif self.signal == "RED":
            self.speed = max(0.0, self.speed - 8.0)
        else:
            # Smooth acceleration or deceleration toward target
            effective_target = min(self.target_speed, self.speed_limit)
            if self.speed < effective_target:
                self.speed = min(effective_target, self.speed + random.uniform(2.0, 4.5))
            elif self.speed > effective_target:
                self.speed = max(effective_target, self.speed - random.uniform(3.0, 6.0))

            # Add minor cruising jitter
            if self.speed > 5.0 and self.active_scenario != "overspeed":
                self.speed = max(0.0, self.speed + random.uniform(-0.5, 0.5))

        # Vehicle Movement along waypoints
        if self.speed > 0.5:
            dist_to_target = haversine_distance(self.current_lat, self.current_lon, self.target_lat, self.target_lon)
            self.heading = calculate_bearing(self.current_lat, self.current_lon, self.target_lat, self.target_lon)

            # Distance traveled in 1 second at current speed (km/h -> m/s)
            distance_traveled_m = (self.speed * 1000.0) / 3600.0

            if distance_traveled_m >= dist_to_target:
                # Reached waypoint, advance to next
                self.current_lat = self.target_lat
                self.current_lon = self.target_lon
                self.waypoint_index = (self.waypoint_index + 1) % len(SIMULATION_WAYPOINTS)
                next_wpt = SIMULATION_WAYPOINTS[self.waypoint_index]
                self.target_lat = next_wpt[0]
                self.target_lon = next_wpt[1]
            else:
                # Move partial distance towards target
                new_lat, new_lon = move_coordinate(self.current_lat, self.current_lon, self.heading, distance_traveled_m)
                self.current_lat = new_lat
                self.current_lon = new_lon

        # Danger Zone Geofencing
        active_zone, dist_to_zone = find_active_danger_zone(self.current_lat, self.current_lon, self.danger_zones)
        if active_zone:
            self.active_zone_code = active_zone.get("code", "ZONE")
            zone_limit = float(active_zone.get("speed_limit", 30))
            self.speed_limit = zone_limit
            # Auto-limit speed inside zone
            if self.speed > zone_limit and self.active_scenario != "overspeed":
                self.target_speed = zone_limit
        else:
            self.active_zone_code = "NONE"
            if self.speed_limit < 60.0 and self.active_scenario is None:
                self.speed_limit = 60.0
                if self.state == "RUNNING":
                    self.target_speed = 45.0

        # Normal sensor fluctuations when not under active override
        if self.active_scenario is None:
            # Alcohol
            self.alcohol_adc = max(80, min(300, self.alcohol_adc + random.randint(-8, 8)))
            # Obstacle
            if self.speed > 5:
                self.obstacle_dist = max(45, min(350, self.obstacle_dist + random.randint(-15, 18)))
            # Tilt & Accelerometer
            self.tilt = round(max(0.5, min(8.0, self.tilt + random.uniform(-0.4, 0.4))), 1)
            self.ax = round(random.uniform(-0.06, 0.06), 2)
            self.ay = round(random.uniform(-0.05, 0.05), 2)
            self.az = round(random.uniform(0.97, 1.03), 2)

        # Assemble telemetry JSON according to ESP32 specification
        telemetry = {
            "t": self.tick,
            "spd": round(self.speed, 1),
            "alc": int(self.alcohol_adc),
            "dist": int(self.obstacle_dist),
            "ax": round(self.ax, 2),
            "ay": round(self.ay, 2),
            "az": round(self.az, 2),
            "tilt": round(self.tilt, 1),
            "lat": round(self.current_lat, 6),
            "lon": round(self.current_lon, 6),
            "fix": int(self.gps_fix),
            "state": self.state,
            "auth": int(self.auth),
            "signal": self.signal,
            "zone": self.active_zone_code,
            "evt": self.current_event
        }

        return telemetry

