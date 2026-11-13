/**
 * AcciAlert - Master Dashboard Application Controller
 * Handles Socket.IO communication, card updates, control panel actions,
 * event filtering, CSV export, and automatic fallback to standalone Client Simulator
 * when running frontend-only without a Python backend.
 * Author: Muniyappan
 */

class DashboardController {
  constructor() {
    this.socket = null;
    this.latestTelemetry = null;
    this.systemMode = "simulation";
    this.activeDriver = "Muniyappan";
    this.backendConnected = false;
    this.clientSimulator = new ClientSimulationEngine(this);

    this.bindEvents();
    this.initSocket();
    this.loadEvents();

    // If socket hasn't connected after 1.5 seconds, start client simulation automatically
    setTimeout(() => {
      if (!this.backendConnected) {
        console.log("No backend detected. Starting in Standalone Frontend Mode.");
        this.clientSimulator.start();
        this.updateConnectionBadge(true, "FRONTEND STANDALONE ACTIVE");
        this.showToast("Running in Standalone Frontend Mode", "info");
      }
    }, 1500);
  }

  // ===================================================================
  // SOCKET.IO & WEBSOCKET SETUP
  // ===================================================================
  initSocket() {
    if (typeof io !== "function" || window.location.protocol === "file:") {
      this.clientSimulator.start();
      this.updateConnectionBadge(true, "STANDALONE MODE");
      return;
    }

    try {
      this.socket = io({
        transports: ["websocket", "polling"],
        reconnectionAttempts: 3,
        timeout: 2000
      });

      this.socket.on("connect", () => {
        this.backendConnected = true;
        this.clientSimulator.stop();
        this.updateConnectionBadge(true, "SYSTEM ACTIVE");
        this.showToast("Connected to AcciAlert Backend Server", "success");
        this.loadSystemStatus();
      });

      this.socket.on("connect_error", () => {
        if (!this.backendConnected) {
          this.clientSimulator.start();
          this.updateConnectionBadge(true, "STANDALONE MODE");
        }
      });

      this.socket.on("disconnect", () => {
        this.backendConnected = false;
        this.clientSimulator.start();
        this.updateConnectionBadge(true, "STANDALONE MODE");
      });

      this.socket.on("status", (data) => {
        if (data.mode) {
          this.systemMode = data.mode;
          this.updateModeUI(data.mode);
        }
        if (data.driver) {
          this.activeDriver = data.driver;
        }
      });

      this.socket.on("telemetry", (telemetry) => {
        this.handleIncomingTelemetry(telemetry);
      });

      this.socket.on("alert", (alertData) => {
        if (window.AlertManager) {
          window.AlertManager.triggerAlert(alertData);
        }
      });
    } catch (e) {
      console.warn("Socket.io init exception, switching to client simulator:", e);
      this.clientSimulator.start();
      this.updateConnectionBadge(true, "STANDALONE MODE");
    }
  }

