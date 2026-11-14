/**
 * AcciAlert - Danger Zone Management Module
 * Add, Edit, Delete, and Map Interactivity for Safety Zones.
 * Supports both Flask REST API and Frontend-Only LocalStorage mode.
 * Author: Muniyappan
 */

class ZoneManager {
  constructor() {
    this.zonesList = [];
    this.modalEl = document.getElementById("zone-modal");
    this.formEl = document.getElementById("zone-form");
    this.tableBodyEl = document.getElementById("zones-table-body");

    this.bindEvents();
    this.loadZones();
  }

  // ---- notification distance: the car (or you) gets this close to a danger zone and a notification shows ----
  bindAlertDistance() {
    const select = document.getElementById("zone-alert-select");
    const custom = document.getElementById("zone-alert-custom");
    const save = document.getElementById("btn-save-zone-alert");
    const msg = document.getElementById("zone-alert-msg");
    if (!select || !save) return;
    const say = (text, kind) => { msg.textContent = text; msg.className = kind || ""; };
    const show = (metres) => {                                  // put the saved distance into the controls
      const preset = [...select.options].find(o => o.value === String(metres));
      select.value = preset ? String(metres) : "custom";
      custom.style.display = preset ? "none" : "";
      if (!preset) custom.value = metres;
    };
    select.addEventListener("change", () => { custom.style.display = select.value === "custom" ? "" : "none"; say(""); });
    fetch("/api/zone-settings").then(r => r.json()).then(d => { if (d && d.alert_distance_m) show(d.alert_distance_m); }).catch(() => {});
    save.addEventListener("click", async () => {
      const metres = select.value === "custom" ? Number(custom.value) : Number(select.value);
      if (!Number.isFinite(metres) || metres < 50 || metres > 5000) { say("Enter a distance from 50 to 5000 metres.", "err"); return; }
      try {
        const res = await fetch("/api/zone-settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ alert_distance_m: metres }) });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.message || `Not saved (server answered ${res.status}).`);
        say(`Saved: notification at ${metres >= 1000 ? (metres / 1000) + " km" : metres + " m"}.`, "ok");
        if (window.MapManager) window.MapManager.setAlertDistance(body.alert_distance_m);
        if (window.DashboardApp) window.DashboardApp.showToast(`Danger zone notification distance: ${metres} m`, "success");
      } catch (err) { say(err.message, "err"); }
    });
  }

  bindEvents() {
    this.bindAlertDistance();
    if (this.formEl) {
      this.formEl.addEventListener("submit", (e) => {
        e.preventDefault();
        this.saveZone();
      });
    }

    const btnUseVehicleGps = document.getElementById("btn-zone-use-gps");
    if (btnUseVehicleGps) {
      btnUseVehicleGps.addEventListener("click", () => {
        if (window.DashboardApp && window.DashboardApp.latestTelemetry) {
          const lat = window.DashboardApp.latestTelemetry.lat;
          const lon = window.DashboardApp.latestTelemetry.lon;
          document.getElementById("zone-lat").value = lat.toFixed(6);
          document.getElementById("zone-lon").value = lon.toFixed(6);
          window.DashboardApp.showToast("Current vehicle coordinates applied.", "info");
        } else {
          window.DashboardApp.showToast("No vehicle GPS telemetry available.", "warning");
        }
      });
    }

    const btnPickOnMap = document.getElementById("btn-zone-pick-map");
    if (btnPickOnMap) {
      btnPickOnMap.addEventListener("click", () => {
        this.closeModal();
        if (window.MapManager) {
          window.MapManager.enablePickMode((lat, lon) => {
            this.openModal();
            document.getElementById("zone-lat").value = lat.toFixed(6);
            document.getElementById("zone-lon").value = lon.toFixed(6);
            window.DashboardApp.showToast(`Selected coordinates: ${lat.toFixed(5)}, ${lon.toFixed(5)}`, "success");
          });
        }
      });
    }
  }

  loadZones() {
    fetch("/api/zones")
      .then(res => {
        if (!res.ok) throw new Error("Backend offline");
        return res.json();
      })
      .then(data => {
        if (data.status === "success" && Array.isArray(data.zones)) {
          this.zonesList = data.zones;
          localStorage.setItem("accialert_zones", JSON.stringify(data.zones));
          this.renderTable(data.zones);
          if (window.MapManager) {
            window.MapManager.renderDangerZones(data.zones);
          }
        } else {
          this.loadLocalZones();
        }
      })
      .catch(() => {
        // Fallback to local storage (frontend-only mode)
        this.loadLocalZones();
      });
  }

  loadLocalZones() {
    const saved = localStorage.getItem("accialert_zones");
    if (saved) {
      try {
        this.zonesList = JSON.parse(saved);
      } catch (e) {
        this.zonesList = DEFAULT_CLIENT_ZONES;
      }
    } else {
      this.zonesList = DEFAULT_CLIENT_ZONES;
      localStorage.setItem("accialert_zones", JSON.stringify(DEFAULT_CLIENT_ZONES));
    }
    this.renderTable(this.zonesList);
    if (window.MapManager) {
      window.MapManager.renderDangerZones(this.zonesList);
    }
  }

  renderTable(zones) {
    if (!this.tableBodyEl) return;

    if (!zones || zones.length === 0) {
      this.tableBodyEl.innerHTML = `
        <tr>
          <td colspan="7" style="text-align:center; color:#6B7280; padding:24px;">
            No danger zones configured. Click "+ Add Danger Zone" to create one.
          </td>
        </tr>
      `;
      return;
    }

    let rowsHtml = "";
    zones.forEach(z => {
      const sev = (z.severity || "MEDIUM").toUpperCase();
      let badgeClass = "badge-info";
      if (sev === "CRITICAL") badgeClass = "badge-danger";
      else if (sev === "HIGH") badgeClass = "badge-warning";
      else if (sev === "MEDIUM") badgeClass = "badge-warning";

      rowsHtml += `
        <tr>
          <td><strong style="color:#16A34A;">${escapeHtml(z.code)}</strong></td>
          <td><b>${escapeHtml(z.name)}</b><br/><small style="color:#6B7280;">${escapeHtml(z.details)}</small></td>
          <td><code style="color:#4B5563;">${parseFloat(z.latitude).toFixed(4)}, ${parseFloat(z.longitude).toFixed(4)}</code></td>
          <td>${z.radius_meters} m</td>
          <td><span style="color:#15803D; font-weight:700;">${z.speed_limit} km/h</span></td>
          <td><span class="badge ${badgeClass}">${sev}</span></td>
          <td>
            <div style="display:flex; gap:6px;">
              <button class="btn btn-outline btn-sm" title="Locate on Map" onclick="window.ZoneApp.locateZone(${z.id})">Locate</button>
              <button class="btn btn-outline btn-sm" title="Edit Zone" onclick="window.ZoneApp.editZone(${z.id})">Edit</button>
              <button class="btn btn-outline btn-sm" style="color:#DC2626;" title="Delete Zone" onclick="window.ZoneApp.deleteZone(${z.id})">Delete</button>
            </div>
          </td>
        </tr>
      `;
    });

    this.tableBodyEl.innerHTML = rowsHtml;
  }

  openModal(zone = null) {
    if (!this.modalEl) return;
    this.formEl.reset();

    const titleEl = document.getElementById("zone-modal-title");
    if (zone) {
      if (titleEl) titleEl.innerText = `Edit Danger Zone: ${zone.code}`;
      document.getElementById("zone-id").value = zone.id;
      document.getElementById("zone-code").value = zone.code;
      document.getElementById("zone-name").value = zone.name;
      document.getElementById("zone-lat").value = zone.latitude;
      document.getElementById("zone-lon").value = zone.longitude;
      document.getElementById("zone-radius").value = zone.radius_meters;
      document.getElementById("zone-speed-limit").value = zone.speed_limit;
      document.getElementById("zone-severity").value = zone.severity;
      document.getElementById("zone-details").value = zone.details || "";
    } else {
      if (titleEl) titleEl.innerText = "Add danger zone";
      document.getElementById("zone-id").value = "";
      const nextNum = this.zonesList.length + 1;
      document.getElementById("zone-code").value = `Z${nextNum < 10 ? '0' + nextNum : nextNum}`;
      document.getElementById("zone-radius").value = 150;
      document.getElementById("zone-speed-limit").value = 30;
      document.getElementById("zone-severity").value = "HIGH";
    }

    this.modalEl.classList.add("show");
  }

  closeModal() {
    if (this.modalEl) {
      this.modalEl.classList.remove("show");
    }
  }

  editZone(zoneId) {
    const zone = this.zonesList.find(z => z.id === zoneId);
    if (zone) {
      this.openModal(zone);
    }
  }

  locateZone(zoneId) {
    const zone = this.zonesList.find(z => z.id === zoneId);
    if (zone && window.MapManager && window.MapManager.view) {
      const lat = parseFloat(zone.latitude);
      const lon = parseFloat(zone.longitude);
      window.MapManager.goTo(lat, lon, 16);
      window.DashboardApp.showToast(`Centered on danger zone: ${zone.name}`, "info");
    }
  }

  saveZone() {
    const zoneId = document.getElementById("zone-id").value;
    const payload = {
      id: zoneId ? parseInt(zoneId) : Date.now(),
      code: document.getElementById("zone-code").value.trim().toUpperCase(),
      name: document.getElementById("zone-name").value.trim(),
      latitude: parseFloat(document.getElementById("zone-lat").value),
      longitude: parseFloat(document.getElementById("zone-lon").value),
      radius_meters: parseFloat(document.getElementById("zone-radius").value),
      speed_limit: parseFloat(document.getElementById("zone-speed-limit").value),
      severity: document.getElementById("zone-severity").value,
      details: document.getElementById("zone-details").value.trim()
    };

    if (!payload.code || !payload.name || isNaN(payload.latitude) || isNaN(payload.longitude)) {
      window.DashboardApp.showToast("Please fill in all required fields properly.", "warning");
      return;
    }

    // The server decides. If it refuses, say why and keep the form open: nothing was saved.
    fetch("/api/zones", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then(res => res.json().catch(() => null).then(data => ({ res, data })))
      .then(({ res, data }) => {
        if (res.ok && data && data.status === "success") {
          this.closeModal();
          this.loadZones();
          window.DashboardApp.showToast(data.message || "Danger zone saved.", "success");
        } else {
          const why = (data && data.message) ? data.message : `The server could not save the zone (error ${res.status}).`;
          window.DashboardApp.showToast(why, "warning");
        }
      })
      .catch(() => {
        // No answer from the server at all (standalone mode): keep the zone in this browser only.
        this.saveLocalZone(payload);
      });
  }

  saveLocalZone(payload) {
    const existingIdx = this.zonesList.findIndex(z => z.id === payload.id || z.code === payload.code);
    if (existingIdx >= 0) {
      this.zonesList[existingIdx] = payload;
    } else {
      this.zonesList.push(payload);
    }
    localStorage.setItem("accialert_zones", JSON.stringify(this.zonesList));
    this.closeModal();
    this.renderTable(this.zonesList);
    if (window.MapManager) {
      window.MapManager.renderDangerZones(this.zonesList);
    }
    window.DashboardApp.showToast("Server not reachable: zone saved in this browser only.", "warning");
  }

  async deleteZone(zoneId) {
    const zone = this.zonesList.find(z => z.id === zoneId);
    const zoneName = zone ? zone.name : `#${zoneId}`;

    if (!await AppDialog.confirm({ title: "Delete danger zone?", message: `"${zoneName}" will be removed from the map and from the ESP32.`, confirmText: "Delete", danger: true })) {
      return;
    }

    fetch(`/api/zones/${zoneId}`, { method: "DELETE" })
      .then(res => res.json().catch(() => null).then(data => ({ res, data })))
      .then(({ res, data }) => {
        if (res.ok && data && data.status === "success") {
          this.loadZones();
          window.DashboardApp.showToast(`Zone "${zoneName}" deleted.`, "info");
        } else if (res.status === 404) {
          this.loadZones();
          window.DashboardApp.showToast(`Zone "${zoneName}" was already deleted.`, "info");
        } else {
          const why = (data && data.message) ? data.message : `The server could not delete the zone (error ${res.status}).`;
          window.DashboardApp.showToast(why, "warning");
        }
      })
      .catch(() => {
        this.deleteLocalZone(zoneId, zoneName);
      });
  }

  deleteLocalZone(zoneId, zoneName) {
    this.zonesList = this.zonesList.filter(z => z.id !== zoneId);
    localStorage.setItem("accialert_zones", JSON.stringify(this.zonesList));
    this.renderTable(this.zonesList);
    if (window.MapManager) {
      window.MapManager.renderDangerZones(this.zonesList);
    }
    window.DashboardApp.showToast(`Server not reachable: "${zoneName}" removed in this browser only.`, "warning");
  }
}

window.ZoneApp = null;
