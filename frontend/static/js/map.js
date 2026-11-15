/**
 * AcciAlert - map module. ONE map on the page: Google Maps (with the danger zones, the car and the live location drawn
 * on it) when a Google Maps key is set in the admin panel, otherwise a Google-style street map (Leaflet + Esri).
 * The manager below keeps all the logic (which zones to show, the notification distance, the live location, the trail).
 * A "view" only draws. Both views offer the same methods.
 * Author: Muniyappan
 */

// Zone names and notes are typed by users, so they are escaped before they go into the popups
// (zones.js uses this function too)
function escapeHtml(text) {
  return String(text == null ? "" : text).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

const SEVERITY_COLOURS = { LOW: "#16A34A", MEDIUM: "#CA8A04", HIGH: "#EA580C", CRITICAL: "#DC2626" };

// Distance in metres between two {lat, lng} points
function distanceM(a, b) {
  const R = 6371000, rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad, dLon = (b.lng - a.lng) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

// A pin like the ones on Google Maps (SVG text, used by both views)
function zonePinSvg(colour) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 38" width="28" height="38">` +
    `<path d="M14 1C7 1 1.5 6.4 1.5 13.2 1.5 22 14 37 14 37S26.5 22 26.5 13.2C26.5 6.4 21 1 14 1z" fill="${colour}" stroke="#fff" stroke-width="2"/>` +
    `<path d="M14 7.5l6 10.5H8z" fill="#fff"/><rect x="13.2" y="11.5" width="1.6" height="3.6" fill="${colour}"/><circle cx="14" cy="16.3" r="1" fill="${colour}"/></svg>`;
}

// The blue location dot (SVG text, for the Google view; the Leaflet view uses the animated CSS dot)
function liveDotSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 26 26" width="26" height="26"><circle cx="13" cy="13" r="12" fill="#2563EB" fill-opacity=".25"/>` +
    `<circle cx="13" cy="13" r="8.5" fill="#2563EB" stroke="#fff" stroke-width="3"/></svg>`;
}

// The car: a top-down car in a round badge, in Google's blue (like the car on Google Maps when you drive).
// The blue dot is only for the live location of this device.
function carIconSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 44 44" width="44" height="44">` +
    `<circle cx="22" cy="22" r="19" fill="#fff" stroke="#1A73E8" stroke-width="3"/>` +
    `<rect x="12.6" y="12.5" width="3" height="6" rx="1.2" fill="#174EA6"/><rect x="28.4" y="12.5" width="3" height="6" rx="1.2" fill="#174EA6"/>` +
    `<rect x="12.6" y="25.5" width="3" height="6" rx="1.2" fill="#174EA6"/><rect x="28.4" y="25.5" width="3" height="6" rx="1.2" fill="#174EA6"/>` +
    `<path d="M22 8.5c-3.6 0-6 2.2-6 5.6v15.8c0 3.4 2.4 5.6 6 5.6s6-2.2 6-5.6V14.1c0-3.4-2.4-5.6-6-5.6z" fill="#1A73E8"/>` +
    `<path d="M17.6 15.6h8.8l1.1 4.6h-11z" fill="#fff" fill-opacity=".92"/><path d="M17.8 27.4h8.4l.6 3.2h-9.6z" fill="#fff" fill-opacity=".85"/>` +
    `<path d="M22 9.6c-1.5 0-2.6.5-3.3 1.4.9-.3 2-.5 3.3-.5s2.4.2 3.3.5c-.7-.9-1.8-1.4-3.3-1.4z" fill="#fff" fill-opacity=".5"/></svg>`;
}

