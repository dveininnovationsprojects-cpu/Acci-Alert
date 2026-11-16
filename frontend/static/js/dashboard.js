/**
 * AcciAlert - Master Dashboard Application Controller
 * Handles Socket.IO communication, card updates, control panel actions,
 * event filtering and CSV export. Only real readings from the ESP32 are shown: there is no simulated data.
 * Author: Muniyappan
 */

class DashboardController {
  constructor() {
    this.socket = null;
    this.latestTelemetry = null;
    this.systemMode = "waiting";    // "waiting" until the first real reading, then "serial" (live)
    this.activeDriver = "Unknown";
    this.backendConnected = false;
    this.lastTelemetryAt = null;   // when the last reading from the server arrived
    this.dataStale = false;        // true while the vehicle has stopped sending

    this.bindEvents();
    this.initSmsSwitch();
    this.initSocket();
    this.loadEvents();

    // Every second: warn when the server is reachable but the vehicle has stopped sending readings
    setInterval(() => this.checkDataAge(), 1000);

    // The laptop's own location is the fallback position while the GPS module has no fix
    this.startLaptopLocation();

    setTimeout(() => {
      if (!this.backendConnected) {
        this.updateConnectionBadge(false, "OFFLINE: cannot reach the AcciAlert server");
      }
    }, 1500);
  }