  // ===================================================================
  // TELEMETRY PROCESSING & CARD UPDATES (10 VALUES)
  // ===================================================================
  handleIncomingTelemetry(t) {
    this.latestTelemetry = t;

    // 1. Vehicle Speed
    const spd = typeof t.spd === "number" ? t.spd : 0.0;
    const speedEl = document.getElementById("val-speed");
    const speedFillEl = document.getElementById("fill-speed");
    if (speedEl) speedEl.innerText = spd.toFixed(1);
    if (speedFillEl) {
      const pct = Math.min(100, Math.max(0, (spd / 100) * 100));
      speedFillEl.style.width = `${pct}%`;
      speedFillEl.classList.toggle("danger", spd >= 80);
      speedFillEl.classList.toggle("warning", spd >= 50 && spd < 80);
    }

    // 2. Alcohol Sensor Level
    const alc = typeof t.alc === "number" ? t.alc : 0;
    const alcEl = document.getElementById("val-alcohol");
    const alcBadgeEl = document.getElementById("badge-alcohol");
    const alcFillEl = document.getElementById("fill-alcohol");
    if (alcEl) alcEl.innerText = alc;
    if (alcBadgeEl) {
      if (alc >= 600) {
        alcBadgeEl.innerText = "DANGER";
        alcBadgeEl.className = "badge badge-danger";
      } else if (alc >= 350) {
        alcBadgeEl.innerText = "CAUTION";
        alcBadgeEl.className = "badge badge-warning";
      } else {
        alcBadgeEl.innerText = "NORMAL";
        alcBadgeEl.className = "badge badge-success";
      }
    }
    if (alcFillEl) {
      const alcPct = Math.min(100, (alc / 1023) * 100);
      alcFillEl.style.width = `${alcPct}%`;
      alcFillEl.classList.toggle("danger", alc >= 600);
      alcFillEl.classList.toggle("warning", alc >= 350 && alc < 600);
    }

    // 3. Obstacle Distance
    const dist = typeof t.dist === "number" ? t.dist : 999;
    const distEl = document.getElementById("val-obstacle");
    const distBadgeEl = document.getElementById("badge-obstacle");
    const distFillEl = document.getElementById("fill-obstacle");
    if (distEl) distEl.innerText = dist > 400 ? "> 400" : Math.round(dist);
    if (distBadgeEl) {
      if (dist <= 40) {
        distBadgeEl.innerText = "IMMINENT";
        distBadgeEl.className = "badge badge-danger";
      } else if (dist <= 100) {
        distBadgeEl.innerText = "WARNING";
        distBadgeEl.className = "badge badge-warning";
      } else {
        distBadgeEl.innerText = "CLEAR";
        distBadgeEl.className = "badge badge-success";
      }
    }
    if (distFillEl) {
      const distPct = Math.min(100, (dist / 300) * 100);
      distFillEl.style.width = `${distPct}%`;
      distFillEl.classList.toggle("danger", dist <= 40);
      distFillEl.classList.toggle("warning", dist > 40 && dist <= 100);
    }

    // 4. Accident / Tilt Status
    const tilt = typeof t.tilt === "number" ? t.tilt : 0.0;
    const tiltEl = document.getElementById("val-tilt");
    const tiltBadgeEl = document.getElementById("badge-tilt");
    const accelEl = document.getElementById("val-accel");
    if (tiltEl) tiltEl.innerText = `${tilt.toFixed(1)}°`;
    if (accelEl) {
      accelEl.innerText = `X:${(t.ax || 0).toFixed(2)} Y:${(t.ay || 0).toFixed(2)} Z:${(t.az || 1).toFixed(2)}g`;
    }
    if (tiltBadgeEl) {
      if (tilt >= 45.0 || (t.evt && t.evt.includes("ACCIDENT"))) {
        tiltBadgeEl.innerText = "ACCIDENT";
        tiltBadgeEl.className = "badge badge-danger";
      } else if (tilt >= 20.0) {
        tiltBadgeEl.innerText = "WARNING";
        tiltBadgeEl.className = "badge badge-warning";
      } else {
        tiltBadgeEl.innerText = "NORMAL";
        tiltBadgeEl.className = "badge badge-success";
      }
    }

    // 5. GPS Latitude & Longitude
    const lat = typeof t.lat === "number" ? t.lat : 0.0;
    const lon = typeof t.lon === "number" ? t.lon : 0.0;
    const gpsEl = document.getElementById("val-gps");
    if (gpsEl) gpsEl.innerText = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;

    // 6. Driver Name and Authorisation Status
    const auth = t.auth === 1;
    const driverName = t.driver_name || (auth ? this.activeDriver : "Unknown Driver");
    const driverEl = document.getElementById("val-driver");
    const authBadgeEl = document.getElementById("badge-driver-auth");
    if (driverEl) driverEl.innerText = driverName;
    if (authBadgeEl) {
      if (auth) {
        authBadgeEl.innerText = "AUTHORIZED";
        authBadgeEl.className = "badge badge-success";
      } else {
        authBadgeEl.innerText = "UNAUTHORIZED";
        authBadgeEl.className = "badge badge-danger";
      }
    }

    // 7. Traffic Signal Status
    const signal = (t.signal || "GREEN").toUpperCase();
    const bulbRed = document.getElementById("bulb-red");
    const bulbYellow = document.getElementById("bulb-yellow");
    const bulbGreen = document.getElementById("bulb-green");
    const signalLabel = document.getElementById("val-signal");

    if (bulbRed) bulbRed.classList.toggle("active", signal === "RED");
    if (bulbYellow) bulbYellow.classList.toggle("active", signal === "YELLOW");
    if (bulbGreen) bulbGreen.classList.toggle("active", signal === "GREEN");
    if (signalLabel) signalLabel.innerText = signal;

    // 8. Vehicle Status (LOCKED / READY / RUNNING / WARNING / EMERGENCY)
    const stateStr = (t.state || "READY").toUpperCase();
    const stateEl = document.getElementById("val-vehicle-state");
    const stateCardEl = document.getElementById("card-vehicle-state");
    if (stateEl) stateEl.innerText = stateStr;
    if (stateCardEl) {
      stateCardEl.classList.remove("accent-red", "accent-orange");
      if (stateStr === "EMERGENCY" || stateStr === "LOCKED") {
        stateCardEl.classList.add("accent-red");
      } else if (stateStr === "WARNING") {
        stateCardEl.classList.add("accent-orange");
      }
    }

    // 9. ESP32 Connection Status
    const espBadgeEl = document.getElementById("badge-esp32-status");
    const espPortEl = document.getElementById("val-esp32-port");
    if (espBadgeEl) {
      if (this.systemMode === "simulation" || !this.backendConnected) {
        espBadgeEl.innerText = "SIMULATED";
        espBadgeEl.className = "badge badge-info";
        if (espPortEl) espPortEl.innerText = "Virtual Driver Engine";
      } else {
        const isConn = t.esp32_connected;
        espBadgeEl.innerText = isConn ? "CONNECTED" : "DISCONNECTED";
        espBadgeEl.className = `badge ${isConn ? 'badge-success' : 'badge-muted'}`;
        if (espPortEl) espPortEl.innerText = isConn ? "USB Serial 115200" : "Hardware Offline";
      }
    }

    // 10. GPS Connection Status
    const fix = t.fix === 1;
    const gpsFixBadge = document.getElementById("badge-gps-fix");
    if (gpsFixBadge) {
      gpsFixBadge.innerText = fix ? "3D FIX" : "NO FIX";
      gpsFixBadge.className = `badge ${fix ? 'badge-success' : 'badge-danger'}`;
    }

    // Current Zone readout
    const zoneCode = t.zone || "NONE";
    const zoneBadgeEl = document.getElementById("val-current-zone");
    if (zoneBadgeEl) {
      zoneBadgeEl.innerText = zoneCode === "NONE" ? "Open Highway" : zoneCode;
    }

    // Feed to Live Charts
    if (window.ChartsManager) {
      window.ChartsManager.update(spd, alc, t.server_time);
    }

    // Feed to GPS Map
    if (window.MapManager) {
      window.MapManager.updateVehiclePosition(lat, lon, spd, stateStr, driverName);
    }
  }