// =====================================================================================================================
// View 1: Leaflet with a Google-style street map (Esri) and a satellite switch. Works without any key.
// =====================================================================================================================
class LeafletView {
  constructor(containerId, center, zoom) {
    this.kind = "leaflet";
    this.map = L.map(containerId, { center: [center.lat, center.lng], zoom: zoom, zoomControl: false });
    L.control.zoom({ position: "topleft" }).addTo(this.map);

    const streets = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", {
      attribution: "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, OpenStreetMap contributors", maxZoom: 19
    });
    const satellite = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
      attribution: "Tiles &copy; Esri", maxZoom: 19
    });
    const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "&copy; OpenStreetMap contributors", maxZoom: 19 });
    let tileErrors = 0;
    streets.on("tileerror", () => {
      tileErrors += 1;
      if (tileErrors === 4 && this.map.hasLayer(streets)) { this.map.removeLayer(streets); osm.addTo(this.map); }
    });
    streets.addTo(this.map);
    L.control.layers({ "Map": streets, "Satellite": satellite }, null, { position: "topright", collapsed: false }).addTo(this.map);

    this.trail = L.polyline([], { color: "#16A34A", weight: 3, opacity: 0.75, dashArray: "6, 6" }).addTo(this.map);
    this.zoneLayer = L.layerGroup().addTo(this.map);
    this.vehicle = null;
    this.live = null;
    this.liveCircle = null;
    this.carIcon = L.divIcon({
      html: `<div class="vehicle-car">${carIconSvg()}</div>`,
      className: "vehicle-car-icon", iconSize: [44, 44], iconAnchor: [22, 22], popupAnchor: [0, -22]
    });
    this.dotIcon = L.divIcon({
      html: '<div class="live-dot"><span class="live-pulse"></span><span class="live-core"></span></div>',
      className: "live-location-icon", iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -14]
    });
  }

  getCenter() { const c = this.map.getCenter(); return { lat: c.lat, lng: c.lng }; }
  getZoom() { return this.map.getZoom(); }
  setCenter(lat, lng, zoom) { this.map.setView([lat, lng], zoom || this.map.getZoom(), { animate: true }); }
  panTo(lat, lng) { this.map.panTo([lat, lng], { animate: true, duration: 0.5 }); }
  onMoveEnd(cb) { this.map.on("moveend", cb); }
  onClick(cb) { this.map.on("click", (e) => cb(e.latlng.lat, e.latlng.lng)); }
  setCursor(c) { this.map.getContainer().style.cursor = c || ""; }
  invalidate() { this.map.invalidateSize(); }

  mountCard(el) {
    const control = L.control({ position: "topright" });
    control.onAdd = () => { L.DomEvent.disableClickPropagation(el); return el; };
    control.addTo(this.map);
  }

  setVehicle(pos, popupHtml) {
    if (!pos) { if (this.vehicle) { this.map.removeLayer(this.vehicle); this.vehicle = null; } return; }
    if (!this.vehicle) this.vehicle = L.marker([pos.lat, pos.lng], { icon: this.carIcon, zIndexOffset: 900 }).addTo(this.map);
    this.vehicle.setLatLng([pos.lat, pos.lng]);
    this.vehicle.bindPopup(popupHtml);
  }

  setLive(pos, accuracy, popupHtml) {
    if (!pos) {
      if (this.live) { this.map.removeLayer(this.live); this.map.removeLayer(this.liveCircle); this.live = this.liveCircle = null; }
      return;
    }
    if (!this.live) {
      this.live = L.marker([pos.lat, pos.lng], { icon: this.dotIcon, zIndexOffset: 800 }).addTo(this.map);
      this.liveCircle = L.circle([pos.lat, pos.lng], { radius: accuracy, color: "#2563EB", weight: 1, fillColor: "#3B82F6", fillOpacity: 0.12, interactive: false }).addTo(this.map);
    }
    this.live.setLatLng([pos.lat, pos.lng]);
    this.liveCircle.setLatLng([pos.lat, pos.lng]).setRadius(accuracy);
    this.live.bindPopup(popupHtml);
  }
  openLivePopup() { if (this.live) this.live.openPopup(); }

  setTrail(points) { this.trail.setLatLngs(points.map(p => [p.lat, p.lng])); }

  setZones(items) {
    this.zoneLayer.clearLayers();
    items.forEach(it => {
      if (it.kind === "ring") {
        L.circle([it.lat, it.lng], { radius: it.radius, color: it.colour, weight: 2, dashArray: "8, 8", fill: false, interactive: false }).addTo(this.zoneLayer);
      } else if (it.kind === "circle") {
        L.circle([it.lat, it.lng], { radius: it.radius, color: it.colour, weight: 2, fillColor: it.colour, fillOpacity: 0.14 }).addTo(this.zoneLayer);
      } else {
        const icon = L.divIcon({ html: `<div style="filter:drop-shadow(0 3px 3px rgba(0,0,0,.35))">${zonePinSvg(it.colour)}</div>`, className: "zone-pin", iconSize: [28, 38], iconAnchor: [14, 37], popupAnchor: [0, -34] });
        L.marker([it.lat, it.lng], { icon, riseOnHover: true }).bindPopup(it.popupHtml).addTo(this.zoneLayer);
      }
    });
  }

  destroy() { this.map.remove(); }
}

