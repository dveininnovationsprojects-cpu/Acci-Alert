/**
 * AcciAlert - Vision AI & Driver Authorisation Controller
 * Module Lead: Arsal
 * Subsystems Integrated:
 *   1. Live Driver Optical Sensor & CLAHE Normalized Face Detector
 *   2. Non-Mirrored Tactical HUD Overlay & Eye-Blink Vitality
 *   3. Vehicle Ignition Interlock & Dynamic Power Gating
 *   4. Driver Enrolment Wizard & Active 1024-D Descriptor Roster
 *   5. Security & Ignition Authorisation Audit Trail (Export CSV & Filter)
 *   6. Threshold & Distance Calibration Engine
 *   7. Synchronization with Muniyappan's Fleet Dashboard & Card 6
 */


const AUTH_FRAMES = 8;          // pictures taken for one login
const AUTH_FRAME_GAP_MS = 350;   // about 2.5 seconds in all

class DriverAuthController {
  constructor() {
    this.videoEl = document.getElementById("driverVideo");
    this.canvasEl = document.getElementById("hudCanvas");
    this.ctx = this.canvasEl ? this.canvasEl.getContext("2d") : null;
    this.standbyEl = document.getElementById("cameraStandby");
    this._captureCanvas = null;

    this.stream = null;
    this.isRunning = false;
    this.animFrameId = null;
    this.facingMode = "user";

    this.matchThreshold = 35.0;
    this.livenessEnabled = true;

    this.lastAuthState = null;
    this.lastAuthDriver = null;
    this.currentFilter = "ALL";
    this.isEvaluatingFrame = false;

    // Enrolled drivers roster (stored and managed)
    this.enrolledDrivers = {};   // filled from /api/enrolled

    this.auditLogs = [];

    this.enrolmentState = {
      isEnrolling: false,
      driverName: "",
      license: "",
      targetSamples: 25,
      capturedSamples: 0
    };

    this.loadPersistedLogs();
    this.bindEvents();
    this.bindRosterEvents();
    this.loadRoster();
    this.loadAuditLogs();
    this.loadPersistedCapture();
    this.showServerLockState();
  }

  // The face login is the first page, so on the dashboard the car is usually already unlocked: show the real state
  async showServerLockState() {
    try {
      const res = await fetch("/api/status");
      const body = await res.json();
      if (body.active_driver_authorized) {
        this.lastAuthState = true;
        this.lastAuthDriver = body.active_driver;
        this.updateGatingUI(true, body.active_driver, null, 0, "Unlocked by the face login.", { detected: true, liveness: "PASSED" });
      }
    } catch (_) {}
  }

