/**
 * AcciAlert - Leaflet.js GPS Mapping Module
 * Displays vehicle position, breadcrumb trail, and severity-colored danger zones.
 * Author: Muniyappan
 */

class VehicleMapManager {
  constructor() {
    this.map = null;
    this.vehicleMarker = null;
    this.trailPolyline = null;
    this.trailCoordinates = [];
    this.maxTrailPoints = 80;

    this.zoneCirclesLayer = null;
    this.zonesData = [];

    this.followVehicle = true;
    this.pickModeActive = false;
    this.onMapClickCallback = null;

    this.defaultLat = 13.0827;
    this.defaultLon = 80.2707;

    this.initMap();
  }

  initMap() {
    const mapElement = document.getElementById("leaflet-map");
    if (!mapElement) return;

    // Create Leaflet Map
    this.map = L.map("leaflet-map", {
      center: [this.defaultLat, this.defaultLon],
      zoom: 14,
      zoomControl: false
    });

    // Add Zoom Control to top-left
    L.control.zoom({ position: "topleft" }).addTo(this.map);

    // Dark Matter Tiles (CartoDB)
    const cartoDark = L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
      {
        attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; OpenStreetMap',
        subdomains: "abcd",
        maxZoom: 19
      }
    );

    // Fallback standard OSM tile layer in case Carto is unavailable
    const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxZoom: 19
    });

    // CARTO dark tiles now return an "API KEY REQUIRED" watermark (HTTP 200, so the
    // tileerror fallback never fires). OpenStreetMap is used as the base map instead.
    osm.addTo(this.map);

    // Trail Polyline
    this.trailPolyline = L.polyline([], {
      color: "#A16207",
      weight: 3,
      opacity: 0.75,
      dashArray: "6, 6"
    }).addTo(this.map);

    // Zone Layer Group
    this.zoneCirclesLayer = L.layerGroup().addTo(this.map);

    // Vehicle Marker with Custom Pulsing SVG Icon
    const carIconHtml = `
      <div class="custom-car-marker" style="
        position: relative;
        width: 38px;
        height: 38px;
        display: flex;
        align-items: center;
        justify-content: center;
      ">
        <div style="
          position: absolute;
          width: 36px;
          height: 36px;
          border-radius: 50%;
          background: rgba(161, 98, 7, 0.20);
          border: 2px solid #A16207;
          
          animation: pulse-ring 1.8s infinite;
        "></div>
        <div style="
          width: 24px;
          height: 24px;
          border-radius: 50%;
          background: #FFFFFF;
          display: flex;
          align-items: center;
          justify-content: center;
          border: 1.5px solid #A16207;
          z-index: 2;
        ">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="#A16207">
            <path d="M18.92 6.01C18.72 5.42 18.16 5 17.5 5h-11c-.66 0-1.21.42-1.42 1.01L3 12v8c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h12v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-8l-2.08-5.99zM6.5 16c-.83 0-1.5-.67-1.5-1.5S5.67 13 6.5 13s1.5.67 1.5 1.5S7.33 16 6.5 16zm11 0c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zM5 11l1.5-4.5h11L19 11H5z"/>
          </svg>
        </div>
      </div>
    `;

    const carIcon = L.divIcon({
      html: carIconHtml,
      className: "vehicle-custom-icon",
      iconSize: [38, 38],
      iconAnchor: [19, 19],
      popupAnchor: [0, -19]
    });

    this.vehicleMarker = L.marker([this.defaultLat, this.defaultLon], { icon: carIcon }).addTo(this.map);
    this.vehicleMarker.bindPopup(`
      <div style="font-size:0.85rem; line-height:1.4;">
        <strong style="color:#A16207;">AcciAlert Vehicle</strong><br/>
        Driver: <span id="map-popup-driver">Muniyappan</span><br/>
        Speed: <span id="map-popup-speed">0.0 km/h</span><br/>
        Status: <span id="map-popup-status">INITIALIZING</span>
      </div>
    `);

    // Map Click Listener (for picking danger zone coords)
    this.map.on("click", (e) => {
      if (this.pickModeActive && this.onMapClickCallback) {
        this.onMapClickCallback(e.latlng.lat, e.latlng.lng);
        this.disablePickMode();
      }
    });

    // Load initial danger zones
    this.loadDangerZones();
  }

  updateVehiclePosition(lat, lon, speed, state, driverName) {
    if (!this.map || !this.vehicleMarker) return;
    if (isNaN(lat) || isNaN(lon) || (lat === 0 && lon === 0)) return;

    const newPos = [lat, lon];
    this.vehicleMarker.setLatLng(newPos);

    // Update Trail
    this.trailCoordinates.push(newPos);
    if (this.trailCoordinates.length > this.maxTrailPoints) {
      this.trailCoordinates.shift();
    }
    this.trailPolyline.setLatLngs(this.trailCoordinates);

    // Update Popup content
    const popupContent = `
      <div style="font-size:0.82rem; line-height:1.4;">
        <strong style="color:#A16207; font-size:0.9rem;">AcciAlert Vehicle</strong><br/>
        <b>Driver:</b> ${driverName || "Muniyappan"}<br/>
        <b>Speed:</b> ${speed ? speed.toFixed(1) : 0.0} km/h<br/>
        <b>State:</b> <span class="badge ${state === 'RUNNING' ? 'badge-success' : 'badge-danger'}">${state || 'READY'}</span><br/>
        <b>GPS:</b> ${lat.toFixed(5)}, ${lon.toFixed(5)}
      </div>
    `;
    this.vehicleMarker.getPopup().setContent(popupContent);

    // Auto-pan if follow is enabled
    if (this.followVehicle) {
      this.map.panTo(newPos, { animate: true, duration: 0.5 });
    }

    // Update overlay readout
    const coordEl = document.getElementById("map-coords-readout");
    if (coordEl) {
      coordEl.innerText = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    }
  }

  centerOnVehicle() {
    if (this.vehicleMarker && this.map) {
      this.map.setView(this.vehicleMarker.getLatLng(), 15, { animate: true });
    }
  }

  toggleFollow() {
    this.followVehicle = !this.followVehicle;
    const btn = document.getElementById("btn-map-follow");
    if (btn) {
      btn.classList.toggle("btn-teal", this.followVehicle);
      btn.classList.toggle("btn-outline", !this.followVehicle);
      btn.innerHTML = this.followVehicle
        ? 'Following'
        : 'Free view';
    }
    if (this.followVehicle) {
      this.centerOnVehicle();
    }
  }

  clearTrail() {
    this.trailCoordinates = [];
    if (this.trailPolyline) {
      this.trailPolyline.setLatLngs([]);
    }
    if (window.DashboardApp) {
      window.DashboardApp.showToast("Vehicle path trail cleared.", "info");
    }
  }

  enablePickMode(callback) {
    this.pickModeActive = true;
    this.onMapClickCallback = callback;
    document.getElementById("leaflet-map").style.cursor = "crosshair";
    if (window.DashboardApp) {
      window.DashboardApp.showToast("Click anywhere on the map to set zone coordinates.", "warning");
    }
  }

  disablePickMode() {
    this.pickModeActive = false;
    document.getElementById("leaflet-map").style.cursor = "";
  }

  loadDangerZones() {
    fetch("/api/zones")
      .then(res => res.json())
      .then(data => {
        if (data.status === "success" && Array.isArray(data.zones)) {
          this.zonesData = data.zones;
          this.renderDangerZones(data.zones);
        }
      })
      .catch(err => console.error("Error loading danger zones:", err));
  }

  renderDangerZones(zones) {
    if (!this.zoneCirclesLayer) return;
    this.zoneCirclesLayer.clearLayers();

    const severityColors = {
      "LOW": { color: "#22C55E", fill: "rgba(34, 197, 94, 0.22)" },
      "MEDIUM": { color: "#CA8A04", fill: "rgba(202, 138, 4, 0.25)" },
      "HIGH": { color: "#EA580C", fill: "rgba(249, 115, 22, 0.30)" },
      "CRITICAL": { color: "#DC2626", fill: "rgba(225, 29, 72, 0.38)" }
    };

    zones.forEach(zone => {
      const sev = (zone.severity || "MEDIUM").toUpperCase();
      const style = severityColors[sev] || severityColors["MEDIUM"];

      const circle = L.circle([parseFloat(zone.latitude), parseFloat(zone.longitude)], {
        radius: parseFloat(zone.radius_meters),
        color: style.color,
        weight: 2,
        fillColor: style.color,
        fillOpacity: sev === "CRITICAL" ? 0.35 : 0.22,
        dashArray: sev === "CRITICAL" ? "4, 4" : null
      });

      const popupHtml = `
        <div style="font-size:0.82rem; line-height:1.45;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
            <strong style="color:${style.color}; font-size:0.9rem;">${zone.name}</strong>
            <span class="badge ${sev === 'CRITICAL' ? 'badge-danger' : sev === 'HIGH' ? 'badge-warning' : 'badge-info'}" style="font-size:0.65rem;">${sev}</span>
          </div>
          <b>Zone Code:</b> ${zone.code}<br/>
          <b>Speed Limit:</b> <span style="color:#A16207; font-weight:700;">${zone.speed_limit} km/h</span><br/>
          <b>Radius:</b> ${zone.radius_meters} m<br/>
          <div style="margin-top:6px; color:#4B5563; font-size:0.75rem;">${zone.details || ''}</div>
          <button onclick="window.DashboardApp.applySpeedLimit(${zone.speed_limit})" class="btn btn-teal btn-sm" style="width:100%; margin-top:8px; padding:4px 8px; font-size:0.75rem;">
            Enforce Limit (${zone.speed_limit} km/h)
          </button>
        </div>
      `;

      circle.bindPopup(popupHtml);
      this.zoneCirclesLayer.addLayer(circle);
    });
  }
}

// Global instance
window.MapManager = null;