// =====================================================================================================================
// View 2: the real Google Maps (JavaScript API). Needs a key from the admin panel.
// =====================================================================================================================
class GoogleView {
  constructor(containerId, center, zoom) {
    this.kind = "google";
    const g = google.maps;
    this.el = document.getElementById(containerId);
    this.map = new g.Map(this.el, {
      center: center, zoom: zoom, mapTypeControl: true, streetViewControl: true, fullscreenControl: true,
      gestureHandling: "greedy", clickableIcons: true,
    });
    this.info = new g.InfoWindow();
    this.ready = false;                                   // becomes true when Google has really drawn its map
    this.map.addListener("tilesloaded", () => { this.ready = true; });
    this.vehicle = null;
    this.live = null;
    this.liveCircle = null;
    this.zoneObjects = [];
    this.trail = new g.Polyline({ map: this.map, path: [], strokeColor: "#16A34A", strokeOpacity: 0.85, strokeWeight: 3 });
    // Google's own traffic layer (live traffic, which already includes slow-downs after crashes)
    this.traffic = new g.TrafficLayer();
    this.trafficOn = false;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-outline btn-sm google-traffic-btn";
    button.textContent = "Traffic";
    button.addEventListener("click", () => {
      this.trafficOn = !this.trafficOn;
      this.traffic.setMap(this.trafficOn ? this.map : null);
      button.classList.toggle("btn-teal", this.trafficOn);
      button.classList.toggle("btn-outline", !this.trafficOn);
    });
    this.map.controls[g.ControlPosition.LEFT_BOTTOM].push(button);
  }

  icon(svg, w, h, ax, ay) {
    const g = google.maps;
    return { url: "data:image/svg+xml;charset=UTF-8," + encodeURIComponent(svg), scaledSize: new g.Size(w, h), anchor: new g.Point(ax, ay) };
  }
  getCenter() { const c = this.map.getCenter(); return { lat: c.lat(), lng: c.lng() }; }
  getZoom() { return this.map.getZoom(); }
  setCenter(lat, lng, zoom) { this.map.panTo({ lat, lng }); if (zoom) this.map.setZoom(zoom); }
  panTo(lat, lng) { this.map.panTo({ lat, lng }); }
  onMoveEnd(cb) { this.map.addListener("idle", cb); }
  onClick(cb) { this.map.addListener("click", (e) => cb(e.latLng.lat(), e.latLng.lng())); }
  setCursor(c) { this.map.setOptions({ draggableCursor: c || null }); }
  invalidate() { google.maps.event.trigger(this.map, "resize"); }

  mountCard(el) { this.map.controls[google.maps.ControlPosition.TOP_RIGHT].push(el); }

  openInfo(marker, html) { this.info.setContent(html); this.info.open({ map: this.map, anchor: marker }); }

  setVehicle(pos, popupHtml) {
    const g = google.maps;
    if (!pos) { if (this.vehicle) { this.vehicle.setMap(null); this.vehicle = null; } return; }
    if (!this.vehicle) {
      this.vehicle = new g.Marker({ map: this.map, position: pos, zIndex: 900, icon: this.icon(carIconSvg(), 44, 44, 22, 22) });
      this.vehicle.addListener("click", () => this.openInfo(this.vehicle, this.vehicleHtml));
    }
    this.vehicle.setPosition(pos);
    this.vehicleHtml = popupHtml;
  }

  setLive(pos, accuracy, popupHtml) {
    const g = google.maps;
    if (!pos) {
      if (this.live) { this.live.setMap(null); this.liveCircle.setMap(null); this.live = this.liveCircle = null; }
      return;
    }
    if (!this.live) {
      this.live = new g.Marker({ map: this.map, position: pos, zIndex: 800, icon: this.icon(liveDotSvg(), 26, 26, 13, 13) });
      this.live.addListener("click", () => this.openInfo(this.live, this.liveHtml));
      this.liveCircle = new g.Circle({ map: this.map, center: pos, radius: accuracy, strokeColor: "#2563EB", strokeWeight: 1, strokeOpacity: 0.6, fillColor: "#3B82F6", fillOpacity: 0.12, clickable: false });
    }
    this.live.setPosition(pos);
    this.liveCircle.setCenter(pos);
    this.liveCircle.setRadius(accuracy);
    this.liveHtml = popupHtml;
  }
  openLivePopup() { if (this.live) this.openInfo(this.live, this.liveHtml); }