  // ===================================================================
  // CONTROL PANEL COMMANDS & ACTIONS
  // ===================================================================
  sendCommand(command, value = null) {
    if (!this.backendConnected) {
      this.clientSimulator.handleCommand(command, value);
      this.showToast(`Command ${command}${value ? ' (' + value + ')' : ''} executed (Local).`, "success");
      return;
    }

    const payload = { command, value };
    fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then(res => res.json())
      .then(data => {
        if (data.status === "success") {
          this.showToast(`Command ${command}${value ? ' (' + value + ')' : ''} executed.`, "success");
        } else {
          this.clientSimulator.handleCommand(command, value);
        }
      })
      .catch(() => {
        this.clientSimulator.handleCommand(command, value);
        this.showToast(`Command ${command} executed (Local).`, "success");
      });
  }

  emergencyStop() {
    this.sendCommand("STOP");
    if (window.AlertManager) {
      window.AlertManager.playSound("accident");
    }
  }

  resetSystem() {
    this.sendCommand("RESET");
    if (window.AlertManager) {
      window.AlertManager.acknowledgeAlert();
    }
  }

  setTrafficSignal(color) {
    this.sendCommand("SIGNAL", color);
  }

  toggleDriverAuth() {
    const newAuth = this.latestTelemetry && this.latestTelemetry.auth === 1 ? 0 : 1;
    this.sendCommand("AUTH", newAuth);
  }

