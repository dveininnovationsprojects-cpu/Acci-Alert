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

  bindEvents() {
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
          <td><strong style="color:#A16207;">${z.code}</strong></td>
          <td><b>${z.name}</b><br/><small style="color:#6B7280;">${z.details || ''}</small></td>
          <td><code style="color:#4B5563;">${parseFloat(z.latitude).toFixed(4)}, ${parseFloat(z.longitude).toFixed(4)}</code></td>
          <td>${z.radius_meters} m</td>
          <td><span style="color:#EA580C; font-weight:700;">${z.speed_limit} km/h</span></td>
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
      if (titleEl) titleEl.innerText = "Add New Danger Zone";
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
    if (zone && window.MapManager && window.MapManager.map) {
      const lat = parseFloat(zone.latitude);
      const lon = parseFloat(zone.longitude);
      window.MapManager.map.setView([lat, lon], 16, { animate: true });
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

    // Attempt backend save
    fetch("/api/zones", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then(res => {
        if (!res.ok) throw new Error("Backend offline");
        return res.json();
      })
      .then(data => {
        if (data.status === "success") {
          this.closeModal();
          this.loadZones();
          window.DashboardApp.showToast(data.message || "Danger zone saved.", "success");
        } else {
          this.saveLocalZone(payload);
        }
      })
      .catch(() => {
        // Save locally
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
    window.DashboardApp.showToast("Danger zone saved (Local Mode).", "success");
  }

  deleteZone(zoneId) {
    const zone = this.zonesList.find(z => z.id === zoneId);
    const zoneName = zone ? zone.name : `#${zoneId}`;

    if (!confirm(`Are you sure you want to delete danger zone "${zoneName}"?`)) {
      return;
    }

    fetch(`/api/zones/${zoneId}`, { method: "DELETE" })
      .then(res => {
        if (!res.ok) throw new Error("Backend offline");
        return res.json();
      })
      .then(data => {
        if (data.status === "success") {
          this.loadZones();
          window.DashboardApp.showToast(`Zone "${zoneName}" deleted.`, "info");
        } else {
          this.deleteLocalZone(zoneId, zoneName);
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
    window.DashboardApp.showToast(`Zone "${zoneName}" deleted (Local Mode).`, "info");
  }
}

window.ZoneApp = null;