  setTrail(points) { this.trail.setPath(points.map(p => ({ lat: p.lat, lng: p.lng }))); }

  setZones(items) {
    const g = google.maps;
    this.zoneObjects.forEach(o => o.setMap(null));
    this.zoneObjects = [];
    items.forEach(it => {
      const center = { lat: it.lat, lng: it.lng };
      if (it.kind === "ring") {
        this.zoneObjects.push(new g.Circle({ map: this.map, center, radius: it.radius, strokeColor: it.colour, strokeWeight: 2, strokeOpacity: 0.8, fillOpacity: 0, clickable: false }));
      } else if (it.kind === "circle") {
        this.zoneObjects.push(new g.Circle({ map: this.map, center, radius: it.radius, strokeColor: it.colour, strokeWeight: 2, strokeOpacity: 0.9, fillColor: it.colour, fillOpacity: 0.14, clickable: false }));
      } else {
        const marker = new g.Marker({ map: this.map, position: center, icon: this.icon(zonePinSvg(it.colour), 28, 38, 14, 37), title: it.title || "" });
        marker.addListener("click", () => this.openInfo(marker, it.popupHtml));
        this.zoneObjects.push(marker);
      }
    });
  }

  destroy() { this.el.innerHTML = ""; }
}

// =====================================================================================================================
// The manager: all the logic. It keeps the state and tells the current view what to draw.
// =====================================================================================================================
class VehicleMapManager {
  constructor() {
    this.view = null;
    this.defaultLat = 13.0827;
    this.defaultLon = 80.2707;

    this.vehicle = null;                 // {lat, lng} of the car (ESP32 GPS, or this laptop when the GPS has no fix)
    this.vehicleHtml = "";
    this.live = null;                    // {lat, lng, accuracy} of this device (browser location)
    this.liveState = "waiting";          // waiting | on | off
    this.trailCoordinates = [];
    this.maxTrailPoints = 80;
    this.followVehicle = true;


    this.zonesData = [];
    this.showAllZones = true;            // every danger spot has a small pin; the circle and the notification are for the ones near the car (or near you)
    this.nearestSpot = null;             // the closest danger spot to the car (or to you), for the "Go to nearest" button
    this.alertDistanceM = 1000;          // the notification distance chosen in Danger Zone Management; also what "near" means on the map
    this.areaZoneMinRadiusM = 1500;      // bigger than this = a whole district, not a spot: no fill, shown only when you are inside
    this.alerted = new Set();            // zones already announced (so a notification shows once per approach)
    this.lastNearRefresh = 0;
    this.lastNearPoint = null;

    this.pickModeActive = false;
    this.onMapClickCallback = null;

    this.card = this.buildCard();
    this.startView(() => new LeafletView("leaflet-map", { lat: this.defaultLat, lng: this.defaultLon }, 14));
    this.loadAlertDistance();
    this.loadDangerZones();
    this.loadMapsConfig();
  }

  get mapKind() { return this.view ? this.view.kind : "leaflet"; }

  // ---- views ----
  // `create` makes the new view. The old one is removed first, because both draw into the same page element.
  startView(create) {
    if (this.view) this.view.destroy();
    this.view = null;
    const view = create();
    this.view = view;
    view.mountCard(this.card);
    view.onClick((lat, lng) => {                     // pick mode: the next click gives the coordinates for a new danger zone
      if (this.pickModeActive && this.onMapClickCallback) { this.onMapClickCallback(lat, lng); this.disablePickMode(); }
    });
    view.onMoveEnd(() => { if (!this.currentPoint()) this.refreshNearZones(); });
    this.redrawAll();
    const where = this.currentPoint();
    if (where) view.setCenter(where.lat, where.lng, 15);
    const badge = document.getElementById("map-provider-badge");
    if (badge) badge.textContent = view.kind === "google" ? "Google Maps" : "Street map";
  }

  redrawAll() {
    this.drawVehicle();
    this.drawLive();
    this.view.setTrail(this.trailCoordinates);
    this.refreshNearZones();
  }

