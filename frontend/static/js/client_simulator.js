/**
 * AcciAlert - Standalone Client-Side Simulation & Storage Engine
 * Enables the complete dashboard to run 100% in the browser (Frontend Only)
 * without requiring Python or a backend server, while still seamlessly connecting
 * to the Flask backend when available.
 * Author: Muniyappan
 */

// Waypoints around Chennai for standalone browser simulation
const CLIENT_WAYPOINTS = [
  [13.0827, 80.2707],  // Anna Salai / Central
  [13.0750, 80.2600],  // Thousand Lights
  [13.0600, 80.2500],  // T. Nagar
  [13.0400, 80.2300],  // Saidapet
  [13.0067, 80.2023],  // Guindy Sharp Curve
  [13.0200, 80.2150],  // Ashok Nagar
  [13.0694, 80.1948],  // Koyambedu
  [13.0750, 80.2200],  // Kilpauk
  [13.0850, 80.2500],  // Perambur
  [13.0500, 80.2824],  // Marina Beach Promenade
  [13.0700, 80.2850],  // Santhome
  [13.0827, 80.2707]   // Back to Start
];

// Initial default danger zones for frontend-only mode
const DEFAULT_CLIENT_ZONES = [
  { id: 1, code: "Z01", name: "Anna Salai School Zone", latitude: 13.0827, longitude: 80.2707, radius_meters: 150, speed_limit: 30, severity: "HIGH", details: "Active school zone with pedestrian traffic." },
  { id: 2, code: "Z02", name: "Guindy Sharp Curve", latitude: 13.0067, longitude: 80.2023, radius_meters: 200, speed_limit: 40, severity: "CRITICAL", details: "Accident prone hairpin curve." },
  { id: 3, code: "Z03", name: "Koyambedu Junction", latitude: 13.0694, longitude: 80.1948, radius_meters: 250, speed_limit: 35, severity: "MEDIUM", details: "Heavy bus terminal corridor." },
  { id: 4, code: "Z04", name: "Marina Beach Promenade", latitude: 13.0500, longitude: 80.2824, radius_meters: 300, speed_limit: 40, severity: "LOW", details: "Tourist zone and night fog." }
];

class ClientSimulationEngine {
  constructor(controller) {
    this.controller = controller;
    this.isRunning = false;
    this.timer = null;

    // Vehicle physics
    this.wptIdx = 0;
    this.lat = CLIENT_WAYPOINTS[0][0];
    this.lon = CLIENT_WAYPOINTS[0][1];
    this.speed = 38.0;
    this.targetSpeed = 45.0;
    this.speedLimit = 60.0;
    this.alcohol = 180;
    this.obstacleDist = 180;
    this.tilt = 3.2;
    this.ax = 0.02;
    this.ay = 0.01;
    this.az = 1.00;
    this.state = "RUNNING";
    this.auth = 1;
    this.signal = "GREEN";
    this.activeZone = "NONE";
    this.currentEvent = "";

    // Scenario timer
    this.scenarioTimer = 0;
    this.activeScenario = null;
  }

  start() {
    if (this.isRunning) return;
    this.isRunning = true;
    console.log("Client-side simulation engine running (Frontend-only mode).");
    this.timer = setInterval(() => this.tick(), 1000);
  }

