/* Zone manager + zone event table. Plain JS + Leaflet, talks to /api/zones and /api/events. */
(function () {
  const COLORS = { critical: "#e11d48", high: "#f97316", medium: "#ca8a04", low: "#22c55e" };
  // Defaults per zone type (requirements 8.1)
  const PRESETS = {
    blackspot:    { radius_m: 30, speed_limit: 10, severity: "high" },
    curve:        { radius_m: 25, speed_limit: 8,  severity: "medium" },
    school:       { radius_m: 30, speed_limit: 5,  severity: "high" },
    construction: { radius_m: 25, speed_limit: 5,  severity: "medium" },
    restricted:   { radius_m: 20, speed_limit: 0,  severity: "critical" },
    hospital:     { radius_m: 30, speed_limit: 5,  severity: "high" },
  };

  const $ = (id) => document.getElementById(id);
  const form = $("zoneForm");
  const map = L.map("map").setView([13.0827, 80.2707], 16);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  let zones = [];
  let layers = {};      // zone id -> L.circle
  let editingId = null; // null = creating
  let draft = null;     // preview marker for a not-yet-saved zone

  function setStatus(text, cls) { const s = $("status"); s.textContent = text; s.className = cls || ""; }

  // Works standalone ("/") and when mounted inside another app ("/zm/").
  const BASE = location.pathname.replace(/\/[^\/]*$/, "");

  async function api(method, url, body) {
    const res = await fetch(BASE + url, {
      method, headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // ------------------------------------------------------------------ zones
  async function loadZones(fit) {
    try {
      zones = await api("GET", "/api/zones");
      setStatus("connected - " + zones.length + " zones", "ok");
    } catch (e) { setStatus("API error: " + e.message, "err"); return; }
    drawZones(fit);
    renderList();
  }

  function drawZones(fit) {
    Object.values(layers).forEach((l) => map.removeLayer(l));
    layers = {};
    zones.forEach((z) => {
      const c = L.circle([z.lat, z.lon], {
        radius: z.radius_m, color: COLORS[z.severity], weight: 2,
        fillOpacity: z.active ? 0.25 : 0.05, dashArray: z.active ? null : "6 6",
      }).addTo(map);
      c.bindTooltip(esc(z.name) + " - limit " + z.speed_limit + " km/h");
      c.on("click", (ev) => { L.DomEvent.stopPropagation(ev); startEdit(z.id); });
      layers[z.id] = c;
    });
    if (fit && zones.length) map.fitBounds(L.featureGroup(Object.values(layers)).getBounds().pad(0.2));
  }

  function renderList() {
    $("zoneCount").textContent = "(" + zones.length + ")";
    const ul = $("zoneList");
    ul.innerHTML = "";
    zones.forEach((z) => {
      const li = document.createElement("li");
      if (!z.active) li.className = "off";
      li.innerHTML =
        '<span class="dot" style="background:' + COLORS[z.severity] + '"></span>' +
        '<div class="info"><b>' + esc(z.id) + " - " + esc(z.name) + "</b><span>" +
        z.radius_m + " m, " + z.speed_limit + " km/h, " + esc(z.severity) + "</span></div>";
      const mk = (label, cls, fn) => {
        const b = document.createElement("button");
        b.textContent = label; if (cls) b.className = cls; b.onclick = fn; li.appendChild(b);
      };
      mk(z.active ? "Off" : "On", "ghost", () => save(Object.assign({}, z, { active: z.active ? 0 : 1 }), z.id));
      mk("Edit", "ghost", () => startEdit(z.id));
      mk("Del", "danger", () => remove(z));
      ul.appendChild(li);
    });
  }

  // ------------------------------------------------------------------- form
  function nextId() {
    let n = 1;
    const used = new Set(zones.map((z) => z.id));
    while (used.has("Z" + String(n).padStart(2, "0"))) n++;
    return "Z" + String(n).padStart(2, "0");
  }

  function fill(z) {
    Object.keys(z).forEach((k) => {
      const el = form.elements[k];
      if (!el) return;
      if (el.type === "checkbox") el.checked = !!z[k]; else el.value = z[k];
    });
  }

  function startNew(lat, lon) {
    editingId = null;
    form.reset();
    const p = PRESETS[form.elements.type.value];
    fill(Object.assign({ id: nextId(), lat: lat.toFixed(6), lon: lon.toFixed(6), active: 1 }, p));
    form.elements.id.readOnly = false;
    $("formTitle").textContent = "New zone";
    showDraft();
    $("formMsg").textContent = "";
  }

  function startEdit(id) {
    const z = zones.find((x) => x.id === id);
    if (!z) return;
    editingId = id;
    fill(z);
    form.elements.id.readOnly = true;
    $("formTitle").textContent = "Edit " + id;
    clearDraft();
    $("formMsg").textContent = "";
  }

  function showDraft() {
    clearDraft();
    const lat = parseFloat(form.elements.lat.value), lon = parseFloat(form.elements.lon.value);
    const r = parseFloat(form.elements.radius_m.value);
    if (isNaN(lat) || isNaN(lon) || !(r > 0)) return;
    draft = L.circle([lat, lon], { radius: r, color: "#A16207", dashArray: "4 4", fillOpacity: 0.15 }).addTo(map);
  }
  function clearDraft() { if (draft) { map.removeLayer(draft); draft = null; } }

  map.on("click", (e) => startNew(e.latlng.lat, e.latlng.lng));
  window.addEventListener("resize", () => map.invalidateSize());

  form.elements.type.addEventListener("change", () => {
    if (editingId) return;
    fill(PRESETS[form.elements.type.value]); showDraft();
  });
  ["lat", "lon", "radius_m"].forEach((n) => form.elements[n].addEventListener("input", () => { if (!editingId) showDraft(); }));

  $("cancelBtn").onclick = () => { editingId = null; form.reset(); form.elements.id.readOnly = false; clearDraft(); $("formTitle").textContent = "New zone"; $("formMsg").textContent = ""; };

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = form.elements;
    save({
      id: f.id.value.trim(), name: f.name.value.trim(), type: f.type.value,
      lat: parseFloat(f.lat.value), lon: parseFloat(f.lon.value),
      radius_m: parseFloat(f.radius_m.value), speed_limit: parseInt(f.speed_limit.value, 10),
      severity: f.severity.value, active: f.active.checked ? 1 : 0,
    }, editingId);
  });

  async function save(z, existingId) {
    const msg = $("formMsg");
    try {
      if (existingId) await api("PUT", "/api/zones/" + encodeURIComponent(existingId), z);
      else await api("POST", "/api/zones", z);
      msg.className = "ok"; msg.textContent = "Saved " + z.id;
      clearDraft(); editingId = null; form.reset(); form.elements.id.readOnly = false;
      $("formTitle").textContent = "New zone";
      await loadZones(false);
    } catch (e) { msg.className = ""; msg.textContent = e.message; }
  }

  async function remove(z) {
    if (!confirm("Delete zone " + z.id + " (" + z.name + ")?")) return;
    try { await api("DELETE", "/api/zones/" + encodeURIComponent(z.id)); await loadZones(false); }
    catch (e) { $("formMsg").className = ""; $("formMsg").textContent = e.message; }
  }

  // ----------------------------------------------------------------- events
  function evQuery() {
    const p = new URLSearchParams();
    if ($("fType").value) p.set("type", $("fType").value);
    if ($("fFrom").value) p.set("from", new Date($("fFrom").value + "T00:00:00").getTime() / 1000);
    if ($("fTo").value) p.set("to", new Date($("fTo").value + "T23:59:59").getTime() / 1000);
    return p.toString();
  }

  let lastRows = "";

  // True when nobody can see this page (browser tab hidden, or the dashboard has closed the overlay)
  function isHidden() {
    return document.hidden || (window.frameElement && window.frameElement.offsetParent === null);
  }

  async function loadEvents() {
    if (isHidden()) return;
    const q = evQuery();
    $("csvLink").href = BASE + "/api/events.csv" + (q ? "?" + q : "");
    try {
      const rows = await api("GET", "/api/events" + (q ? "?" + q : ""));
      const fingerprint = q + "|" + rows.length + "|" + (rows[0] ? rows[0].id : "");
      if (fingerprint === lastRows) return;      // nothing new: keep the table as it is
      lastRows = fingerprint;
      $("evBody").innerHTML = rows.map((r) =>
        "<tr><td>" + new Date(r.timestamp * 1000).toLocaleString() + "</td><td>" + esc(r.type) +
        '</td><td class="sev-' + esc(r.severity) + '">' + esc(r.severity) + "</td><td>" + esc(r.driver) +
        "</td><td>" + (r.speed == null ? "" : Number(r.speed).toFixed(1)) + "</td><td>" +
        (r.lat == null ? "" : Number(r.lat).toFixed(5) + ", " + Number(r.lon).toFixed(5)) +
        "</td><td>" + esc(r.zone_id) + "</td><td>" + esc(r.details) + "</td></tr>"
      ).join("") || '<tr><td colspan="8">No events</td></tr>';
    } catch (e) { setStatus("API error: " + e.message, "err"); }
  }

  $("fApply").onclick = loadEvents;
  loadZones(true);
  loadEvents();
  setInterval(loadEvents, 3000); // simple polling; live push comes from Muniyappan's WebSocket layer
})();