  // Google Maps when the admin has saved a key. If Google refuses the key, the street map stays.
  loadMapsConfig() {
    fetch("/api/maps-config").then(r => r.json()).then(cfg => {
      if (!cfg || !cfg.google_key) return;
      window.gm_authFailure = () => this.fallbackToStreetMap("The Google Maps key was refused by Google. Check the key and its allowed websites. Showing the street map instead.");
      this.loadGoogleScript(cfg.google_key).then(() => {
        const c = this.view.getCenter();
        const zoom = this.view.getZoom ? this.view.getZoom() : 14;
        this.startView(() => new GoogleView("leaflet-map", c, zoom));
        this.watchForGoogleError();
      }).catch(() => this.fallbackToStreetMap("Google Maps could not be loaded (internet or key). Showing the street map instead."));
    }).catch(() => {});
  }

  // A wrong, expired or not-allowed key leaves Google's map blank or shows its own "Oops! Something went wrong" panel.
  // So the page checks that Google really loaded its map ("tilesloaded"); if not within 15 seconds, the street map takes over
  // and the page is never left with a broken map.
  watchForGoogleError() {
    const container = document.getElementById("leaflet-map");
    const view = this.view;
    let waited = 0;
    const timer = setInterval(() => {
      waited += 1;
      if (this.view !== view || view.kind !== "google") { clearInterval(timer); return; }       // already replaced
      if (view.ready) { clearInterval(timer); return; }                                          // the map loaded: all good
      if ((container && container.querySelector(".gm-err-container")) || waited >= 15) {
        clearInterval(timer);
        this.fallbackToStreetMap("Google Maps did not load with this key (wrong key, Maps JavaScript API not enabled, or the website not allowed). Showing the street map instead.");
      }
    }, 1000);
  }