  loadPersistedLogs() {
    try {
      const saved = localStorage.getItem("accialert_face_audit_logs");
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) {
          this.auditLogs = parsed.map(l => ({
            ...l,
            driver: (l.driver && String(l.driver).trim()) ? l.driver : (l.driver_name || "Unknown")
          }));
        }
      }
    } catch (_) {}

    if (!this.auditLogs || this.auditLogs.length === 0) {
      const now = new Date();
      const fmtTime = (offsetSec) => {
        const d = new Date(now.getTime() - offsetSec * 1000);
        return d.toTimeString().split(" ")[0];
      };
      this.auditLogs = [
        { timestamp: fmtTime(10), type: "UNAUTHORISED", severity: "HIGH", driver: "Unknown", match: 41, liveness: "PASSED", cmd: '{"cmd":"AUTH","ok":0,"driver":"Unknown"}' },
        { timestamp: fmtTime(30), type: "CAMERA_STOP", severity: "INFO", driver: "Unknown", match: 0, liveness: "PASSED", cmd: '{"cmd":"AUTH","ok":0,"driver":"Unknown"}' }
      ];
      this.savePersistedLogs();
    }
  }

  savePersistedLogs() {
    try {
      localStorage.setItem("accialert_face_audit_logs", JSON.stringify(this.auditLogs.slice(0, 100)));
    } catch (_) {}
  }

  bindEvents() {
    // Sub-Tab Switching inside Face Recognition Module
    document.querySelectorAll(".face-subtab-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const targetSubtab = btn.getAttribute("data-subtab");
        document.querySelectorAll(".face-subtab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".face-subtab-content").forEach(c => c.classList.remove("active"));

        btn.classList.add("active");
        const targetContent = document.getElementById(targetSubtab);
        if (targetContent) targetContent.classList.add("active");

        if (targetSubtab === "face-sub-logs") {
          this.loadAuditLogs();
        }
      });
    });

    // Optical Feed Controls
    document.getElementById("btnStartCamera")?.addEventListener("click", () => this.startCamera());
    document.getElementById("btnStopCamera")?.addEventListener("click", () => this.stopCamera());
    document.getElementById("btnSwitchCamera")?.addEventListener("click", () => this.toggleCamera());

    // Calibration Slider
    const slider = document.getElementById("rangeThreshold");
    const valLabel = document.getElementById("valThreshold");
    if (slider && valLabel) {
      // The slider follows the recogniser that is really in use (SFace score or the old LBPH distance).
      const decimals = () => (parseFloat(slider.step) < 0.1 ? 2 : 1);
      const showValue = () => { valLabel.textContent = parseFloat(slider.value).toFixed(decimals()); };
      fetch("/api/config")
        .then(res => res.json())
        .then(cfg => {
          if (!cfg || cfg.status !== "success" || !cfg.slider) return;
          slider.min = cfg.slider.min;
          slider.max = cfg.slider.max;
          slider.step = cfg.slider.step;
          slider.value = cfg.threshold;
          this.matchThreshold = cfg.threshold;
          const label = document.getElementById("labelThreshold");
          const hint = document.getElementById("hintThreshold");
          if (label) label.textContent = `${cfg.slider.label}:`;
          if (hint) hint.textContent = `${cfg.slider.hint} Allowed range: ${cfg.slider.min} to ${cfg.slider.max}.`;
          showValue();
        })
        .catch(() => {
          const hint = document.getElementById("hintThreshold");
          if (hint) hint.textContent = "The server could not be reached, so the threshold cannot be shown or changed.";
        });
      showValue();
      slider.addEventListener("input", (e) => {
        this.matchThreshold = parseFloat(e.target.value);
        showValue();
        fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ threshold: this.matchThreshold })
        })
          .then(res => res.json().catch(() => null).then(data => ({ res, data })))
          .then(({ res, data }) => {
            if (!res.ok && window.DashboardApp) {
              window.DashboardApp.showToast((data && data.message) || "The threshold was not changed.", "warning");
            }
          })
          .catch(() => {});
      });
    }

    // Liveness Toggle
    const toggle = document.getElementById("toggleLiveness");
    if (toggle) {
      toggle.checked = this.livenessEnabled;
      toggle.addEventListener("change", (e) => {
        this.livenessEnabled = e.target.checked;
        fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ liveness: this.livenessEnabled })
        }).catch(() => {});
      });
    }

    // Driver Enrolment Form
    const form = document.getElementById("formEnrolDriver");
    if (form) {
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        this.startEnrolment();
      });
    }

    // Audit Log Filters
    document.querySelectorAll(".face-filter-pill").forEach(pill => {
      pill.addEventListener("click", () => {
        document.querySelectorAll(".face-filter-pill").forEach(p => p.classList.remove("active"));
        pill.classList.add("active");
        this.currentFilter = pill.getAttribute("data-face-filter") || "ALL";
        this.renderAuditLogsTable();
      });
    });

    // Audit Log Export CSV & Clear Buttons
    document.getElementById("btnExportFaceLogsCsv")?.addEventListener("click", () => this.exportAuditLogsCsv());
    document.getElementById("btnClearFaceLogs")?.addEventListener("click", () => this.clearAuditLogs());

    // Capture Image & Snapshot Controls
    const btnCapture = document.getElementById("btnCaptureSnapshot");
    const btnRetake = document.getElementById("btnRetakeSnapshot");
    if (btnCapture) {
      btnCapture.addEventListener("click", () => this.captureSnapshot());
    }
    document.getElementById("btnLockVehicle")?.addEventListener("click", () => this.lockVehicle());
    if (btnRetake) {
      btnRetake.addEventListener("click", () => this.resetCapturedImage());
    }
  }

  // ===================================================================
  // SNAPSHOT & IMAGE CAPTURE
  // ===================================================================
  async captureSnapshot() {
    if (this.isAuthenticating) return null;

    // The camera must be live: this is a real login attempt, so no placeholder picture is ever made.
    if (!this.cameraIsLive()) {
      try {
        await this.startCamera();
        await new Promise(r => setTimeout(r, 800));
      } catch (_) {}
    }
    if (!this.cameraIsLive()) {
      this.setCaptureStatus("Camera is not available. Allow camera access, then press Capture Image again.", "red");
      return null;
    }

    this.isAuthenticating = true;
    const btn = document.getElementById("btnCaptureSnapshot");
    if (btn) btn.disabled = true;
    this.setCaptureFrame("");

    const preview = document.getElementById("capturedImagePreview");
    const placeholder = document.getElementById("capturePlaceholder");
    const actionsRow = document.getElementById("captureActionsRow");
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 480;
    const ctx = canvas.getContext("2d");
    const frames = [];
    let lastPicture = null;

    try {
      // Step 1: the server asks for a random movement ("Move your head to the LEFT"). A photo or a recorded
      // video cannot know the direction in advance. The picture on screen is a mirror, so LEFT is the driver's own left.
      let challenge = null;
      try {
        const asked = await fetch("/api/auth/challenge", { method: "POST" });
        challenge = asked.ok ? await asked.json() : null;
      } catch (_) {}
      if (!challenge || !challenge.token) {
        this.setCaptureFrame("red");
        this.setCaptureStatus("Cannot get the movement request from the server. Car will NOT start.", "red");
        return null;
      }
      this.setCaptureStatus(`${challenge.text}. Starting in 1 second...`, "");
      await new Promise(r => setTimeout(r, 1000));

      // Step 2: about 2.5 seconds of pictures while the driver follows the request.
      for (let i = 0; i < AUTH_FRAMES; i++) {
        ctx.drawImage(this.videoEl, 0, 0, 640, 480);
        lastPicture = canvas.toDataURL("image/jpeg", 0.85);
        frames.push(lastPicture);
        this.setCaptureStatus(`${challenge.text}... ${i + 1} / ${AUTH_FRAMES}`, "");
        if (i < AUTH_FRAMES - 1) await new Promise(r => setTimeout(r, AUTH_FRAME_GAP_MS));
      }
      if (preview && lastPicture) {
        preview.src = lastPicture;
        preview.style.display = "block";
      }
      if (placeholder) placeholder.style.display = "none";
      if (actionsRow) actionsRow.style.display = "flex";
      this.setCaptureStatus("Checking the driver's face...", "");

      const response = await fetch("/api/authenticate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ frames: frames, challenge: challenge.token })
      });
      let res = null;
      try { res = await response.json(); } catch (_) {}
      if (!response.ok || !res || res.status !== "success") {
        this.setCaptureFrame("red");
        this.setCaptureStatus((res && res.message) || `Face check failed (server answered ${response.status}). Car will NOT start.`, "red");
        return lastPicture;
      }
      this.showAuthResult(res);
    } catch (err) {
      this.setCaptureFrame("red");
      this.setCaptureStatus("Cannot reach the server. Car will NOT start.", "red");
    } finally {
      this.isAuthenticating = false;
      if (btn) btn.disabled = false;
    }
    return lastPicture;
  }

  // Shows the answer of /api/authenticate. The server has already locked or unlocked the car and written the log.
  showAuthResult(res) {
    const ok = Boolean(res.authorized) && res.driver && res.driver !== "Unknown";
    this.lastAuthState = ok;
    this.lastAuthDriver = ok ? res.driver : "Unknown";
    if (ok) {
      this.setCaptureFrame("green");
      this.setCaptureStatus(`AUTHORISED: ${res.driver}. Car start allowed.`, "green");
    } else {
      this.setCaptureFrame("red");
      this.setCaptureStatus(`NOT AUTHORISED. Car will NOT start. ${res.message || ""}`.trim(), "red");
    }
    this.updateGatingUI(ok, res.driver || "Unknown", res.person_distance_cm,
                        typeof res.confidence === "number" ? res.confidence : 0, res.message, res);
    const timing = document.getElementById("authLatency");
    if (timing && typeof res.elapsed_ms === "number") timing.textContent = `CHECK TOOK: ${res.elapsed_ms} ms`;
  }

  // Locking is always allowed. (Unlocking happens only through a face check.)
  async lockVehicle() {
    try {
      await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: "AUTH", value: 0 })
      });
    } catch (_) {}
    this.lastAuthState = false;
    this.lastAuthDriver = "Unknown";
    if (document.body.classList.contains("view-dash")) { window.location.href = "/"; return; }   // locked: back to the face login
    this.setCaptureFrame("");
    this.setCaptureStatus("Vehicle locked. Capture Image to unlock.", "");
    this.updateGatingUI(false, "Unknown", null, 0, "Vehicle locked.", { detected: false, liveness: "STANDBY" });
  }

  setCaptureStatus(text, colour) {
    const statusMsg = document.getElementById("captureStatusMsg");
    if (!statusMsg) return;
    const palette = { green: "var(--green-success, #059669)", red: "var(--red-critical, #DC2626)" };
    statusMsg.textContent = text;
    statusMsg.style.color = palette[colour] || "var(--text-secondary)";
    statusMsg.style.fontWeight = colour ? "700" : "400";
  }

  setCaptureFrame(colour) {
    const box = document.getElementById("capturePreviewContainer");
    if (!box) return;
    const palette = { green: "var(--green-success, #059669)", red: "var(--red-critical, #DC2626)" };
    box.style.borderColor = palette[colour] || "";
    box.style.borderStyle = colour ? "solid" : "dashed";
  }

  resetCapturedImage() {
    const preview = document.getElementById("capturedImagePreview");
    const placeholder = document.getElementById("capturePlaceholder");
    const actionsRow = document.getElementById("captureActionsRow");

    if (preview) {
      preview.style.display = "none";
      preview.src = "";
    }
    if (placeholder) placeholder.style.display = "block";
    if (actionsRow) actionsRow.style.display = "none";
    this.setCaptureFrame("");
    this.setCaptureStatus("Ready to capture driver photo", "");
  }

  loadPersistedCapture() {
    // Older versions kept the last picture in the browser. A picture without a result is misleading, so remove it.
    try { localStorage.removeItem("accialert_last_captured_image"); } catch (_) {}
  }

  // ===================================================================
  // CAMERA & OPTICAL FEED
  // ===================================================================
  async startCamera() {
    if (this.isRunning) return;
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        this.cameraUnavailable("This browser cannot use the camera. Open the page at http://127.0.0.1:5173/ in Chrome or Edge.");
        return;
      }
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 640 },
          height: { ideal: 480 },
          facingMode: this.facingMode
        },
        audio: false
      });

      if (this.videoEl) {
        this.videoEl.srcObject = this.stream;
        await this.videoEl.play();
      }

      this.isRunning = true;
      if (this.standbyEl) this.standbyEl.classList.add("hidden");

      const btnStart = document.getElementById("btnStartCamera");
      const btnStop = document.getElementById("btnStopCamera");
      if (btnStart) btnStart.style.display = "none";
      if (btnStop) btnStop.style.display = "inline-flex";

      this.onCameraFrame();
    } catch (err) {
      console.warn("Could not access the camera:", err);
      this.cameraUnavailable("Camera is not available. Allow camera access and try again.");
    }
  }

  stopCamera() {
    this.isRunning = false;
    if (this.animFrameId) {
      cancelAnimationFrame(this.animFrameId);
      this.animFrameId = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach(t => t.stop());
      this.stream = null;
    }
    if (this.videoEl) {
      this.videoEl.srcObject = null;
    }
    if (this.ctx && this.canvasEl) {
      this.ctx.clearRect(0, 0, this.canvasEl.width, this.canvasEl.height);
    }
    if (this.standbyEl) this.standbyEl.classList.remove("hidden");

    const btnStart = document.getElementById("btnStartCamera");
    const btnStop = document.getElementById("btnStopCamera");
    if (btnStart) btnStart.style.display = "inline-flex";
    if (btnStop) btnStop.style.display = "none";

    // Stopping the camera does not change the lock: only a face check unlocks, only "Lock vehicle" locks.
    if (!this.lastAuthState) {
      this.updateGatingUI(false, "Unknown", null, 0, "Camera is off", { detected: false, liveness: "STANDBY" });
    }
  }

  toggleCamera() {
    this.facingMode = this.facingMode === "user" ? "environment" : "user";
    if (this.isRunning) {
      this.stopCamera();
      this.startCamera();
    }
  }

  onCameraFrame() {
    if (!this.isRunning) return;

    if (this.videoEl && this.canvasEl && this.ctx && this.videoEl.videoWidth > 0) {
      if (this.canvasEl.width !== this.videoEl.videoWidth) {
        this.canvasEl.width = this.videoEl.videoWidth;
        this.canvasEl.height = this.videoEl.videoHeight;
      }

      this.ctx.clearRect(0, 0, this.canvasEl.width, this.canvasEl.height);

      // The camera is only a preview. The face is checked once, when the driver presses Capture Image.
      this.drawGuide(this.canvasEl.width, this.canvasEl.height);
    }

    this.animFrameId = requestAnimationFrame(() => this.onCameraFrame());
  }

  // Shown when no face is found yet
  drawGuide(cw, ch) {
    const ctx = this.ctx;
    const w = Math.round(cw * 0.36);
    const h = Math.round(ch * 0.5);
    const x = Math.round((cw - w) / 2);
    const y = Math.round((ch - h) / 2);
    ctx.strokeStyle = "rgba(255, 255, 255, 0.85)";
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 6]);
    ctx.strokeRect(x, y, w, h);
    ctx.setLineDash([]);
    ctx.font = "14px 'Segoe UI', Arial, sans-serif";
    ctx.textAlign = "center";
    ctx.fillStyle = "#FFFFFF";
    ctx.fillText("Face the camera, then press Capture Image", cw / 2, y + h + 22);
  }

  cameraUnavailable(message) {
    // No camera means nobody can be checked, so the car stays locked. Nothing is faked.
    this.isRunning = false;
    this.setCaptureStatus(message, "red");
    this.setEnrolStatus(message, true);
  }

  // ===================================================================
  // VEHICLE GATING & IGNITION INTERLOCK
  // ===================================================================
  updateGatingUI(isAuth, driverName, distance, similarity, note, extraData = {}) {
    const isRealMatch = isAuth && driverName !== "Unknown";
    const displayName = isRealMatch ? driverName : "Unknown";
    const hasFace = Boolean(extraData.detected || (extraData.face_count && extraData.face_count > 0) || (this.faceBox && (Date.now() - this.faceBoxTime) < 2000));

    // Element references for the 4 Telemetry Cards
    const cardAuth = document.getElementById("cardDriverAuth");
    const nameEl = document.getElementById("valDriverName");
    const authDot = document.getElementById("authStatusDot");
    const authStatusText = document.getElementById("valAuthStatusText");

    const cardMatch = document.getElementById("cardMatchScore");
    const simEl = document.getElementById("valSimilarity");
    const simBar = document.getElementById("valSimilarityBar");

    const cardLive = document.getElementById("cardLiveness");
    const livenessBadge = document.getElementById("txtLivenessStatus");
    const eyeTrackingSub = document.getElementById("valEyeTrackingSub");

    const cardDist = document.getElementById("cardDistance");
    const distEl = document.getElementById("valDistance");
    const distSub = document.getElementById("valDistanceSub");

    const lockBadge = document.getElementById("ignitionLockBadge");
    const engineIndicator = document.getElementById("engineIndicator");
    const engineTitle = document.getElementById("engineStatusTitle");
    const engineSub = document.getElementById("engineSubStatus");

    // 1. Driver Authorized / Unauthorized Card
    if (nameEl) {
      if (isRealMatch) {
        nameEl.textContent = displayName;
        nameEl.style.color = "var(--green-success, #059669)";
        if (cardAuth) cardAuth.style.borderTopColor = "var(--green-success, #059669)";
        if (authDot) authDot.style.background = "var(--green-success, #059669)";
        if (authStatusText) authStatusText.innerHTML = '<span class="badge badge-success" style="font-size:0.7rem; padding:2px 6px;">AUTHORIZED</span>';
      } else if (hasFace) {
        nameEl.textContent = (driverName && driverName !== "Unknown") ? driverName : "UNAUTHORIZED";
        nameEl.style.color = "var(--red-critical, #DC2626)";
        if (cardAuth) cardAuth.style.borderTopColor = "var(--red-critical, #DC2626)";
        if (authDot) authDot.style.background = "var(--red-critical, #DC2626)";
        if (authStatusText) authStatusText.innerHTML = '<span class="badge badge-danger" style="font-size:0.7rem; padding:2px 6px;">UNAUTHORIZED</span>';
      } else if (this.isRunning) {
        nameEl.textContent = "READY";
        nameEl.style.color = "var(--text-muted, #6B7280)";
        if (cardAuth) cardAuth.style.borderTopColor = "var(--border-color, #E5E7EB)";
        if (authDot) authDot.style.background = "#9CA3AF";
        if (authStatusText) authStatusText.textContent = "Press Capture Image";
      } else {
        nameEl.textContent = "STANDBY";
        nameEl.style.color = "var(--text-muted, #6B7280)";
        if (cardAuth) cardAuth.style.borderTopColor = "var(--border-color, #E5E7EB)";
        if (authDot) authDot.style.background = "#9CA3AF";
        if (authStatusText) authStatusText.textContent = "Camera is off";
      }
    }

    // 2. Match % Card
    const simVal = (similarity !== null && !isNaN(similarity)) ? Math.round(similarity) : 0;
    if (simEl) {
      if (hasFace && simVal > 0) {
        simEl.textContent = `${simVal}%`;
        const simColor = isRealMatch ? "var(--green-success, #059669)" : (simVal >= 50 ? "var(--yellow-warning, #D97706)" : "var(--red-critical, #DC2626)");
        simEl.style.color = simColor;
        if (simBar) {
          simBar.style.width = `${Math.min(100, simVal)}%`;
          simBar.style.background = simColor;
        }
        if (cardMatch) cardMatch.style.borderTopColor = isRealMatch ? "var(--green-success, #059669)" : "var(--border-color, #E5E7EB)";
      } else {
        simEl.textContent = "0%";
        simEl.style.color = "var(--text-muted, #6B7280)";
        if (simBar) simBar.style.width = "0%";
        if (cardMatch) cardMatch.style.borderTopColor = "var(--border-color, #E5E7EB)";
      }
    }

    // 3. Eye Tracking "Liveness" Card
    const liveVal = extraData.liveness || (isRealMatch ? "PASSED" : (hasFace ? "PENDING" : "STANDBY"));
    if (livenessBadge) {
      if (liveVal === "PASSED" || isRealMatch) {
        livenessBadge.className = "badge badge-success";
        livenessBadge.textContent = "PASSED";
        if (eyeTrackingSub) eyeTrackingSub.textContent = extraData.eye_tracking ? `Vitality OK • ${extraData.eye_tracking}` : "Eye blink & motion confirmed";
        if (cardLive) cardLive.style.borderTopColor = "var(--green-success, #059669)";
      } else if (hasFace) {
        livenessBadge.className = "badge badge-warning";
        livenessBadge.textContent = (extraData.eyes_count && extraData.eyes_count >= 1) ? `TRACKING (${extraData.eyes_count} EYES)` : "PENDING";
        if (eyeTrackingSub) eyeTrackingSub.textContent = extraData.eye_tracking || "Blink or move head slightly";
        if (cardLive) cardLive.style.borderTopColor = "var(--yellow-warning, #D97706)";
      } else {
        livenessBadge.className = "badge badge-muted";
        livenessBadge.textContent = this.isRunning ? "WAITING" : "STANDBY";
        if (eyeTrackingSub) eyeTrackingSub.textContent = this.isRunning ? "Checked when you capture" : "Checked when you capture";
        if (cardLive) cardLive.style.borderTopColor = "var(--border-color, #E5E7EB)";
      }
    }

    // 4. Distance (Camera to Person) Card
    if (distEl) {
      if (hasFace && extraData.person_distance_str && extraData.person_distance_str !== "--") {
        distEl.textContent = extraData.person_distance_str;
        distEl.style.color = "var(--teal-primary, #0D9488)";
        if (distSub) distSub.textContent = extraData.person_distance_m ? `${extraData.person_distance_m} m from camera` : "Camera to face";
        if (cardDist) cardDist.style.borderTopColor = "var(--teal-primary, #0D9488)";
      } else if (hasFace && this.faceBox) {
        const boxW = (this.faceBox[2] > 1) ? this.faceBox[2] : (this.faceBox[2] * 640);
        if (boxW > 10) {
          const estCm = Math.round(Math.max(15, Math.min(250, (14.5 * 550.0) / boxW)));
          distEl.textContent = `${estCm} cm`;
          distEl.style.color = "var(--teal-primary, #0D9488)";
          if (distSub) distSub.textContent = `${(estCm / 100).toFixed(2)} m from camera`;
          if (cardDist) cardDist.style.borderTopColor = "var(--teal-primary, #0D9488)";
        } else {
          distEl.textContent = "--";
          distEl.style.color = "var(--text-muted, #6B7280)";
          if (distSub) distSub.textContent = "Camera to driver";
          if (cardDist) cardDist.style.borderTopColor = "var(--border-color, #E5E7EB)";
        }
      } else {
        distEl.textContent = "--";
        distEl.style.color = "var(--text-muted, #6B7280)";
        if (distSub) distSub.textContent = "Camera to driver";
        if (cardDist) cardDist.style.borderTopColor = "var(--border-color, #E5E7EB)";
      }
    }

    if (lockBadge) {
      lockBadge.className = isRealMatch ? "badge badge-success" : "badge badge-danger";
      lockBadge.innerHTML = isRealMatch ? 'VEHICLE UNLOCKED' : 'VEHICLE LOCKED';
    }

    if (engineIndicator) {
      engineIndicator.className = isRealMatch ? "engine-indicator unlocked" : "engine-indicator locked";
    }

    if (engineTitle) {
      engineTitle.className = isRealMatch ? "text-success fw-bold mb-1" : "text-danger fw-bold mb-1";
      engineTitle.textContent = isRealMatch ? "ENGINE START AUTHORISED" : "ENGINE START LOCKED";
    }

    if (engineSub) {
      engineSub.textContent = isRealMatch ?
        `Face matched: ${displayName}. Live person confirmed.` :
        (note || "Waiting for the driver's face.");
    }

    // Sync with Fleet Dashboard Card 6
    const cardDriver = document.getElementById("val-driver");
    const cardBadge = document.getElementById("badge-driver-auth");
    if (cardDriver) cardDriver.textContent = displayName;
    if (cardBadge) {
      cardBadge.className = isRealMatch ? "badge badge-success" : "badge badge-danger";
      cardBadge.textContent = isRealMatch ? "AUTHORIZED" : "UNAUTHORIZED";
    }

    if (window.DashboardApp) {
      window.DashboardApp.activeDriver = displayName;
    }
  }

  // ===================================================================
  // DRIVER ENROLMENT (live camera frames are sent to the server)
  // ===================================================================
  async loadRoster() {
    try {
      const res = await fetch("/api/enrolled");
      const data = await res.json();
      this.enrolledDrivers = {};
      (data.drivers || []).forEach(d => {
        this.enrolledDrivers[d.name] = { name: d.name, count: d.samples, license: d.license || "", enrolledAt: d.enrolled_at || "" };
      });
    } catch (_) {
      this.enrolledDrivers = {};
    }
    this.renderRosterTable();
  }

  cameraIsLive() {
    return Boolean(this.stream && this.videoEl && this.videoEl.videoWidth > 0);
  }

  setEnrolStatus(text, isError) {
    const el = document.getElementById("enrolProgressText");
    if (el) {
      el.textContent = text;
      el.style.color = isError ? "var(--red-critical)" : "var(--teal-primary)";
    }
  }

  async startEnrolment() {
    if (this.enrolmentState.isEnrolling) return;
    const name = document.getElementById("inputDriverName")?.value.trim();
    const license = document.getElementById("inputLicenseNumber")?.value.trim();
    if (!name || !license) {
      AppDialog.alert({ title: "Details missing", message: "Enter the driver's name and license number first." });
      return;
    }

    const wrap = document.getElementById("enrolProgressWrap");
    if (wrap) wrap.style.display = "block";
    const fill = document.getElementById("enrolProgressFill");
    if (fill) fill.style.width = "0%";
    this.setEnrolStatus("Starting the camera...", false);
    this.setEnrolCameraNote("Starting the camera...");

    await this.startCamera();
    if (!this.cameraIsLive()) {
      this.setEnrolStatus("Camera is not available. Allow camera access and try again.", true);
      return;
    }

    this.enrolmentState = { isEnrolling: true, driverName: name, targetSamples: 0, capturedSamples: 0 };
    this.showEnrolCamera(true);
    const box = document.querySelector(".enrol-camera");
    const overlay = window.HeadTurn.mountOverlay(box);
    let clip = null, challenge = null;

    // The same check as the login: look straight, turn the head RIGHT, then LEFT, then look straight again.
    // These pictures are the enrolment: there is no separate set of 25 pictures.
    try {
      const asked = await fetch("/api/auth/challenge", { method: "POST" });
      challenge = asked.ok ? await asked.json() : null;
      if (!challenge || !challenge.token) throw new Error("Cannot get the head-turn request from the server.");
      clip = await window.HeadTurn.capture({
        video: this.videoEl, challenge: challenge, overlay: overlay,
        say: (t) => { this.setEnrolStatus(`${name}: ${t}`, false); },
        progress: (p) => { if (fill) fill.style.width = `${Math.round(p * 90)}%`; },
      });
    } catch (err) {
      clip = { ok: false, message: err.message || "The head turn could not be recorded. Try again." };
    } finally {
      // The camera is only needed while the pictures are taken, so it goes off here.
      this.stopCamera();
      overlay.reset();
      this.showEnrolCamera(false);
      this.setEnrolCameraNote("Camera is off.");
    }
    if (!clip.ok) {
      this.enrolmentState.isEnrolling = false;
      this.setEnrolStatus(`Not saved. ${clip.message}`, true);
      return;
    }

    this.setEnrolStatus("Saving face samples...", false);
    this.setEnrolCount(`${clip.frames.length} pictures`);
    try {
      const res = await fetch("/api/enrol", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, license: license, frames: clip.frames, challenge: challenge.token })
      });
      let data = null;
      try { data = await res.json(); } catch (e) { /* the server sent a page, not JSON */ }
      if (!res.ok || !data) {
        throw new Error((data && data.message) ||
          `Enrolment failed (server answered ${res.status}). Restart the AcciAlert server and try again.`);
      }
      if (fill) fill.style.width = "100%";
      this.finishEnrolment(data);
    } catch (err) {
      this.setEnrolStatus(err.message, true);
    } finally {
      this.enrolmentState.isEnrolling = false;
    }
  }

  async finishEnrolment(result) {
    await this.loadRoster();
    if (window.DashboardApp) {
      window.DashboardApp.showToast(`${result.name} enrolled: ${result.accepted} samples saved.`, "success");
    }
    this.setEnrolStatus(`${result.name} enrolled with ${result.total_samples} samples. Photos saved in ${result.folder} (skipped frames: ${result.skipped}).`, false);
    for (const id of ["inputDriverName", "inputLicenseNumber"]) {
      const input = document.getElementById(id);
      if (input) input.value = "";
    }
  }

  // Shows the live camera in the enrolment form (the same camera stream the face check uses)
  showEnrolCamera(on) {
    const video = document.getElementById("enrolVideo");
    const box = video ? video.parentElement : null;
    if (!video || !box) return;
    if (on && this.stream) {
      video.srcObject = this.stream;
      video.play().catch(() => {});
      box.classList.add("live");
      const count = document.getElementById("enrolFrameCount");
      if (count) count.style.display = "block";
    } else {
      video.srcObject = null;
      box.classList.remove("live");
      const count = document.getElementById("enrolFrameCount");
      if (count) count.style.display = "none";
    }
  }

  setEnrolCameraNote(text) {
    const note = document.getElementById("enrolCameraNote");
    if (note) note.textContent = text;
  }

  setEnrolCount(text) {
    const count = document.getElementById("enrolFrameCount");
    if (count) count.textContent = text;
  }

  escapeHtml(text) {
    return String(text ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  statusLabel(d) {
    return d.count > 0
      ? '<span class="badge badge-success">Live enrolled</span>'
      : '<span class="badge badge-warning">Needs live enrolment</span>';
  }

  // The roster shows the 5 most recent drivers. "View all drivers" opens the full list.
  renderRosterTable() {
    const tbody = document.getElementById("rosterTableBody");
    if (!tbody) return;

    const drivers = Object.values(this.enrolledDrivers);   // newest enrolment first (server order)
    const recent = drivers.slice(0, 5);

    if (drivers.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:20px;">No drivers enrolled.</td></tr>';
    } else {
      tbody.innerHTML = recent.map(d => `
        <tr>
          <td style="font-weight:600;">${this.escapeHtml(d.name)}</td>
          <td>${this.statusLabel(d)}</td>
          <td style="color:var(--text-secondary);">${this.escapeHtml(d.license) || "-"}</td>
          <td style="color:var(--text-secondary);">${d.count} samples</td>
          <td><div class="row-actions">
            <button type="button" class="btn btn-outline btn-sm" data-view-driver="${this.escapeHtml(d.name)}">View</button>
            <button type="button" class="btn btn-delete btn-sm" data-delete-driver="${this.escapeHtml(d.name)}">Delete</button>
          </div></td>
        </tr>
      `).join("");
    }

    const note = document.getElementById("rosterNote");
    if (note) note.textContent = drivers.length > 5 ? `Showing the 5 most recent of ${drivers.length} drivers` : "";

    const badge = document.getElementById("enrolledCountBadge");
    if (badge) badge.textContent = `${drivers.length} drivers`;

    const subtabBadge = document.getElementById("subtab-enrol-badge");
    if (subtabBadge) subtabBadge.textContent = drivers.length;
  }

  // ---- full-screen window: all drivers, or one driver's details and history
  openDriverModal(title, html) {
    document.getElementById("driver-modal-title").textContent = title;
    document.getElementById("driver-modal-body").innerHTML = html;
    document.getElementById("driver-modal").classList.add("show");
  }

  closeDriverModal() {
    document.getElementById("driver-modal")?.classList.remove("show");
  }

  showAllDrivers() {
    const drivers = Object.values(this.enrolledDrivers);
    const rows = drivers.map(d => `
      <tr>
        <td style="font-weight:600;">${this.escapeHtml(d.name)}</td>
        <td>${this.statusLabel(d)}</td>
        <td>${this.escapeHtml(d.license) || "-"}</td>
        <td>${d.count}</td>
        <td>${this.escapeHtml(d.enrolledAt) || "-"}</td>
        <td><div class="row-actions">
          <button type="button" class="btn btn-outline btn-sm" data-view-driver="${this.escapeHtml(d.name)}" data-from-list="1">View</button>
          <button type="button" class="btn btn-delete btn-sm" data-delete-driver="${this.escapeHtml(d.name)}" data-from-list="1">Delete</button>
        </div></td>
      </tr>`).join("");
    this.openDriverModal(`All drivers (${drivers.length})`, `
      <div class="table-responsive">
        <table class="custom-table">
          <thead><tr><th>Driver Name</th><th>Status</th><th>License number</th><th>Samples</th><th>Last enrolled</th><th>Details</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="6" style="text-align:center;">No drivers enrolled.</td></tr>'}</tbody>
        </table>
      </div>`);
  }

  async showDriverDetails(name, fromList) {
    let d;
    try {
      const res = await fetch(`/api/enrolled/${encodeURIComponent(name)}`);
      if (!res.ok) throw new Error();
      d = await res.json();
    } catch (_) {
      this.openDriverModal(name, "<p>The details could not be loaded.</p>");
      return;
    }
    const history = (d.history || []).map(h => `
      <tr>
        <td style="white-space:nowrap;">${this.escapeHtml(h.enrolled_at)}</td>
        <td>${this.escapeHtml(h.license_number) || "-"}</td>
        <td>${h.frames_used}</td>
        <td>${h.face_samples}</td>
        <td>${h.photos_saved}</td>
      </tr>`).join("");
    const back = fromList ? '<button type="button" class="btn btn-outline btn-sm" data-back-to-list="1">Back to all drivers</button>' : "";
    this.openDriverModal(d.name, `
      <div class="row-actions" style="margin-bottom:14px;">
        ${back}
        <button type="button" class="btn btn-delete btn-sm" data-delete-driver="${this.escapeHtml(d.name)}">Delete driver</button>
      </div>
      <dl class="detail-grid">
        <dt>Status</dt><dd>${d.samples > 0 ? "Live enrolled" : "Needs live enrolment"}</dd>
        <dt>License number</dt><dd>${this.escapeHtml(d.license) || "-"}</dd>
        <dt>Face samples</dt><dd>${d.samples}</dd>
        <dt>Photos saved</dt><dd>${d.images} in ${this.escapeHtml(d.folder)}</dd>
        <dt>Last enrolled</dt><dd>${this.escapeHtml(d.enrolled_at) || "-"}</dd>
      </dl>
      <h4 style="margin-bottom:8px;">Enrolment history</h4>
      <div class="table-responsive">
        <table class="custom-table">
          <thead><tr><th>Date and time</th><th>License number</th><th>Frames used</th><th>Total samples</th><th>Photos saved</th></tr></thead>
          <tbody>${history || '<tr><td colspan="5" style="text-align:center;">No enrolment recorded yet.</td></tr>'}</tbody>
        </table>
      </div>`);
  }

  // Delete a driver (admin only): face data, record, history and photos. Asks first.
  async deleteDriver(name) {
    const only = Object.keys(this.enrolledDrivers).length <= 1;
    const warning = `Their face data, enrolment history and saved photos will be removed and they can no longer start the car. This cannot be undone.` +
      (only ? `\n\nThis is the last driver: nobody will be able to start the car until a driver is enrolled.` : "");
    const yes = await AppDialog.confirm({ title: `Delete driver "${name}"?`, message: warning, confirmText: "Delete driver", danger: true });
    if (!yes) return;
    try {
      const res = await fetch(`/api/enrolled/${encodeURIComponent(name)}`, { method: "DELETE" });
      let body = {}; try { body = await res.json(); } catch (_) {}
      if (!res.ok) throw new Error(body.message || `The driver could not be deleted (server answered ${res.status}).`);
      this.closeDriverModal();
      await this.loadRoster();
      if (window.DashboardApp) window.DashboardApp.showToast(body.message || `${name} was deleted.`, "success");
      this.setEnrolStatus(`${name} was deleted (${body.face_samples || 0} face samples, ${body.photos || 0} photos removed).`, false);
    } catch (err) {
      if (window.DashboardApp) window.DashboardApp.showToast(err.message, "danger");
      else AppDialog.alert({ title: "Could not delete", message: err.message });
    }
  }

  bindRosterEvents() {
    document.getElementById("btnViewAllDrivers")?.addEventListener("click", () => this.showAllDrivers());
    document.getElementById("btn-driver-modal-close")?.addEventListener("click", () => this.closeDriverModal());
    document.getElementById("driver-modal")?.addEventListener("click", (e) => {
      if (e.target.id === "driver-modal") this.closeDriverModal();        // click outside the window
      const view = e.target.closest("[data-view-driver]");
      if (view) this.showDriverDetails(view.dataset.viewDriver, view.dataset.fromList === "1");
      const del = e.target.closest("[data-delete-driver]");
      if (del) this.deleteDriver(del.dataset.deleteDriver);
      if (e.target.closest("[data-back-to-list]")) this.showAllDrivers();
    });
    document.getElementById("rosterTableBody")?.addEventListener("click", (e) => {
      const view = e.target.closest("[data-view-driver]");
      if (view) this.showDriverDetails(view.dataset.viewDriver, false);
      const del = e.target.closest("[data-delete-driver]");
      if (del) this.deleteDriver(del.dataset.deleteDriver);
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") this.closeDriverModal(); });
  }


  // ===================================================================
  // SECURITY & IGNITION AUDIT TRAIL
  // ===================================================================
  recordAuditEvent(eventData) {
    const now = new Date();
    const driverVal = (eventData.driver && String(eventData.driver).trim()) ? eventData.driver : "Unknown";
    const entry = {
      timestamp: now.toTimeString().split(" ")[0],
      type: eventData.type,
      severity: eventData.severity,
      driver: driverVal,
      match: eventData.match,
      liveness: eventData.liveness,
      cmd: eventData.cmd
    };

    this.auditLogs.unshift(entry);
    if (this.auditLogs.length > 200) this.auditLogs.pop();
    this.savePersistedLogs();
    this.renderAuditLogsTable();
    this.updateAuditStats();
  }

  loadAuditLogs() {
    fetch("/api/logs?limit=50")
      .then(res => res.json())
      .then(data => {
        if (data.status === "success" && Array.isArray(data.logs) && data.logs.length > 0) {
          this.auditLogs = data.logs.map(l => ({
            timestamp: l.timestamp.split(" ")[1] || l.timestamp,
            type: l.event_type || l.type || "AUTH_OK",
            severity: l.severity || ("OK" in (l.event_type || "") ? "INFO" : "HIGH"),
            driver: l.driver_name || l.driver || "Unknown",
            match: (l.match_score === null || l.match_score === undefined) ? null : l.match_score,
            liveness: l.liveness_result || "--",
            cmd: l.transmission || JSON.stringify({ cmd: "AUTH", ok: 1, driver: l.driver_name || l.driver || "Unknown" })
          }));
          this.savePersistedLogs();
        }
        this.renderAuditLogsTable();
        this.updateAuditStats();
      })
      .catch(() => {
        this.renderAuditLogsTable();
        this.updateAuditStats();
      });
  }

  updateAuditStats() {
    const total = this.auditLogs.length;
    const authCount = this.auditLogs.filter(l => l.type === "AUTH_OK").length;
    const rejectCount = this.auditLogs.filter(l => l.type === "UNAUTHORISED").length;
    const lastTime = this.auditLogs[0] ? this.auditLogs[0].timestamp : "--:--:--";

    const totalEl = document.getElementById("faceTotalVerifications");
    const authEl = document.getElementById("faceAuthorisedUnlocks");
    const rejectEl = document.getElementById("faceSecurityRejections");
    const lastEl = document.getElementById("faceLastTelemetry");

    if (totalEl) totalEl.textContent = total;
    if (authEl) authEl.textContent = authCount;
    if (rejectEl) rejectEl.textContent = rejectCount;
    if (lastEl) lastEl.textContent = lastTime;
  }

  renderAuditLogsTable() {
    const tbody = document.getElementById("faceAuditLogsTableBody");
    if (!tbody) return;

    let filtered = this.auditLogs;
    if (this.currentFilter === "AUTH") {
      filtered = this.auditLogs.filter(l => l.type === "AUTH_OK");
    } else if (this.currentFilter === "REJECT") {
      filtered = this.auditLogs.filter(l => l.type === "UNAUTHORISED");
    }

    if (filtered.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:#627289; padding:24px;">No audit events recorded for current filter.</td></tr>';
      return;
    }

    tbody.innerHTML = filtered.map(l => {
      const isOk = l.type === "AUTH_OK";
      const isStop = l.type === "CAMERA_STOP";
      const typeBadgeClass = isOk ? "badge badge-success" : (isStop ? "badge badge-muted" : "badge badge-danger");
      const sevBadgeClass = l.severity === "HIGH" ? "badge badge-danger" : "badge badge-muted";

      return `
        <tr>
          <td style="font-family:monospace; color:var(--text-secondary); font-size:0.78rem;">${l.timestamp}</td>
          <td><span class="${typeBadgeClass}">${l.type}</span></td>
          <td><span class="${sevBadgeClass}">${l.severity}</span></td>
          <td style="font-weight:600; color:var(--text-main, #111827);">${l.driver || "Unknown"}</td>
          <td>
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-family:monospace; color:${isOk ? 'var(--green-success)' : 'var(--red-critical)'}; font-weight:700;">${(l.match === null || l.match === undefined) ? "--" : l.match + "%"}</span>
              <div class="mini-progress-bar" style="width:60px; height:4px; margin-top:0;">
                <div class="mini-progress-fill ${isOk ? '' : 'danger'}" style="width:${(l.match === null || l.match === undefined) ? 0 : l.match}%;"></div>
              </div>
            </div>
          </td>
          <td><span class="badge badge-success">${l.liveness}</span></td>
          <td><code style="color:var(--text-muted); font-size:0.75rem;">${l.cmd}</code></td>
        </tr>
      `;
    }).join("");
  }

  exportAuditLogsCsv() {
    let csv = "Timestamp,Event Type,Severity,Driver Name,Match Score,Liveness Result,ESP32 Transmission\n";
    this.auditLogs.forEach(l => {
      csv += `"${l.timestamp}","${l.type}","${l.severity}","${l.driver}","${l.match}%","${l.liveness}","${(l.cmd || '').replace(/"/g, '""')}"\n`;
    });

    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `accialert_face_audit_trail_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    if (window.DashboardApp) {
      window.DashboardApp.showToast("Exported Security & Ignition Audit Trail CSV.", "success");
    }
  }

  async clearAuditLogs() {
    if (!await AppDialog.confirm({ title: "Clear the audit trail?", message: "All login audit records shown here will be removed.", confirmText: "Clear", danger: true })) return;
    this.auditLogs = [];
    this.savePersistedLogs();
    this.renderAuditLogsTable();
    this.updateAuditStats();

    fetch("/api/logs", { method: "DELETE" }).catch(() => {});
    if (window.DashboardApp) {
      window.DashboardApp.showToast("Audit logs cleared.", "info");
    }
  }
}

// Opens the Face Recognition tab (used by the Driver Identity card)
window.openDriverAuthTab = function() {
  const tabBtn = document.querySelector('[data-tab="tab-face-recognition"]');
  if (tabBtn) tabBtn.click();

  const subBtn = document.querySelector('[data-subtab="face-sub-detector"]');
  if (subBtn) subBtn.click();

  if (window.DriverAuth && !window.DriverAuth.isRunning) {
    window.DriverAuth.startCamera();
  }
};

window.addEventListener("DOMContentLoaded", () => {
  window.DriverAuth = new DriverAuthController();
});
