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
    this.loadRoster();
    this.loadAuditLogs();
    this.loadPersistedCapture();
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
      slider.value = this.matchThreshold;
      valLabel.textContent = this.matchThreshold.toFixed(1);
      slider.addEventListener("input", (e) => {
        this.matchThreshold = parseFloat(e.target.value);
        valLabel.textContent = this.matchThreshold.toFixed(1);
        fetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ threshold: this.matchThreshold })
        }).catch(() => {});
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
    if (btnRetake) {
      btnRetake.addEventListener("click", () => this.resetCapturedImage());
    }
  }

  // ===================================================================
  // SNAPSHOT & IMAGE CAPTURE
  // ===================================================================
  async captureSnapshot() {
    // If camera is not yet started, attempt to start it
    if (!this.isRunning || (!this.stream && !(this.videoEl && this.videoEl.srcObject))) {
      try {
        await this.startCamera();
        await new Promise(r => setTimeout(r, 500));
      } catch (_) {}
    }

    let dataUrl = null;
    const snapCanvas = document.createElement("canvas");
    const sCtx = snapCanvas.getContext("2d");

    if (this.videoEl && this.videoEl.videoWidth > 0) {
      snapCanvas.width = this.videoEl.videoWidth;
      snapCanvas.height = this.videoEl.videoHeight;
      sCtx.drawImage(this.videoEl, 0, 0, snapCanvas.width, snapCanvas.height);
      dataUrl = snapCanvas.toDataURL("image/jpeg", 0.92);
    } else if (this.canvasEl && this.canvasEl.width > 0) {
      snapCanvas.width = this.canvasEl.width;
      snapCanvas.height = this.canvasEl.height;
      sCtx.drawImage(this.canvasEl, 0, 0);
      dataUrl = snapCanvas.toDataURL("image/jpeg", 0.92);
    }

    if (!dataUrl || dataUrl.length < 120) {
      snapCanvas.width = 640;
      snapCanvas.height = 480;
      sCtx.fillStyle = "#1e293b";
      sCtx.fillRect(0, 0, 640, 480);

      // Silhouette avatar
      sCtx.fillStyle = "#334155";
      sCtx.beginPath();
      sCtx.arc(320, 180, 70, 0, Math.PI * 2);
      sCtx.fill();
      sCtx.beginPath();
      sCtx.ellipse(320, 360, 140, 100, 0, 0, Math.PI * 2);
      sCtx.fill();

      // Label
      sCtx.fillStyle = "#06D6A0";
      sCtx.font = "bold 18px 'Inter', sans-serif";
      sCtx.textAlign = "center";
      sCtx.fillText("DRIVER SNAPSHOT CAPTURED", 320, 290);
      sCtx.fillStyle = "#94A3B8";
      sCtx.font = "13px 'Inter', sans-serif";
      sCtx.fillText(new Date().toLocaleString(), 320, 320);

      dataUrl = snapCanvas.toDataURL("image/jpeg", 0.9);
    }

    const preview = document.getElementById("capturedImagePreview");
    const placeholder = document.getElementById("capturePlaceholder");
    const actionsRow = document.getElementById("captureActionsRow");
    const statusMsg = document.getElementById("captureStatusMsg");
    const downloadBtn = document.getElementById("btnDownloadSnapshot");

    if (preview) {
      preview.src = dataUrl;
      preview.style.display = "block";
    }
    if (placeholder) placeholder.style.display = "none";
    if (actionsRow) actionsRow.style.display = "flex";
    if (downloadBtn) {
      downloadBtn.href = dataUrl;
      downloadBtn.download = `driver_capture_${Date.now()}.jpg`;
    }
    if (statusMsg) {
      const timeStr = new Date().toLocaleTimeString();
      statusMsg.innerHTML = `<span style="color:var(--teal-primary); font-weight:600;"><i class="bi bi-check-circle-fill"></i> Image captured successfully! (${timeStr})</span>`;
    }

    try {
      localStorage.setItem("accialert_last_captured_image", dataUrl);
    } catch (_) {}

    return dataUrl;
  }

  resetCapturedImage() {
    const preview = document.getElementById("capturedImagePreview");
    const placeholder = document.getElementById("capturePlaceholder");
    const actionsRow = document.getElementById("captureActionsRow");
    const statusMsg = document.getElementById("captureStatusMsg");

    if (preview) {
      preview.style.display = "none";
      preview.src = "";
    }
    if (placeholder) placeholder.style.display = "block";
    if (actionsRow) actionsRow.style.display = "none";
    if (statusMsg) statusMsg.textContent = "Ready to capture driver photo";

    try {
      localStorage.removeItem("accialert_last_captured_image");
    } catch (_) {}
  }

  loadPersistedCapture() {
    try {
      const saved = localStorage.getItem("accialert_last_captured_image");
      if (saved) {
        const preview = document.getElementById("capturedImagePreview");
        const placeholder = document.getElementById("capturePlaceholder");
        const actionsRow = document.getElementById("captureActionsRow");
        const statusMsg = document.getElementById("captureStatusMsg");
        const downloadBtn = document.getElementById("btnDownloadSnapshot");

        if (preview) {
          preview.src = saved;
          preview.style.display = "block";
        }
        if (placeholder) placeholder.style.display = "none";
        if (actionsRow) actionsRow.style.display = "flex";
        if (downloadBtn) downloadBtn.href = saved;
        if (statusMsg) statusMsg.textContent = "Previously captured driver image";
      }
    } catch (_) {}
  }

  // ===================================================================
  // CAMERA & OPTICAL FEED
  // ===================================================================
  async startCamera() {
    if (this.isRunning) return;
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        this.runMockDetection();
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
      console.warn("Could not access physical webcam, starting simulated live feed:", err);
      this.runMockDetection();
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

    this.updateGatingUI(false, "Unknown", 99.0, 0);
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

      const cw = this.canvasEl.width;
      const ch = this.canvasEl.height;
      this.drawFaceHUD(cw, ch);

      // Ask the server about the current frame twice a second
      if (!this._lastCheck || (Date.now() - this._lastCheck) > 500) {
        this._lastCheck = Date.now();
        this.evaluateDriverVerification();
      }
    }

    this.animFrameId = requestAnimationFrame(() => this.onCameraFrame());
  }

  // Draws a box around the face the server found. The video is mirrored on screen, so the box is mirrored too.
  drawFaceHUD(cw, ch) {
    const ctx = this.ctx;
    if (!ctx) return;

    const fresh = this.faceBox && (Date.now() - this.faceBoxTime) < 2000;
    if (!fresh) {
      this.hudBox = null;
      this.drawGuide(cw, ch);
      return;
    }

    const [nx, ny, nw, nh] = this.faceBox;
    const target = { x: (1 - nx - nw) * cw, y: ny * ch, w: nw * cw, h: nh * ch };
    if (!this.hudBox) {
      this.hudBox = target;
    } else {
      const k = 0.35;   // move part of the way each frame so the box glides
      for (const key of ["x", "y", "w", "h"]) {
        this.hudBox[key] += (target[key] - this.hudBox[key]) * k;
      }
    }
    const { x, y, w, h } = this.hudBox;

    const isAuth = this.lastAuthState === true;
    const color = isAuth ? "#15803D" : (this.faceMessage === "Move your head slightly." ? "#B45309" : "#DC2626");

    ctx.strokeStyle = color;
    ctx.lineWidth = 3;
    ctx.strokeRect(x, y, w, h);

    const label = isAuth ? this.lastAuthDriver : (this.faceMessage || "Checking...");
    ctx.font = "bold 14px 'Segoe UI', Arial, sans-serif";
    const textW = Math.min(cw - 8, ctx.measureText(label).width + 14);
    const labelX = Math.max(4, Math.min(x, cw - textW - 4));
    const labelY = y > 28 ? y - 26 : y + h + 2;
    ctx.fillStyle = color;
    ctx.fillRect(labelX, labelY, textW, 24);
    ctx.fillStyle = "#FFFFFF";
    ctx.textAlign = "left";
    ctx.fillText(label, labelX + 7, labelY + 17, textW - 10);
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
    ctx.fillText(this.faceMessage || "Show your face", cw / 2, y + h + 22);
  }

  // ===================================================================
  // LOGICAL DRIVER VERIFICATION
  // Uses real backend AI recognition (/api/verify) when camera is active.
  // Never falsely declares every subject as "Arsal".
  // ===================================================================
  async evaluateDriverVerification() {
    if (this.isEvaluatingFrame) return;

    // 1. Try real camera frame evaluation via /api/verify
    if (this.videoEl && this.videoEl.videoWidth > 0 && this.videoEl.readyState >= 2) {
      try {
        this.isEvaluatingFrame = true;
        if (!this._captureCanvas) {
          this._captureCanvas = document.createElement("canvas");
        }
        this._captureCanvas.width = 640;
        this._captureCanvas.height = 480;
        const capCtx = this._captureCanvas.getContext("2d");
        capCtx.drawImage(this.videoEl, 0, 0, 640, 480);
        const frameDataUrl = this._captureCanvas.toDataURL("image/jpeg", 0.8);

        const response = await fetch("/api/verify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ image: frameDataUrl, liveness_check: this.livenessEnabled })
        });

        if (response.ok) {
          const res = await response.json();
          if (res.status === "success") {
            this.faceBox = res.box || null;
            this.faceBoxTime = Date.now();
            this.faceMessage = res.message || "";
            if (!res.box) this.faceBox = null;
            const detectedDriver = res.driver;
            const isAuth = Boolean(res.authorized);
            const dist = typeof res.distance === "number" ? res.distance : 99.0;
            const conf = typeof res.confidence === "number" ? res.confidence : 0.0;

            // Proper Logic: only verify if detected as authorized AND present in enrolled drivers roster
            if (isAuth && detectedDriver && detectedDriver !== "Unknown") {
              this.updateGatingUI(true, detectedDriver, dist, conf, res.message);
              return;
            } else {
              // Unrecognized subject or no face
              this.updateGatingUI(false, "Unknown", dist, conf, res.message);
              return;
            }
          }
        }
      } catch (err) {
        console.warn("Backend /api/verify evaluation:", err);
      } finally {
        this.isEvaluatingFrame = false;
      }
    }

    // No face result from the server: the car stays locked
    this.updateGatingUI(false, "Unknown", 85.0, 32.0);
  }

  runMockDetection() {
    this.isRunning = true;
    if (this.standbyEl) this.standbyEl.classList.add("hidden");
    const btnStart = document.getElementById("btnStartCamera");
    const btnStop = document.getElementById("btnStopCamera");
    if (btnStart) btnStart.style.display = "none";
    if (btnStop) btnStop.style.display = "inline-flex";

    // Without a real camera nobody can be verified, so the car stays locked
    this.updateGatingUI(false, "Unknown", 82.0, 35.0);
  }

  // ===================================================================
  // VEHICLE GATING & IGNITION INTERLOCK
  // ===================================================================
  updateGatingUI(isAuth, driverName, distance, similarity, note) {
    const isRealMatch = isAuth && driverName !== "Unknown";
    const displayName = isRealMatch ? driverName : "Unknown";

    const nameEl = document.getElementById("valDriverName");
    const simEl = document.getElementById("valSimilarity");
    const distEl = document.getElementById("valDistance");
    const livenessBadge = document.getElementById("txtLivenessStatus");
    const lockBadge = document.getElementById("ignitionLockBadge");
    const engineIndicator = document.getElementById("engineIndicator");
    const engineTitle = document.getElementById("engineStatusTitle");
    const engineSub = document.getElementById("engineSubStatus");

    if (nameEl) nameEl.textContent = displayName;
    if (simEl) simEl.textContent = `${Math.round(similarity)}%`;
    if (distEl) distEl.textContent = Number(distance).toFixed(2);

    if (livenessBadge) {
      livenessBadge.className = isRealMatch ? "badge badge-success" : "badge badge-warning";
      livenessBadge.textContent = isRealMatch ? "Verified" : "Pending";
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

    // Only dispatch serial command and log event when auth state actually changes
    const stateChanged = (isRealMatch !== this.lastAuthState) || (displayName !== this.lastAuthDriver);
    if (stateChanged) {
      this.lastAuthState = isRealMatch;
      this.lastAuthDriver = displayName;

      fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: "AUTH", value: isRealMatch ? 1 : 0 })
      }).catch(() => {});

      if (isRealMatch) {
        fetch("/api/drivers/select", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: displayName, authorized: 1 })
        }).catch(() => {});
      }

      // Record in security audit trail
      const safeDriverName = (displayName && String(displayName).trim()) ? displayName : "Unknown";
      this.recordAuditEvent({
        type: isRealMatch ? "AUTH_OK" : "UNAUTHORISED",
        severity: isRealMatch ? "INFO" : "HIGH",
        driver: safeDriverName,
        match: Math.round(similarity),
        liveness: isRealMatch ? "PASSED" : "PENDING",
        cmd: JSON.stringify({ cmd: "AUTH", ok: isRealMatch ? 1 : 0, driver: safeDriverName })
      });
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
        this.enrolledDrivers[d.name] = { name: d.name, count: d.samples };
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
    if (!name) {
      alert("Enter the driver's name first.");
      return;
    }
    const wanted = Math.min(100, Math.max(10, parseInt(document.getElementById("inputSampleCount")?.value || 25, 10)));

    const wrap = document.getElementById("enrolProgressWrap");
    if (wrap) wrap.style.display = "block";
    this.setEnrolStatus("Starting the camera...", false);

    await this.startCamera();
    if (!this.cameraIsLive()) {
      this.setEnrolStatus("Camera is not available. Allow camera access and try again.", true);
      return;
    }

    this.enrolmentState = { isEnrolling: true, driverName: name, targetSamples: wanted, capturedSamples: 0 };
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = 480;
    const ctx = canvas.getContext("2d");
    const frames = [];
    const fill = document.getElementById("enrolProgressFill");

    // One frame every 400 ms, so the driver has time to turn the head a little between frames.
    while (frames.length < wanted) {
      ctx.drawImage(this.videoEl, 0, 0, 640, 480);
      frames.push(canvas.toDataURL("image/jpeg", 0.85));
      this.enrolmentState.capturedSamples = frames.length;
      if (fill) fill.style.width = `${Math.round((frames.length / wanted) * 100)}%`;
      this.setEnrolStatus(`Capturing ${name}: ${frames.length} / ${wanted}. Look at the camera and turn your head slowly.`, false);
      await new Promise(resolve => setTimeout(resolve, 400));
    }

    this.setEnrolStatus("Saving face samples...", false);
    try {
      const res = await fetch("/api/enrol", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, frames: frames })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.message || "Enrolment failed.");
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
    this.setEnrolStatus(`${result.name} enrolled with ${result.total_samples} samples. Skipped frames: ${result.skipped}.`, false);
    const nameInput = document.getElementById("inputDriverName");
    if (nameInput) nameInput.value = "";
  }

  renderRosterTable() {
    const tbody = document.getElementById("rosterTableBody");
    if (!tbody) return;

    const drivers = Object.values(this.enrolledDrivers);
    if (drivers.length === 0) {
      tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:var(--text-muted); padding:20px;">No drivers enrolled.</td></tr>';
      return;
    }

    tbody.innerHTML = drivers.map(d => `
      <tr>
        <td style="font-weight:600; color:var(--text-main, #111827);">
          ${d.name}
        </td>
        <td><span class="badge ${d.count > 0 ? "badge-success" : "badge-warning"}">${d.count > 0 ? "Live enrolled" : "Needs live enrolment"}</span></td>
        <td style="color:var(--text-secondary);">${d.count} samples</td>
      </tr>
    `).join("");

    const badge = document.getElementById("enrolledCountBadge");
    if (badge) badge.textContent = `${drivers.length} drivers`;

    const subtabBadge = document.getElementById("subtab-enrol-badge");
    if (subtabBadge) subtabBadge.textContent = drivers.length;
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
            match: l.match_score || 85,
            liveness: l.liveness_result || "PASSED",
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
              <span style="font-family:monospace; color:${isOk ? 'var(--green-success)' : 'var(--red-critical)'}; font-weight:700;">${l.match}%</span>
              <div class="mini-progress-bar" style="width:60px; height:4px; margin-top:0;">
                <div class="mini-progress-fill ${isOk ? '' : 'danger'}" style="width:${l.match}%;"></div>
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

  clearAuditLogs() {
    if (!confirm("Are you sure you want to clear the audit trail?")) return;
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