  // ===================================================================
  // LAPTOP LOCATION (used as the car position only while the GPS module has no fix)
  // ===================================================================
  startLaptopLocation() {
    if (!navigator.geolocation) return;
    let lastSent = 0;
    let warned = false;
    navigator.geolocation.watchPosition(
      (pos) => {
        // the map shows this device's live location at once, on every update
        if (window.MapManager) window.MapManager.updateLiveLocation(pos.coords.latitude, pos.coords.longitude, pos.coords.accuracy);
        const now = Date.now();
        if (now - lastSent < 5000) return;          // the server needs one position every few seconds
        lastSent = now;
        fetch("/api/laptop-location", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy_m: pos.coords.accuracy })
        }).catch(() => {});
      },
      (err) => {
        if (window.MapManager) window.MapManager.liveLocationOff(err && err.code === 1 ? "Live location: blocked (allow location in the browser)" : "Live location: not available");
        if (!warned) {
          warned = true;
          this.showToast("Laptop location is off. Allow location access to see this laptop's position when the GPS module has no fix.", "warning");
        }
      },
      { enableHighAccuracy: true, maximumAge: 10000, timeout: 30000 }
    );
  }

  // ===================================================================
  // SOCKET.IO & WEBSOCKET SETUP
  // ===================================================================
  initSocket() {
    if (typeof io !== "function" || window.location.protocol === "file:") {
      this.updateConnectionBadge(false, "OFFLINE: open the page from the AcciAlert server");
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
        this.updateConnectionBadge(true, "System active");
        this.showToast("Connected to AcciAlert Backend Server", "success");
        this.loadSystemStatus();
      });

      this.socket.on("connect_error", () => {
        if (!this.backendConnected) {
          this.updateConnectionBadge(false, "OFFLINE: cannot reach the AcciAlert server");
        }
      });

      this.socket.on("disconnect", () => {
        this.backendConnected = false;
        this.updateConnectionBadge(false, "OFFLINE: cannot reach the AcciAlert server");
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

      // A zone was added, edited or deleted (in the zone manager or in the zone tab)
      this.socket.on("zone_settings", (d) => {                      // the notification distance was changed in Danger Zone Management
        if (window.MapManager && d && d.alert_distance_m) window.MapManager.setAlertDistance(d.alert_distance_m);
      });

      this.socket.on("zones_updated", () => {
        if (window.ZoneApp) window.ZoneApp.loadZones();
      });

      this.socket.on("alert", (alertData) => {
        if (window.AlertManager) {
          window.AlertManager.triggerAlert(alertData);
        }
      });
    } catch (e) {
      console.warn("Socket.io init exception:", e);
      this.updateConnectionBadge(false, "OFFLINE: cannot reach the AcciAlert server");
    }
  }

  // ===================================================================
  // TELEMETRY PROCESSING & CARD UPDATES (10 VALUES)
  // ===================================================================
  handleIncomingTelemetry(t) {
    this.latestTelemetry = t;
    this.lastTelemetryAt = Date.now();
    if (this.dataStale) {
      this.dataStale = false;
      this.updateConnectionBadge(true, "System active");
      this.showToast("Vehicle data is arriving again.", "success");
    }

    // 1. Vehicle Speed
    const spd = typeof t.spd === "number" ? t.spd : 0.0;
    const speedEl = document.getElementById("val-speed");
    const speedFillEl = document.getElementById("fill-speed");
    if (speedEl) speedEl.innerText = spd.toFixed(1);
    if (speedFillEl) {
      const pct = Math.min(100, Math.max(0, (spd / 25) * 100));          // the bar is full at the 25 km/h limit
      speedFillEl.style.width = `${pct}%`;
      speedFillEl.classList.toggle("danger", spd > 25);
      speedFillEl.classList.toggle("warning", spd >= 20 && spd <= 25);
    }

    // 2. Alcohol Sensor Level
    const alc = typeof t.alc === "number" ? t.alc : 0;
    const alcEl = document.getElementById("val-alcohol");
    const alcBadgeEl = document.getElementById("badge-alcohol");
    const alcFillEl = document.getElementById("fill-alcohol");
    if (alcEl) alcEl.innerText = alc;
    if (alcBadgeEl) {
      if (alc >= 220) {
        alcBadgeEl.innerText = "Danger";
        alcBadgeEl.className = "badge badge-danger";
      } else if (alc >= 180) {
        alcBadgeEl.innerText = "Caution";
        alcBadgeEl.className = "badge badge-warning";
      } else {
        alcBadgeEl.innerText = "Normal";
        alcBadgeEl.className = "badge badge-success";
      }
    }
    if (alcFillEl) {
      const alcPct = Math.min(100, (alc / 330) * 100);      // bar is full at 1.5 x the alert level (220)
      alcFillEl.style.width = `${alcPct}%`;
      alcFillEl.classList.toggle("danger", alc >= 220);
      alcFillEl.classList.toggle("warning", alc >= 180 && alc < 220);
    }

    // 3. Obstacle Distance
    const dist = typeof t.dist === "number" ? t.dist : 999;
    const distEl = document.getElementById("val-obstacle");
    const distBadgeEl = document.getElementById("badge-obstacle");
    const distFillEl = document.getElementById("fill-obstacle");
    if (distEl) distEl.innerText = dist > 400 ? "> 400" : Math.round(dist);
    if (distBadgeEl) {
      if (t.no_echo === true || dist < 4.5) {
        distBadgeEl.innerText = "Crash";                // no echo ("none" / "> 400") or 4 cm and less: the CAR CRASHED alert
        distBadgeEl.className = "badge badge-danger";
      } else if (dist <= 25) {
        distBadgeEl.innerText = "Imminent";
        distBadgeEl.className = "badge badge-danger";
      } else if (dist <= 40) {
        distBadgeEl.innerText = "Warning";
        distBadgeEl.className = "badge badge-warning";
      } else {
        distBadgeEl.innerText = "Clear";
        distBadgeEl.className = "badge badge-success";
      }
    }
    if (distFillEl) {
      const distPct = Math.min(100, (dist / 300) * 100);
      distFillEl.style.width = `${distPct}%`;
      distFillEl.classList.toggle("danger", dist <= 25);
      distFillEl.classList.toggle("warning", dist > 25 && dist <= 40);
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
      // a big tilt is an accident only after a knock or fast spin (t.tilt_violent); turned slowly by hand it is a warning
      if ((tilt >= 50.0 && t.tilt_violent !== false) || (t.evt && t.evt.includes("ACCIDENT"))) {
        tiltBadgeEl.innerText = "Accident";
        tiltBadgeEl.className = "badge badge-danger";
      } else if (tilt >= 25.0) {
        tiltBadgeEl.innerText = "Warning";
        tiltBadgeEl.className = "badge badge-warning";
      } else {
        tiltBadgeEl.innerText = "Normal";
        tiltBadgeEl.className = "badge badge-success";
      }
    }

    // 5. GPS Latitude & Longitude
    // The GPS module is the main reading. With no GPS fix the server sends this laptop's location instead.
    const hasPosition = typeof t.lat === "number" && typeof t.lon === "number";
    const lat = hasPosition ? t.lat : null;
    const lon = hasPosition ? t.lon : null;
    const gpsEl = document.getElementById("val-gps");
    if (gpsEl) gpsEl.innerText = hasPosition ? `${lat.toFixed(4)}, ${lon.toFixed(4)}` : "No position";

    // 6. Driver Name and Authorisation Status
    const auth = t.auth === 1;
    const driverName = t.driver || t.driver_name || (auth && this.activeDriver !== "Unknown" ? this.activeDriver : "Unknown");
    const driverEl = document.getElementById("val-driver");
    const authBadgeEl = document.getElementById("badge-driver-auth");
    if (driverEl) driverEl.innerText = driverName;
    if (authBadgeEl) {
      if (auth && driverName !== "Unknown") {
        authBadgeEl.innerText = "Authorized";
        authBadgeEl.className = "badge badge-success";
      } else {
        authBadgeEl.innerText = "Unauthorized";
        authBadgeEl.className = "badge badge-danger";
      }
    }

    // 7. Vehicle Status (LOCKED / READY / RUNNING / WARNING / EMERGENCY)
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
      espBadgeEl.innerText = "Connected";
      espBadgeEl.className = "badge badge-success";
      if (espPortEl) espPortEl.innerText = t.source === "ESP32_WIFI" ? "Wi-Fi" : "USB serial 115200";
    }

    // 10. GPS Connection Status
    const fix = t.fix === 1;
    const gpsFixBadge = document.getElementById("badge-gps-fix");
    if (gpsFixBadge) {
      if (t.position_source === "LAPTOP") {
        const acc = typeof t.position_accuracy_m === "number" ? ` (±${Math.round(t.position_accuracy_m)} m)` : "";
        gpsFixBadge.innerText = `Location${acc}`;
        gpsFixBadge.className = "badge badge-warning";
      } else if (t.position_source === "NONE" || !hasPosition) {
        gpsFixBadge.innerText = "Waiting";              // no GPS fix and no laptop location yet
        gpsFixBadge.className = "badge badge-muted";
      } else {
        gpsFixBadge.innerText = fix ? "GPS fix" : "No fix";
        gpsFixBadge.className = `badge ${fix ? 'badge-success' : 'badge-danger'}`;
      }
    }

    // Current Zone readout
    const zoneCode = t.zone || "NONE";
    const zoneBadgeEl = document.getElementById("val-current-zone");
    if (zoneBadgeEl) {
      zoneBadgeEl.innerText = zoneCode === "None" ? "Open Highway" : zoneCode;
    }

    // Feed to Live Charts
    if (window.ChartsManager) {
      window.ChartsManager.update(spd, alc, t.timestamp, t.epoch);
    }

    // Feed to GPS Map
    if (window.MapManager) {
      if (hasPosition) {
        window.MapManager.updateVehiclePosition(lat, lon, spd, stateStr, driverName, t.position_source);
      } else {
        window.MapManager.clearVehicle();
      }
    }
  }

  // ===================================================================
  // CONTROL PANEL COMMANDS & ACTIONS
  // ===================================================================
  sendCommand(command, value = null) {
    if (!this.backendConnected) {
      this.showToast("The server cannot be reached. The command was NOT sent.", "danger");
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
          this.showToast(`Command ${command}${value ? ' (' + value + ')' : ''} sent.`, "success");
        } else {
          this.showToast(`Command ${command} was refused: ${data.message || "unknown reason"}`, "danger");
        }
      })
      .catch(() => {
        this.showToast(`Command ${command} was NOT sent: the server did not answer.`, "danger");
      });
  }

  // SOS button (top right of the dashboard): records the emergency and texts the owner with the car's position
  async sendSos() {
    const btn = document.getElementById("btn-sos");
    const yes = await AppDialog.confirm({
      title: "Send SOS alert?",
      message: "An emergency text with the car's position will be sent to the owner now.",
      confirmText: "Send SOS", cancelText: "Cancel", danger: true,
    });
    if (!yes) return;
    if (btn) btn.disabled = true;
    try {
      const res = await fetch("/api/sos", { method: "POST" });
      const body = await res.json().catch(() => ({}));
      this.showToast(body.message || "SOS could not be sent.", res.ok && body.texted ? "success" : "danger");
      if (window.AlertManager && res.ok) window.AlertManager.playSound("accident");
    } catch (_) {
      this.showToast("SOS was NOT sent: the server did not answer.", "danger");
    } finally {
      if (btn) setTimeout(() => { btn.disabled = false; }, 3000);
    }
  }

  resetSystem() {
    this.sendCommand("RESET");
    if (window.AlertManager) {
      window.AlertManager.acknowledgeAlert();
    }
  }

  applySpeedLimit(limitValue) {
    const limit = limitValue || parseFloat(document.getElementById("input-custom-speed-limit").value);
    if (!isNaN(limit) && limit > 0) {
      this.sendCommand("LIMIT", limit);
    } else {
      this.showToast("Enter a valid speed limit in km/h", "warning");
    }
  }

  // ===================================================================
  // SYSTEM MODE: "waiting" for the first real reading, then "serial" (live ESP32 data)
  // ===================================================================
  updateModeUI(mode) {
    const serialConfigPanel = document.getElementById("serial-hardware-panel");
    const headerModeBadge = document.getElementById("header-mode-badge");
    const espBadgeEl = document.getElementById("badge-esp32-status");
    const espPortEl = document.getElementById("val-esp32-port");

    // The USB serial setup stays visible: it is needed before the first reading to connect a cable.
    if (serialConfigPanel) serialConfigPanel.style.display = "block";
    if (mode === "serial") {
      if (headerModeBadge) {
        headerModeBadge.innerText = "Live ESP32 data";
        headerModeBadge.className = "badge badge-success";
      }
    } else {
      if (headerModeBadge) {
        headerModeBadge.innerText = "Waiting for ESP32";
        headerModeBadge.className = "badge badge-warning";
      }
      if (espBadgeEl) {
        espBadgeEl.innerText = "Waiting";
        espBadgeEl.className = "badge badge-muted";
      }
      if (espPortEl) espPortEl.innerText = "No reading yet";
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
  // The filters typed in the filter bar, as a query string for the server
  eventQuery() {
    const params = new URLSearchParams();
    const type = document.getElementById("filter-event-type")?.value;
    if (type && type !== "ALL") params.set("type", type);
    const severity = document.getElementById("filter-severity")?.value;
    if (severity && severity !== "ALL") params.set("severity", severity);
    const start = document.getElementById("filter-start-date")?.value;
    const end = document.getElementById("filter-end-date")?.value;
    const search = document.getElementById("filter-search")?.value.trim();
    if (start) params.set("start", start);
    if (end) params.set("end", end);
    if (search) params.set("search", search);
    return params;
  }

  // The tab shows only the 5 latest events. "View all events" opens the whole history.
  loadEvents() {
    const params = this.eventQuery();
    params.set("limit", "5");
    fetch(`/api/events?${params}`)
      .then(res => {
        if (!res.ok) throw new Error("Offline");
        return res.json();
      })
      .then(data => {
        if (data.status === "success" && data.data) {
          this.renderEventsTable(data.data.events);
          const counterEl = document.getElementById("events-count-badge");
          if (counterEl) counterEl.innerText = `${data.data.total} Total`;
        }
      })
      .catch(() => {
        this.renderEventsTable([]);       // server offline: no events are shown, none are made up
        const counterEl = document.getElementById("events-count-badge");
        if (counterEl) counterEl.innerText = "Offline";
      });
  }

  eventBadgeClass(type) {
    const classes = {
      "ACCIDENT": "badge-danger", "ALCOHOL DETECTED": "badge-danger", "UNAUTHORISED DRIVER": "badge-danger",
      "OBSTACLE DETECTED": "badge-warning", "OVERSPEED": "badge-warning", "DANGER ZONE": "badge-warning",
      "ZONE EXIT": "badge-info", "ZONE_ENTER": "badge-warning", "ZONE_EXIT": "badge-info",
      "OVERSPEED_IN_ZONE": "badge-danger", "EMERGENCY STOP": "badge-danger"
    };
    return classes[type] || "badge-info";
  }

  severityBadgeClass(severity) {
    const level = String(severity || "INFO").toUpperCase();
    if (level === "CRITICAL" || level === "HIGH") return "badge-danger";
    if (level === "WARNING" || level === "MEDIUM") return "badge-warning";
    return "badge-info";
  }

  renderEventsTable(events) {
    const tbody = document.getElementById("events-table-body");
    if (!tbody) return;
    this.recentEvents = events || [];

    if (this.recentEvents.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" style="text-align:center; color:var(--text-muted); padding:24px;">
            No events match. Change the filter or wait for new events.
          </td>
        </tr>`;
      return;
    }

    tbody.innerHTML = this.recentEvents.map((ev, index) => `
      <tr>
        <td><span class="badge ${this.eventBadgeClass(ev.event_type)}">${escapeHtml(ev.event_type)}</span></td>
        <td><span class="badge ${this.severityBadgeClass(ev.severity)}">${escapeHtml(ev.severity || "INFO")}</span></td>
        <td style="color:var(--text-secondary); white-space:nowrap;">${escapeHtml(ev.timestamp)}</td>
        <td><b>${escapeHtml(ev.driver_name || "Unknown")}</b></td>
        <td>${escapeHtml(ev.vehicle_speed)} km/h</td>
        <td>${escapeHtml(ev.danger_zone_name || "None")}</td>
        <td><button type="button" class="btn btn-outline btn-sm" data-view-event="${index}">View</button></td>
      </tr>`).join("");
  }

  // ---- full-screen window: one event, or the whole history 20 at a time
  openEventModal(title, html) {
    document.getElementById("event-modal-title").textContent = title;
    document.getElementById("event-modal-body").innerHTML = html;
    document.getElementById("event-modal").classList.add("show");
  }

  closeEventModal() {
    document.getElementById("event-modal")?.classList.remove("show");
  }

  showEventDetails(ev) {
    this.openEventModal(`${ev.event_type} (#${ev.id})`, `
      <dl class="detail-grid">
        <dt>Event</dt><dd>${escapeHtml(ev.event_type)}</dd>
        <dt>Severity</dt><dd>${escapeHtml(ev.severity || "INFO")}</dd>
        <dt>Date and time</dt><dd>${escapeHtml(ev.timestamp)}</dd>
        <dt>Driver</dt><dd>${escapeHtml(ev.driver_name || "Unknown")}</dd>
        <dt>Speed</dt><dd>${escapeHtml(ev.vehicle_speed)} km/h</dd>
        <dt>GPS position</dt><dd>${escapeHtml(ev.latitude)}, ${escapeHtml(ev.longitude)}</dd>
        <dt>Danger zone</dt><dd>${escapeHtml(ev.danger_zone_name || "None")}</dd>
        <dt>Details</dt><dd>${escapeHtml(ev.details) || "-"}</dd>
      </dl>`);
  }

  async showAllEvents(page = 0) {
    const perPage = 20;
    const params = this.eventQuery();
    params.set("limit", String(perPage));
    params.set("offset", String(page * perPage));
    let data;
    try {
      const res = await fetch(`/api/events?${params}`);
      data = (await res.json()).data;
    } catch (_) {
      this.openEventModal("All events", "<p>The events could not be loaded.</p>");
      return;
    }
    const pages = Math.max(1, Math.ceil(data.total / perPage));
    const rows = data.events.map(ev => `
      <tr>
        <td><code>#${escapeHtml(ev.id)}</code></td>
        <td><span class="badge ${this.eventBadgeClass(ev.event_type)}">${escapeHtml(ev.event_type)}</span></td>
        <td><span class="badge ${this.severityBadgeClass(ev.severity)}">${escapeHtml(ev.severity || "INFO")}</span></td>
        <td style="white-space:nowrap;">${escapeHtml(ev.timestamp)}</td>
        <td>${escapeHtml(ev.driver_name || "Unknown")}</td>
        <td style="white-space:nowrap;">${escapeHtml(ev.vehicle_speed)} km/h</td>
        <td style="white-space:nowrap;">${escapeHtml(ev.latitude)}, ${escapeHtml(ev.longitude)}</td>
        <td>${escapeHtml(ev.danger_zone_name || "None")}</td>
        <td>${escapeHtml(ev.details)}</td>
      </tr>`).join("");
    this.openEventModal(`All events (${data.total})`, `
      <div class="table-responsive">
        <table class="custom-table">
          <thead><tr><th>ID</th><th>Event</th><th>Severity</th><th>Date and time</th><th>Driver</th><th>Speed</th><th>GPS position</th><th>Danger zone</th><th>Details</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="9" style="text-align:center;">No events match the filter.</td></tr>'}</tbody>
        </table>
      </div>
      <div style="display:flex; justify-content:space-between; align-items:center; margin-top:12px;">
        <button type="button" class="btn btn-outline btn-sm" data-events-page="${page - 1}" ${page === 0 ? "disabled" : ""}>Previous</button>
        <span style="color:var(--text-secondary);">Page ${page + 1} of ${pages}</span>
        <button type="button" class="btn btn-outline btn-sm" data-events-page="${page + 1}" ${page + 1 >= pages ? "disabled" : ""}>Next</button>
      </div>`);
  }

  exportEventsCSV() {
    if (this.backendConnected) {
      window.location.href = `/api/events/export?${this.eventQuery()}`;
      this.showToast("Downloading events CSV export...", "info");
      return;
    }

    this.showToast("The server cannot be reached, so there is nothing to export.", "warning");
  }

  // ===================================================================
  // UI INTERACTIONS & EVENT BINDINGS
  // ===================================================================
  // ===================================================================
  // SMS ON / OFF BUTTON (admin panel, Controls tab)
  // ===================================================================
  async loadSmsSwitch() {
    try {
      const r = await fetch("/api/sms/status", { cache: "no-store" });
      this.showSmsSwitch(await r.json());
    } catch (e) {
      const note = document.getElementById("sms-switch-note");
      if (note) note.innerText = "Cannot read the SMS status.";
    }
  }

  showSmsSwitch(s) {
    const btn = document.getElementById("btn-sms-switch");
    const note = document.getElementById("sms-switch-note");
    if (!btn || !s) return;
    const on = !!s.enabled;
    // state badge (green ON / red OFF) and one button that does the opposite
    const state = document.getElementById("sms-state");
    if (state) {
      state.classList.toggle("off", !on);
      state.querySelector("b").innerText = on ? "SMS sending is ON" : "SMS sending is OFF";
    }
    btn.innerText = on ? "Turn SMS off" : "Turn SMS on";
    btn.classList.toggle("btn-teal", !on);           // green button to turn it on
    btn.classList.toggle("btn-outline", on);         // red outline button to turn it off
    btn.classList.toggle("sms-turn-off", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    if (!note) return;
    if (!on) note.innerText = "No text message is sent: not for alerts, not for SOS.";
    else if (!s.configured) note.innerText = "On, but SMS is not set up yet (" + (s.problems || []).join("; ") + ").";
    else note.innerText = "Alerts and SOS are texted to " + (s.recipients || []).join(", ") + ".";
  }

  initSmsSwitch() {
    const btn = document.getElementById("btn-sms-switch");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      const turnOn = btn.getAttribute("aria-pressed") !== "true";
      btn.disabled = true;
      try {
        const r = await fetch("/api/sms/enabled", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: turnOn }),
        });
        const d = await r.json().catch(() => ({}));
        if (r.ok && d.status === "success") {
          this.showSmsSwitch(d);
          this.showToast(turnOn ? "SMS sending is ON." : "SMS sending is OFF.", turnOn ? "success" : "warning");
        } else {
          await AppDialog.alert({ title: "Could not change SMS", message: d.message || (r.status === 401 ? "Log in to the admin panel first." : "The server refused the change.") });
        }
      } catch (e) {
        await AppDialog.alert({ title: "Could not change SMS", message: "Cannot reach the AcciAlert server." });
      }
      btn.disabled = false;
    });
    this.loadSmsSwitch();
  }

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

        if (window.MapManager && window.MapManager.view) {
          setTimeout(() => window.MapManager.invalidate(), 150);
        }
      });
    });

    // Control Panel Action Buttons
    document.getElementById("btn-sos")?.addEventListener("click", () => this.sendSos());
    document.getElementById("btn-reset-system")?.addEventListener("click", () => this.resetSystem());
    document.getElementById("btn-ack-alert")?.addEventListener("click", () => {
      if (window.AlertManager) window.AlertManager.acknowledgeAlert();
    });
    document.getElementById("btn-banner-ack")?.addEventListener("click", () => {
      if (window.AlertManager) window.AlertManager.acknowledgeAlert();
    });

    // Speed Limit Button
    document.getElementById("btn-apply-limit")?.addEventListener("click", () => this.applySpeedLimit());


    // Event Filters
    document.getElementById("btn-filter-apply")?.addEventListener("click", () => this.loadEvents());
    document.getElementById("btn-filter-reset")?.addEventListener("click", () => {
      for (const id of ["filter-event-type", "filter-severity"]) {
        const select = document.getElementById(id);
        if (select) select.value = "ALL";
      }
      for (const id of ["filter-start-date", "filter-end-date", "filter-search"]) {
        const input = document.getElementById(id);
        if (input) input.value = "";
      }
      this.loadEvents();
    });
    document.getElementById("btn-export-csv")?.addEventListener("click", () => this.exportEventsCSV());
    document.getElementById("btn-events-refresh")?.addEventListener("click", () => this.loadEvents());
    document.getElementById("btn-view-all-events")?.addEventListener("click", () => this.showAllEvents(0));
    document.getElementById("btn-event-modal-close")?.addEventListener("click", () => this.closeEventModal());
    document.getElementById("events-table-body")?.addEventListener("click", (e) => {
      const view = e.target.closest("[data-view-event]");
      if (view) this.showEventDetails(this.recentEvents[Number(view.dataset.viewEvent)]);
    });
    document.getElementById("event-modal")?.addEventListener("click", (e) => {
      if (e.target.id === "event-modal") this.closeEventModal();            // click outside the window
      const pager = e.target.closest("[data-events-page]");
      if (pager && !pager.disabled) this.showAllEvents(Number(pager.dataset.eventsPage));
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") this.closeEventModal(); });

    // Audio Mute Toggle
    document.getElementById("btn-mute-toggle")?.addEventListener("click", () => {
      if (window.AlertManager) window.AlertManager.toggleMute();
    });

    // Map Utility Buttons
    document.getElementById("btn-map-me")?.addEventListener("click", () => {
      if (window.MapManager) window.MapManager.centerOnLive();
    });
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

  // Shows a red warning in the header when no reading has arrived from the vehicle for 5 seconds.
  checkDataAge() {
    if (!this.backendConnected || this.lastTelemetryAt === null) return;
    const age = (Date.now() - this.lastTelemetryAt) / 1000;
    if (age <= 5) return;
    if (!this.dataStale) {
      this.dataStale = true;
      this.showToast("No data from the vehicle. Check the ESP32 and its connection.", "warning");
    }
    const dot = document.getElementById("header-pulse-dot");
    const text = document.getElementById("header-status-text");
    if (dot && text) {
      dot.className = "pulse-dot warning";
      text.innerText = `No data from the vehicle for ${Math.round(age)} s`;
    }
  }

  updateConnectionBadge(connected, labelText) {
    const dot = document.getElementById("header-pulse-dot");
    const text = document.getElementById("header-status-text");
    if (dot && text) {
      if (connected) {
        dot.className = "pulse-dot";
        text.innerText = labelText || "System active";
      } else {
        dot.className = "pulse-dot danger";
        text.innerText = labelText || "Disconnected";
      }
    }
  }

  showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;

    toast.innerHTML = `
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