  stop() {
    this.isRunning = false;
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  triggerScenario(scenarioName) {
    this.activeScenario = scenarioName.toLowerCase();
    this.scenarioTimer = 15;

    if (this.activeScenario === "accident") {
      this.tilt = 58.2;
      this.ax = 2.85;
      this.ay = 1.95;
      this.speed = 0.0;
      this.targetSpeed = 0.0;
      this.state = "EMERGENCY";
      this.currentEvent = "ACCIDENT";
      if (window.AlertManager) {
        window.AlertManager.triggerAlert({
          type: "ACCIDENT",
          severity: "CRITICAL",
          title: "CRITICAL: ACCIDENT DETECTED",
          message: "Rollover angle exceeded 45°! Emergency impact detected.",
          sound: "accident"
        });
      }
    } else if (this.activeScenario === "alcohol") {
      this.alcohol = 845;
      this.state = "WARNING";
      this.currentEvent = "ALCOHOL DETECTED";
      if (window.AlertManager) {
        window.AlertManager.triggerAlert({
          type: "ALCOHOL DETECTED",
          severity: "CRITICAL",
          title: "SAFETY ALERT: ALCOHOL DETECTED",
          message: "MQ-3 Sensor level 845 ADC exceeds safety limit (600 ADC).",
          sound: "alcohol"
        });
      }
    } else if (this.activeScenario === "obstacle") {
      this.obstacleDist = 28;
      this.state = "WARNING";
      this.currentEvent = "OBSTACLE DETECTED";
      if (window.AlertManager) {
        window.AlertManager.triggerAlert({
          type: "OBSTACLE DETECTED",
          severity: "HIGH",
          title: "PROXIMITY WARNING: OBSTACLE DETECTED",
          message: "Object detected within 28 cm! Immediate brake required.",
          sound: "obstacle"
        });
      }
    } else if (this.activeScenario === "overspeed") {
      this.speed = 88.5;
      this.targetSpeed = 88.5;
      this.state = "WARNING";
      this.currentEvent = "OVERSPEED";
      if (window.AlertManager) {
        window.AlertManager.triggerAlert({
          type: "OVERSPEED",
          severity: "HIGH",
          title: "SPEED VIOLATION: OVERSPEED",
          message: "Vehicle speed 88.5 km/h exceeds 60 km/h speed limit.",
          sound: "overspeed"
        });
      }
    } else if (this.activeScenario === "unauthorized") {
      this.auth = 0;
      this.state = "LOCKED";
      this.currentEvent = "UNAUTHORISED DRIVER";
      if (window.AlertManager) {
        window.AlertManager.triggerAlert({
          type: "UNAUTHORISED DRIVER",
          severity: "HIGH",
          title: "SECURITY ALERT: UNAUTHORISED DRIVER",
          message: "Facial / identity verification failed. Ignition locked.",
          sound: "unauthorized"
        });
      }
    } else if (this.activeScenario === "danger_zone") {
      const zones = window.ZoneApp ? window.ZoneApp.zonesList : DEFAULT_CLIENT_ZONES;
      if (zones.length > 0) {
        const target = zones[0];
        this.lat = parseFloat(target.latitude);
        this.lon = parseFloat(target.longitude);
        this.speedLimit = parseFloat(target.speed_limit);
        this.targetSpeed = this.speedLimit;
        this.activeZone = target.code;
        if (window.AlertManager) {
          window.AlertManager.triggerAlert({
            type: "DANGER ZONE",
            severity: target.severity || "HIGH",
            title: `DANGER ZONE: ${target.name}`,
            message: `Entered ${target.name}. Speed limit enforced: ${target.speed_limit} km/h.`,
            sound: "zone"
          });
        }
      }
    } else if (this.activeScenario === "normal") {
      this.resetNormal();
    }

    // Log event in client memory
    if (this.currentEvent && this.currentEvent !== "") {
      this.logClientEvent(this.currentEvent, `Triggered test scenario: ${scenarioName}`);
    }
  }

  resetNormal() {
    this.activeScenario = null;
    this.scenarioTimer = 0;
    this.alcohol = 180 + Math.floor(Math.random() * 40);
    this.obstacleDist = 160 + Math.floor(Math.random() * 60);
    this.tilt = 3.0 + Math.random();
    this.ax = 0.02;
    this.ay = 0.01;
    this.az = 1.00;
    this.auth = 1;
    this.signal = "GREEN";
    this.state = "RUNNING";
    this.speedLimit = 60.0;
    this.targetSpeed = 45.0;
    this.currentEvent = "";
  }

  handleCommand(cmd, val) {
    const c = cmd.toUpperCase();
    if (c === "STOP") {
      this.state = "EMERGENCY";
      this.speed = 0.0;
      this.targetSpeed = 0.0;
      this.currentEvent = "EMERGENCY STOP";
      this.logClientEvent("EMERGENCY STOP", "Manual emergency stop command triggered from control panel.");
    } else if (c === "RESET") {
      this.resetNormal();
    } else if (c === "AUTH") {
      this.auth = val !== null && val !== undefined ? parseInt(val) : (this.auth === 1 ? 0 : 1);
      if (this.auth === 0) {
        this.state = "LOCKED";
        this.speed = 0.0;
        this.targetSpeed = 0.0;
        this.logClientEvent("UNAUTHORISED DRIVER", "Driver authorization revoked.");
      } else {
        if (this.state === "LOCKED") this.state = "RUNNING";
        this.targetSpeed = 45.0;
      }
    } else if (c === "SIGNAL") {
      this.signal = String(val).toUpperCase();
      if (this.signal === "RED") {
        this.targetSpeed = 0.0;
      } else if (this.signal === "YELLOW") {
        this.targetSpeed = 20.0;
      } else {
        this.targetSpeed = this.speedLimit;
      }
    } else if (c === "LIMIT") {
      this.speedLimit = parseFloat(val) || 60.0;
      this.targetSpeed = Math.min(this.targetSpeed, this.speedLimit);
    } else if (c === "ACK") {
      this.currentEvent = "";
      if (this.state === "WARNING") this.state = "RUNNING";
    }
  }

  tick() {
    if (this.scenarioTimer > 0) {
      this.scenarioTimer--;
      if (this.scenarioTimer === 0 && this.activeScenario !== "accident") {
        this.resetNormal();
      }
    }

    // Speed physics
    if (this.state === "EMERGENCY" || this.state === "LOCKED") {
      this.speed = Math.max(0.0, this.speed - 12.0);
    } else if (this.signal === "RED") {
      this.speed = Math.max(0.0, this.speed - 7.0);
    } else {
      const effTarget = Math.min(this.targetSpeed, this.speedLimit);
      if (this.speed < effTarget) {
        this.speed = Math.min(effTarget, this.speed + 2.5 + Math.random() * 2);
      } else if (this.speed > effTarget) {
        this.speed = Math.max(effTarget, this.speed - 3.5);
      }
      if (this.speed > 5) {
        this.speed += (Math.random() - 0.5) * 0.8;
      }
    }

    // Movement along waypoints
    if (this.speed > 0.5) {
      const targetWpt = CLIENT_WAYPOINTS[(this.wptIdx + 1) % CLIENT_WAYPOINTS.length];
      const dLat = targetWpt[0] - this.lat;
      const dLon = targetWpt[1] - this.lon;
      const dist = Math.sqrt(dLat * dLat + dLon * dLon);

      if (dist < 0.001) {
        this.wptIdx = (this.wptIdx + 1) % CLIENT_WAYPOINTS.length;
      } else {
        const stepFraction = 0.04;
        this.lat += dLat * stepFraction;
        this.lon += dLon * stepFraction;
      }
    }

    // Danger Zone detection
    const zones = window.ZoneApp ? window.ZoneApp.zonesList : DEFAULT_CLIENT_ZONES;
    let inZone = null;
    zones.forEach(z => {
      const dLat = (this.lat - parseFloat(z.latitude)) * 111000;
      const dLon = (this.lon - parseFloat(z.longitude)) * 111000 * Math.cos(this.lat * Math.PI / 180);
      const distMeters = Math.sqrt(dLat * dLat + dLon * dLon);
      if (distMeters <= parseFloat(z.radius_meters)) {
        inZone = z;
      }
    });

    if (inZone) {
      if (this.activeZone !== inZone.code) {
        this.activeZone = inZone.code;
        this.speedLimit = parseFloat(inZone.speed_limit);
        if (window.AlertManager) {
          window.AlertManager.triggerAlert({
            type: "DANGER ZONE",
            severity: inZone.severity || "HIGH",
            title: `DANGER ZONE: ${inZone.name}`,
            message: `Vehicle entered ${inZone.name}. Speed limit enforced: ${inZone.speed_limit} km/h.`,
            sound: "zone"
          });
        }
        this.logClientEvent("DANGER ZONE", `Entered zone ${inZone.name}. Limit: ${inZone.speed_limit} km/h.`);
      }
    } else {
      if (this.activeZone !== "NONE") {
        this.activeZone = "NONE";
        this.speedLimit = 60.0;
        this.targetSpeed = 45.0;
        this.logClientEvent("ZONE EXIT", "Exited danger zone. Restored standard speed limit.");
      }
    }

    // Normal sensor variance
    if (!this.activeScenario) {
      this.alcohol = Math.max(120, Math.min(260, this.alcohol + Math.floor((Math.random() - 0.5) * 8)));
      if (this.speed > 5) {
        this.obstacleDist = Math.max(45, Math.min(320, this.obstacleDist + Math.floor((Math.random() - 0.5) * 16)));
      }
      this.tilt = parseFloat((2.5 + Math.random() * 1.5).toFixed(1));
      this.ax = parseFloat(((Math.random() - 0.5) * 0.08).toFixed(2));
      this.ay = parseFloat(((Math.random() - 0.5) * 0.08).toFixed(2));
    }

    // Build telemetry packet matching ESP32 protocol
    const packet = {
      t: Date.now(),
      spd: parseFloat(this.speed.toFixed(1)),
      alc: parseInt(this.alcohol),
      dist: parseInt(this.obstacleDist),
      ax: this.ax,
      ay: this.ay,
      az: this.az,
      tilt: this.tilt,
      lat: parseFloat(this.lat.toFixed(6)),
      lon: parseFloat(this.lon.toFixed(6)),
      fix: 1,
      state: this.state,
      auth: this.auth,
      signal: this.signal,
      zone: this.activeZone,
      evt: this.currentEvent,
      driver_name: this.auth === 1 ? "Muniyappan" : "Unknown Driver",
      server_time: new Date().toLocaleTimeString(),
      esp32_connected: false,
      mode: "simulation"
    };

    // Feed to dashboard UI
    if (this.controller) {
      this.controller.handleIncomingTelemetry(packet);
    }
  }

  logClientEvent(type, details) {
    const events = JSON.parse(localStorage.getItem("accialert_events") || "[]");
    const newEv = {
      id: events.length + 1,
      event_type: type,
      timestamp: new Date().toLocaleString(),
      driver_name: this.auth === 1 ? "Muniyappan" : "Unknown Driver",
      vehicle_speed: this.speed.toFixed(1),
      latitude: this.lat.toFixed(4),
      longitude: this.lon.toFixed(4),
      danger_zone_name: this.activeZone === "NONE" ? "Open Highway" : this.activeZone,
      details: details || ""
    };
    events.unshift(newEv);
    localStorage.setItem("accialert_events", JSON.stringify(events.slice(0, 200)));

    if (this.controller && typeof this.controller.renderEventsTable === "function") {
      this.controller.renderEventsTable(events);
    }
  }
}

window.ClientSimulationEngine = ClientSimulationEngine;