  applySpeedLimit(limitValue) {
    const limit = limitValue || parseFloat(document.getElementById("input-custom-speed-limit").value);
    if (!isNaN(limit) && limit > 0) {
      this.sendCommand("LIMIT", limit);
    } else {
      this.showToast("Enter a valid speed limit in km/h", "warning");
    }
  }

  triggerScenario(scenarioName) {
    if (!this.backendConnected) {
      this.clientSimulator.triggerScenario(scenarioName);
      this.showToast(`Scenario triggered: ${scenarioName.toUpperCase()}`, "info");
      return;
    }

    fetch("/api/simulation/scenario", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario: scenarioName, duration: 20 })
    })
      .then(res => res.json())
      .then(() => {
        this.showToast(`Simulation scenario triggered: ${scenarioName.toUpperCase()}`, "info");
      })
      .catch(() => {
        this.clientSimulator.triggerScenario(scenarioName);
        this.showToast(`Scenario triggered: ${scenarioName.toUpperCase()} (Local)`, "info");
      });
  }

  // ===================================================================
  // SYSTEM MODE SWITCHING (SIMULATION vs REAL SERIAL)
  // ===================================================================
  switchMode(targetMode) {
    if (!this.backendConnected) {
      this.systemMode = targetMode;
      this.updateModeUI(targetMode);
      this.showToast(`Switched to ${targetMode.toUpperCase()} mode (Frontend Only).`, "info");
      return;
    }

    const port = document.getElementById("select-serial-port")?.value || "COM3";
    const baud = parseInt(document.getElementById("select-serial-baud")?.value) || 115200;

    fetch("/api/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: targetMode, port, baudrate: baud })
    })
      .then(res => res.json())
      .then(data => {
        if (data.status === "success") {
          this.systemMode = data.mode;
          this.updateModeUI(data.mode);
          this.showToast(`Switched to ${data.mode.toUpperCase()} mode.`, "success");
        }
      })
      .catch(() => {
        this.systemMode = targetMode;
        this.updateModeUI(targetMode);
      });
  }

  updateModeUI(mode) {
    const btnSim = document.getElementById("btn-mode-sim");
    const btnSerial = document.getElementById("btn-mode-serial");
    const serialConfigPanel = document.getElementById("serial-hardware-panel");
    const headerModeBadge = document.getElementById("header-mode-badge");

    if (mode === "simulation") {
      if (btnSim) btnSim.className = "btn btn-teal btn-sm";
      if (btnSerial) btnSerial.className = "btn btn-outline btn-sm";
      if (serialConfigPanel) serialConfigPanel.style.display = "none";
      if (headerModeBadge) {
        headerModeBadge.innerText = "SIMULATION ACTIVE";
        headerModeBadge.className = "badge badge-info";
      }
    } else {
      if (btnSim) btnSim.className = "btn btn-outline btn-sm";
      if (btnSerial) btnSerial.className = "btn btn-teal btn-sm";
      if (serialConfigPanel) serialConfigPanel.style.display = "block";
      if (headerModeBadge) {
        headerModeBadge.innerText = "HARDWARE SERIAL ACTIVE";
        headerModeBadge.className = "badge badge-warning";
      }
    }
  }

  loadSystemStatus() {
    fetch("/api/status")
      .then(res => res.json())
      .then(data => {
        if (data.mode) {
          this.systemMode = data.mode;
          this.updateModeUI(data.mode);
        }
        if (data.active_driver) {
          this.activeDriver = data.active_driver;
        }
      })
      .catch(() => {});
  }

  // ===================================================================
  // EVENT MANAGEMENT & CSV EXPORT
  // ===================================================================
  loadEvents() {
    fetch("/api/events?limit=50")
      .then(res => {
        if (!res.ok) throw new Error("Offline");
        return res.json();
      })
      .then(data => {
        if (data.status === "success" && data.data) {
          this.renderEventsTable(data.data.events);
          const counterEl = document.getElementById("events-count-badge");
          if (counterEl) counterEl.innerText = `${data.data.total} Total`;
        } else {
          this.loadLocalEvents();
        }
      })
      .catch(() => {
        this.loadLocalEvents();
      });
  }

  loadLocalEvents() {
    const events = JSON.parse(localStorage.getItem("accialert_events") || "[]");
    this.renderEventsTable(events);
    const counterEl = document.getElementById("events-count-badge");
    if (counterEl) counterEl.innerText = `${events.length} Total`;
  }

  renderEventsTable(events) {
    const tbody = document.getElementById("events-table-body");
    if (!tbody) return;

    if (!events || events.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="8" style="text-align:center; color:#627289; padding:24px;">
            No safety events recorded yet. Trigger a test scenario below to log events.
          </td>
        </tr>
      `;
      return;
    }

    const typeBadges = {
      "ACCIDENT": "badge-danger",
      "ALCOHOL DETECTED": "badge-danger",
      "UNAUTHORISED DRIVER": "badge-danger",
      "OBSTACLE DETECTED": "badge-warning",
      "OVERSPEED": "badge-warning",
      "DANGER ZONE": "badge-warning",
      "TRAFFIC SIGNAL STOP": "badge-warning",
      "ZONE EXIT": "badge-info",
      "ZONE_ENTER": "badge-warning",
      "ZONE_EXIT": "badge-info",
      "OVERSPEED_IN_ZONE": "badge-danger",
      "EMERGENCY STOP": "badge-danger"
    };

    let html = "";
    events.forEach(ev => {
      const badgeClass = typeBadges[ev.event_type] || "badge-info";
      html += `
        <tr>
          <td><code style="color:#627289;">#${ev.id}</code></td>
          <td><span class="badge ${badgeClass}">${ev.event_type}</span></td>
          <td style="color:#94A3B8; font-size:0.78rem;">${ev.timestamp}</td>
          <td><b>${ev.driver_name || 'Unknown'}</b></td>
          <td><span style="color:#E6C34A; font-weight:700;">${ev.vehicle_speed} km/h</span></td>
          <td><code style="color:#94A3B8; font-size:0.75rem;">${ev.latitude}, ${ev.longitude}</code></td>
          <td>${ev.danger_zone_name || 'None'}</td>
          <td style="color:#94A3B8; font-size:0.78rem;">${ev.details || ''}</td>
        </tr>
      `;
    });

    tbody.innerHTML = html;
  }

  exportEventsCSV() {
    if (this.backendConnected) {
      window.location.href = "/api/events/export";
      this.showToast("Downloading events CSV export...", "info");
      return;
    }

    // Client-side CSV generation
    const events = JSON.parse(localStorage.getItem("accialert_events") || "[]");
    let csvContent = "Event ID,Event Type,Date & Time,Driver Name,Speed (km/h),Latitude,Longitude,Danger Zone,Details\n";

    events.forEach(ev => {
      csvContent += `"${ev.id}","${ev.event_type}","${ev.timestamp}","${ev.driver_name}","${ev.vehicle_speed}","${ev.latitude}","${ev.longitude}","${ev.danger_zone_name}","${ev.details}"\n`;
    });

    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `accialert_events_${new Date().toISOString().slice(0,10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    this.showToast("Downloaded events CSV export.", "success");
  }

  // ===================================================================
  // UI INTERACTIONS & EVENT BINDINGS
  // ===================================================================
  bindEvents() {
    // Tab Switching
    document.querySelectorAll(".tab-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const tabTarget = btn.getAttribute("data-tab");
        document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

        btn.classList.add("active");
        const targetContent = document.getElementById(tabTarget);
        if (targetContent) targetContent.classList.add("active");

        if (window.MapManager && window.MapManager.map) {
          setTimeout(() => window.MapManager.map.invalidateSize(), 150);
        }
      });
    });

    // Control Panel Action Buttons
    document.getElementById("btn-emergency-stop")?.addEventListener("click", () => this.emergencyStop());
    document.getElementById("btn-reset-system")?.addEventListener("click", () => this.resetSystem());
    document.getElementById("btn-ack-alert")?.addEventListener("click", () => {
      if (window.AlertManager) window.AlertManager.acknowledgeAlert();
    });
    document.getElementById("btn-banner-ack")?.addEventListener("click", () => {
      if (window.AlertManager) window.AlertManager.acknowledgeAlert();
    });

    // Traffic Signal Override Buttons
    document.getElementById("btn-signal-red")?.addEventListener("click", () => this.setTrafficSignal("RED"));
    document.getElementById("btn-signal-yellow")?.addEventListener("click", () => this.setTrafficSignal("YELLOW"));
    document.getElementById("btn-signal-green")?.addEventListener("click", () => this.setTrafficSignal("GREEN"));

    // Driver Auth Toggle
    document.getElementById("btn-auth-driver")?.addEventListener("click", () => this.toggleDriverAuth());

    // Speed Limit Button
    document.getElementById("btn-apply-limit")?.addEventListener("click", () => this.applySpeedLimit());

    // Simulation Scenario Buttons
    document.querySelectorAll(".btn-scenario").forEach(btn => {
      btn.addEventListener("click", () => {
        const scenario = btn.getAttribute("data-scenario");
        if (scenario) this.triggerScenario(scenario);
      });
    });

    // Mode Switch Buttons
    document.getElementById("btn-mode-sim")?.addEventListener("click", () => this.switchMode("simulation"));
    document.getElementById("btn-mode-serial")?.addEventListener("click", () => this.switchMode("serial"));

    // Event Filters
    document.getElementById("btn-filter-apply")?.addEventListener("click", () => this.loadEvents());
    document.getElementById("btn-filter-reset")?.addEventListener("click", () => {
      const typeEl = document.getElementById("filter-event-type");
      if (typeEl) typeEl.value = "ALL";
      this.loadEvents();
    });
    document.getElementById("btn-export-csv")?.addEventListener("click", () => this.exportEventsCSV());

    // Audio Mute Toggle
    document.getElementById("btn-mute-toggle")?.addEventListener("click", () => {
      if (window.AlertManager) window.AlertManager.toggleMute();
    });

    // Map Utility Buttons
    document.getElementById("btn-map-center")?.addEventListener("click", () => {
      if (window.MapManager) window.MapManager.centerOnVehicle();
    });
    document.getElementById("btn-map-follow")?.addEventListener("click", () => {
      if (window.MapManager) window.MapManager.toggleFollow();
    });
    document.getElementById("btn-map-clear-trail")?.addEventListener("click", () => {
      if (window.MapManager) window.MapManager.clearTrail();
    });

    // Zone Modal Trigger
    document.getElementById("btn-open-add-zone")?.addEventListener("click", () => {
      if (window.ZoneApp) window.ZoneApp.openModal();
    });
    document.getElementById("btn-modal-close")?.addEventListener("click", () => {
      if (window.ZoneApp) window.ZoneApp.closeModal();
    });
    document.getElementById("btn-modal-cancel")?.addEventListener("click", () => {
      if (window.ZoneApp) window.ZoneApp.closeModal();
    });
  }

  updateConnectionBadge(connected, labelText) {
    const dot = document.getElementById("header-pulse-dot");
    const text = document.getElementById("header-status-text");
    if (dot && text) {
      if (connected) {
        dot.className = "pulse-dot";
        text.innerText = labelText || "SYSTEM ACTIVE";
      } else {
        dot.className = "pulse-dot danger";
        text.innerText = "DISCONNECTED";
      }
    }
  }

  showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    const icon = type === "error" ? "bi-x-circle-fill" : type === "warning" ? "bi-exclamation-triangle-fill" : "bi-check-circle-fill";

    toast.innerHTML = `
      <i class="bi ${icon}" style="font-size:1.1rem;"></i>
      <span>${message}</span>
    `;

    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateX(100%)";
      toast.style.transition = "all 0.3s ease";
      setTimeout(() => toast.remove(), 300);
    }, 3200);
  }
}

// Global bootstrap on window load
window.addEventListener("DOMContentLoaded", () => {
  window.ChartsManager = new LiveChartsManager();
  window.MapManager = new VehicleMapManager();
  window.ZoneApp = new ZoneManager();
  window.DashboardApp = new DashboardController();
});