  loadGoogleScript(key) {
    return new Promise((resolve, reject) => {
      if (window.google && window.google.maps) { resolve(); return; }
      const timer = setTimeout(() => reject(new Error("timeout")), 12000);
      window.__acciGoogleReady = () => { clearTimeout(timer); resolve(); };
      const s = document.createElement("script");
      s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&callback=__acciGoogleReady`;
      s.async = true;
      s.onerror = () => { clearTimeout(timer); reject(new Error("load")); };
      document.head.appendChild(s);
    });
  }

  fallbackToStreetMap(message) {
    if (this.view && this.view.kind === "leaflet") { if (window.DashboardApp) window.DashboardApp.showToast(message, "warning"); return; }
    const c = this.view ? this.view.getCenter() : { lat: this.defaultLat, lng: this.defaultLon };
    this.startView(() => new LeafletView("leaflet-map", c, 14));
    if (window.DashboardApp) window.DashboardApp.showToast(message, "warning");
  }

  // Used by the pages: re-measure the map after it was hidden, or move it to a place
  invalidate() { if (this.view) this.view.invalidate(); }
  goTo(lat, lng, zoom) { if (this.view) this.view.setCenter(lat, lng, zoom); }

  // ---- the small card on the map: live location, how many danger zones are shown, the nearest one, a switch for all ----
  buildCard() {
    const box = document.createElement("div");
    box.className = "zone-near-card";
    box.innerHTML = '<div class="zone-live-text" id="zone-live-text">Live location: waiting...</div>' +
      '<div class="zone-near-text" id="zone-near-text">Danger zones near you: --</div>' +
      '<div class="zone-nearest-text" id="zone-nearest-text">Nearest danger spot: --</div>' +
      '<button type="button" class="btn btn-outline btn-sm zone-go-nearest" id="zone-go-nearest">Go to nearest danger spot</button>' +
      '<label class="zone-near-switch"><input type="checkbox" id="zone-show-all" checked> Show all danger spots</label>' +
      '<div class="zone-provider">Map: <b id="map-provider-badge">Street map</b></div>';
    box.querySelector("input").addEventListener("change", (e) => { this.showAllZones = e.target.checked; this.refreshNearZones(); });
    box.querySelector("#zone-go-nearest").addEventListener("click", () => {
      if (this.nearestSpot) this.view.setCenter(this.nearestSpot.lat, this.nearestSpot.lng, 16);
      else if (window.DashboardApp) window.DashboardApp.showToast("There are no danger spots to show yet.", "info");
    });
    return box;
  }

  // ---- live location of this device (the browser's location, updated while you move) ----
  updateLiveLocation(lat, lon, accuracyM) {
    if (isNaN(lat) || isNaN(lon)) return;
    const first = !this.live;
    this.live = { lat, lng: lon, accuracy: Math.min(Math.max(Number(accuracyM) || 0, 0), 500), raw: Number(accuracyM) || 0 };
    this.liveState = "on";
    this.showLiveStatus(`Live location: on (about ${Math.round(this.live.raw)} m)`);
    if (first && !this.vehicle) this.view.setCenter(lat, lon, 15);                  // first fix: go there
    if (!this.vehicle) {
      const coordEl = document.getElementById("map-coords-readout");
      if (coordEl) coordEl.innerText = `Live: ${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    }
    this.drawLive();
    this.checkZoneAlerts();
    const moved = this.lastNearPoint && !this.vehicle ? distanceM(this.lastNearPoint, this.live) : Infinity;
    if (moved > 150) this.refreshNearZones();
  }

  liveLocationOff(reason) {
    this.liveState = "off";
    this.showLiveStatus(reason || "Live location: off (allow location in the browser)");
  }

  showLiveStatus(text) {
    const el = document.getElementById("zone-live-text");
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("off", this.liveState === "off");
  }

  centerOnLive() {
    if (this.live) { this.view.setCenter(this.live.lat, this.live.lng, 16); this.view.openLivePopup(); }
    else if (window.DashboardApp) window.DashboardApp.showToast("Your live location is not available yet. Allow location access in the browser.", "warning");
  }

  // The blue dot is this device's live location. It stays on the map; the car icon sits on top of it when they are together.
  // The blue dot is this device's live location. It stays on the map; the car icon sits on top of it when they are together.
  drawLive() {
    if (!this.live) { this.view.setLive(null); return; }
    this.view.setLive(this.live, this.live.accuracy,
      `<div style="font-size:0.82rem; line-height:1.4;"><strong style="color:#2563EB;">Your live location</strong><br/>${this.live.lat.toFixed(5)}, ${this.live.lng.toFixed(5)}<br/>Accuracy: about ${Math.round(this.live.raw)} m</div>`);
  }

  drawVehicle() {
    this.view.setVehicle(this.vehicle, this.vehicleHtml);
  }

  // ---- the notification distance (Danger Zone Management) ----
  loadAlertDistance() {
    fetch("/api/zone-settings").then(r => r.json()).then(d => { if (d && d.alert_distance_m) this.setAlertDistance(d.alert_distance_m); }).catch(() => {});
  }

  setAlertDistance(metres) {
    const value = Number(metres);
    if (!(value >= 50)) return;
    this.alertDistanceM = value;
    this.alerted.clear();
    this.refreshNearZones();
  }

  formatMetres(m) {
    return m >= 1000 ? `${(m / 1000).toFixed(m % 1000 === 0 ? 0 : 1)} km` : `${Math.round(m)} m`;
  }

  // Where the car is (the blue dot of the vehicle), or else this device's live location. No guess from the map centre.
  currentPoint() {
    return this.vehicle || this.live || null;
  }

  // Shows the danger-zone notification when the car (or you) gets within the chosen distance of a spot, once per approach
  checkZoneAlerts() {
    const here = this.currentPoint();
    const nearestEl = document.getElementById("zone-nearest-text");
    if (!here) { if (nearestEl) nearestEl.textContent = "Nearest danger spot: --"; return; }
    let nearest = null;
    const fresh = [];
    (this.zonesData || []).forEach(zone => {
      const lat = parseFloat(zone.latitude), lon = parseFloat(zone.longitude), radius = parseFloat(zone.radius_meters);
      if (isNaN(lat) || isNaN(lon) || isNaN(radius) || radius >= this.areaZoneMinRadiusM) return;      // spots only, not districts
      const edge = distanceM(here, { lat, lng: lon }) - radius;
      if (!nearest || edge < nearest.edge) nearest = { zone, edge };
      const id = String(zone.code || zone.name);
      if (edge <= this.alertDistanceM) {
        if (!this.alerted.has(id)) { this.alerted.add(id); fresh.push({ zone, edge }); }
      } else if (edge > this.alertDistanceM * 1.2 + 50) {
        this.alerted.delete(id);                           // far enough away: ready to announce it again
      }
    });
    if (nearestEl) {
      nearestEl.textContent = nearest
        ? (nearest.edge <= 0 ? `Inside: ${nearest.zone.name}` : `Nearest danger spot: ${nearest.zone.name}, ${this.formatMetres(nearest.edge)}`)
        : "Nearest danger spot: --";
    }
    if (!fresh.length || !window.DashboardApp) return;
    fresh.sort((a, b) => a.edge - b.edge);
    const first = fresh[0];
    let message;
    if (fresh.length === 1) {
      message = first.edge <= 0
        ? `You are inside a danger zone: ${first.zone.name}. Speed limit ${first.zone.speed_limit} km/h.`
        : `Danger zone ahead: ${first.zone.name}, about ${this.formatMetres(first.edge)} away. Speed limit ${first.zone.speed_limit} km/h.`;
    } else {
      message = `${fresh.length} danger zones within ${this.formatMetres(this.alertDistanceM)}: ` +
        fresh.slice(0, 3).map(f => `${f.zone.name} (${this.formatMetres(Math.max(0, f.edge))})`).join(", ") + (fresh.length > 3 ? ", ..." : "") + ".";
    }
    window.DashboardApp.showToast(message, first.edge <= 0 ? "danger" : "warning");
  }

  // ---- the car ----
  // No GPS and no laptop location: take the car off the map instead of showing an old or made-up spot.
  clearVehicle() {
    this.vehicle = null;
    this.drawVehicle();
    this.drawLive();
    const coordEl = document.getElementById("map-coords-readout");
    if (coordEl && !this.live) coordEl.innerText = "No position";
    this.refreshNearZones();
  }

  updateVehiclePosition(lat, lon, speed, state, driverName, source) {
    if (isNaN(lat) || isNaN(lon) || (lat === 0 && lon === 0)) return;
    const first = !this.vehicle;
    this.vehicle = { lat, lng: lon };
    this.vehicleHtml = `
      <div style="font-size:0.82rem; line-height:1.4;">
        <strong style="color:#16A34A; font-size:0.9rem;">AcciAlert Vehicle</strong><br/>
        <b>Driver:</b> ${escapeHtml(driverName || "Unknown")}<br/>
        <b>Speed:</b> ${speed ? speed.toFixed(1) : 0.0} km/h<br/>
        <b>State:</b> <span class="badge ${(state === 'RUNNING' || state === 'MOVING') ? 'badge-success' : 'badge-danger'}">${state || 'READY'}</span><br/>
        <b>${source === "LAPTOP" ? "Location" : "GPS"}:</b> ${lat.toFixed(5)}, ${lon.toFixed(5)}
      </div>`;
    if (first) this.view.setCenter(lat, lon, 15);                  // first real position: go to it
    this.drawVehicle();
    this.drawLive();

    this.trailCoordinates.push({ lat, lng: lon });
    if (this.trailCoordinates.length > this.maxTrailPoints) this.trailCoordinates.shift();
    this.view.setTrail(this.trailCoordinates);

    this.checkZoneAlerts();
    // Danger zones follow the car: redraw when it has moved 150 m (or every 10 s)
    const moved = this.lastNearPoint ? distanceM(this.lastNearPoint, this.vehicle) : Infinity;
    if (moved > 150 || Date.now() - this.lastNearRefresh > 10000) this.refreshNearZones();

    if (this.followVehicle) this.view.panTo(lat, lon);
    const coordEl = document.getElementById("map-coords-readout");
    if (coordEl) coordEl.innerText = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  }

  centerOnVehicle() {
    if (this.vehicle) this.view.setCenter(this.vehicle.lat, this.vehicle.lng, 15);
  }

  toggleFollow() {
    this.followVehicle = !this.followVehicle;
    const btn = document.getElementById("btn-map-follow");
    if (btn) {
      btn.classList.toggle("btn-teal", this.followVehicle);
      btn.classList.toggle("btn-outline", !this.followVehicle);
      btn.innerHTML = this.followVehicle ? 'Following' : 'Free view';
    }
    if (this.followVehicle) this.centerOnVehicle();
  }

  clearTrail() {
    this.trailCoordinates = [];
    this.view.setTrail([]);
    if (window.DashboardApp) window.DashboardApp.showToast("Vehicle path trail cleared.", "info");
  }

  enablePickMode(callback) {
    this.pickModeActive = true;
    this.onMapClickCallback = callback;
    this.view.setCursor("crosshair");
    if (window.DashboardApp) window.DashboardApp.showToast("Click anywhere on the map to set zone coordinates.", "warning");
  }

  disablePickMode() {
    this.pickModeActive = false;
    this.view.setCursor("");
  }

  // ---- danger zones ----
  loadDangerZones() {
    fetch("/api/zones")
      .then(res => res.json())
      .then(data => {
        if (data.status === "success" && Array.isArray(data.zones)) this.renderDangerZones(data.zones);
      })
      .catch(err => console.error("Error loading danger zones:", err));
  }

  renderDangerZones(zones) {
    this.zonesData = zones || [];
    this.refreshNearZones();
  }

  // Draws only the danger zones that matter now: spots near the car (a pin and a light circle), and the district
  // you are inside (a thin outline, no fill). "Show all zones" adds a pin for every spot.
  refreshNearZones() {
    if (!this.view) return;
    this.checkZoneAlerts();
    // "near" = near the car; with no car, near this device's live location; with neither, near the middle of the map
    const here = this.currentPoint() || this.view.getCenter();
    this.lastNearPoint = { lat: here.lat, lng: here.lng };
    this.lastNearRefresh = Date.now();

    const items = [];
    const insideAreas = [];
    let shown = 0, nearCount = 0;
    this.nearestSpot = null;
    let nearestEdge = Infinity;
    (this.zonesData || []).forEach(zone => {
      const lat = parseFloat(zone.latitude), lon = parseFloat(zone.longitude), radius = parseFloat(zone.radius_meters);
      if (isNaN(lat) || isNaN(lon) || isNaN(radius)) return;
      const sev = (zone.severity || "MEDIUM").toUpperCase();
      const colour = SEVERITY_COLOURS[sev] || SEVERITY_COLOURS.MEDIUM;
      const distance = distanceM(here, { lat, lng: lon });

      if (radius >= this.areaZoneMinRadiusM) {            // a whole district: outline only, and only when you are inside it
        if (distance <= radius) { items.push({ kind: "ring", lat, lng: lon, radius, colour }); insideAreas.push(zone.name); }
        return;
      }
      const near = distance <= this.alertDistanceM + radius;
      if (distance - radius < nearestEdge) { nearestEdge = distance - radius; this.nearestSpot = { lat, lng: lon }; }
      if (!near && !this.showAllZones) return;
      shown += 1;
      if (near) { nearCount += 1; items.push({ kind: "circle", lat, lng: lon, radius, colour }); }
      items.push({ kind: "pin", lat, lng: lon, colour, title: zone.name, popupHtml: this.zonePopup(zone, sev, colour) });
    });
    this.view.setZones(items);

    const text = document.getElementById("zone-near-text");
    if (text) {
      const within = this.formatMetres(this.alertDistanceM);
      const line = this.showAllZones ? `Danger spots on the map: ${shown} (${nearCount} within ${within})` : `Danger spots within ${within}: ${shown}`;
      text.innerHTML = escapeHtml(line) + (insideAreas.length ? `<div class="zone-near-area">Inside: ${insideAreas.map(escapeHtml).join(", ")}</div>` : "");
    }
  }

  zonePopup(zone, sev, colour) {
    const lat = parseFloat(zone.latitude), lon = parseFloat(zone.longitude);
    return `
      <div style="font-size:0.82rem; line-height:1.45; min-width:200px;">
        <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:4px;">
          <strong style="color:${colour}; font-size:0.92rem;">${escapeHtml(zone.name)}</strong>
          <span class="badge ${sev === 'CRITICAL' ? 'badge-danger' : sev === 'HIGH' ? 'badge-warning' : 'badge-info'}" style="font-size:0.65rem;">${sev}</span>
        </div>
        <b>Zone code:</b> ${escapeHtml(zone.code)}<br/>
        <b>Speed limit:</b> <span style="color:#16A34A; font-weight:700;">${zone.speed_limit} km/h</span><br/>
        <b>Radius:</b> ${zone.radius_meters} m<br/>
        <div style="margin-top:6px; color:#4B5563; font-size:0.75rem;">${escapeHtml(zone.details)}</div>
        <a href="https://www.google.com/maps/search/?api=1&query=${lat},${lon}" target="_blank" rel="noopener" class="btn btn-outline btn-sm" style="display:block; text-align:center; margin-top:8px; text-decoration:none;">Open in Google Maps</a>
        <button onclick="window.DashboardApp.applySpeedLimit(${zone.speed_limit})" class="btn btn-teal btn-sm" style="width:100%; margin-top:6px; padding:4px 8px; font-size:0.75rem;">
          Enforce limit (${zone.speed_limit} km/h)
        </button>
      </div>`;
  }
}

// Global instance
window.MapManager = null;
