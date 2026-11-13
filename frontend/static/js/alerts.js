/**
 * AcciAlert - Alert Notification & Web Audio Synthesizer
 * Handles real-time alert banners, modal alerts, and synthetic sound synthesis.
 * Author: Muniyappan
 */

class AlertSystem {
  constructor() {
    this.audioCtx = null;
    this.isMuted = false;
    this.activeSirenInterval = null;
    this.currentAlert = null;
    this.alertQueue = [];

    this.bannerEl = document.getElementById("alert-banner-container");
    this.bannerTitleEl = document.getElementById("alert-banner-title");
    this.bannerMsgEl = document.getElementById("alert-banner-message");
    this.bannerIconEl = document.getElementById("alert-banner-icon");

    this.initAudioContext();
  }

  /**
   * Lazily initialize Web Audio API context upon user interaction.
   */
  initAudioContext() {
    const unlockAudio = () => {
      if (!this.audioCtx) {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (AudioContextClass) {
          this.audioCtx = new AudioContextClass();
        }
      }
      if (this.audioCtx && this.audioCtx.state === "suspended") {
        this.audioCtx.resume();
      }
      document.removeEventListener("click", unlockAudio);
      document.removeEventListener("keydown", unlockAudio);
    };

    document.addEventListener("click", unlockAudio, { once: true });
    document.addEventListener("keydown", unlockAudio, { once: true });
  }

  toggleMute() {
    this.isMuted = !this.isMuted;
    this.stopActiveSiren();
    const muteBtn = document.getElementById("btn-mute-toggle");
    if (muteBtn) {
      muteBtn.innerHTML = this.isMuted
        ? '<i class="bi bi-volume-mute-fill"></i> Muted'
        : '<i class="bi bi-volume-up-fill"></i> Sound ON';
      muteBtn.classList.toggle("btn-orange", this.isMuted);
      muteBtn.classList.toggle("btn-outline", !this.isMuted);
    }
    window.DashboardApp && window.DashboardApp.showToast(
      this.isMuted ? "Alert audio muted" : "Alert audio enabled",
      "info"
    );
  }

  /**
   * Synthesize sound using Web Audio API oscillators.
   */
  playSound(type) {
    if (this.isMuted) return;

    if (!this.audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) this.audioCtx = new AudioContextClass();
    }

    if (!this.audioCtx) return;
    if (this.audioCtx.state === "suspended") {
      this.audioCtx.resume();
    }

    const ctx = this.audioCtx;
    const now = ctx.currentTime;

    switch (type.toLowerCase()) {
      case "accident":
        // High-low alternating rapid emergency siren
        this.startEmergencySiren();
        break;

      case "alcohol":
        // Harsh dual-frequency warning buzz
        this.playTone(320, "sawtooth", 0.4, 0.25);
        setTimeout(() => this.playTone(280, "sawtooth", 0.4, 0.25), 180);
        setTimeout(() => this.playTone(320, "sawtooth", 0.4, 0.25), 360);
        break;

      case "unauthorized":
        // Triple sharp warning beeps
        this.playTone(650, "sine", 0.15, 0.2);
        setTimeout(() => this.playTone(750, "sine", 0.15, 0.2), 120);
        setTimeout(() => this.playTone(850, "sine", 0.2, 0.25), 240);
        break;

      case "obstacle":
        // High radar sonar ping
        this.playTone(1200, "sine", 0.12, 0.28, true);
        setTimeout(() => this.playTone(1400, "sine", 0.14, 0.3, true), 160);
        break;

      case "overspeed":
        // Double alert chime
        this.playTone(880, "triangle", 0.18, 0.25);
        setTimeout(() => this.playTone(1100, "triangle", 0.25, 0.3), 150);
        break;

      case "zone":
        // Soft positive geofence chime
        this.playTone(523.25, "sine", 0.22, 0.2); // C5
        setTimeout(() => this.playTone(659.25, "sine", 0.35, 0.25), 160); // E5
        break;

      case "signal":
        // Continuous pulsed red stop warning
        this.playTone(700, "square", 0.25, 0.15);
        setTimeout(() => this.playTone(700, "square", 0.25, 0.15), 300);
        break;

      default:
        this.playTone(600, "sine", 0.2, 0.2);
        break;
    }
  }

  playTone(freq, waveType = "sine", duration = 0.2, volume = 0.2, isChirp = false) {
    if (this.isMuted || !this.audioCtx) return;

    try {
      const ctx = this.audioCtx;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.type = waveType;
      osc.frequency.setValueAtTime(freq, ctx.currentTime);

      if (isChirp) {
        osc.frequency.exponentialRampToValueAtTime(freq * 0.5, ctx.currentTime + duration);
      }

      gain.gain.setValueAtTime(volume, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start();
      osc.stop(ctx.currentTime + duration);
    } catch (e) {
      console.warn("WebAudio tone error:", e);
    }
  }

  startEmergencySiren() {
    this.stopActiveSiren();
    if (this.isMuted) return;

    let high = true;
    const step = () => {
      this.playTone(high ? 920 : 640, "sawtooth", 0.28, 0.35);
      high = !high;
    };

    step();
    this.activeSirenInterval = setInterval(step, 300);

    // Auto terminate after 6 seconds if not acknowledged
    setTimeout(() => this.stopActiveSiren(), 6000);
  }

  stopActiveSiren() {
    if (this.activeSirenInterval) {
      clearInterval(this.activeSirenInterval);
      this.activeSirenInterval = null;
    }
  }

  /**
   * Display or queue a new alert.
   */
  triggerAlert(alertObj) {
    if (!alertObj || !alertObj.type) return;

    // Check if duplicate of current alert within last 3 seconds
    if (this.currentAlert && this.currentAlert.type === alertObj.type) {
      return;
    }

    this.currentAlert = alertObj;
    this.displayBanner(alertObj);
    this.playSound(alertObj.sound || alertObj.type);

    // Refresh event history table
    if (window.DashboardApp && typeof window.DashboardApp.loadEvents === "function") {
      window.DashboardApp.loadEvents();
    }
  }

  displayBanner(alert) {
    if (!this.bannerEl) return;

    const titles = {
      "ACCIDENT": "CRITICAL: ACCIDENT DETECTED",
      "ALCOHOL DETECTED": "SAFETY ALERT: ALCOHOL DETECTED",
      "UNAUTHORISED DRIVER": "SECURITY ALERT: UNAUTHORISED DRIVER",
      "OBSTACLE DETECTED": "PROXIMITY WARNING: OBSTACLE DETECTED",
      "OVERSPEED": "SPEED VIOLATION: OVERSPEED",
      "DANGER ZONE": alert.title || "DANGER ZONE ENTERED",
      "TRAFFIC SIGNAL STOP": "SIGNAL VIOLATION: RED SIGNAL STOP"
    };

    const icons = {
      "ACCIDENT": "bi-exclamation-octagon-fill",
      "ALCOHOL DETECTED": "bi-shield-fill-x",
      "UNAUTHORISED DRIVER": "bi-person-x-fill",
      "OBSTACLE DETECTED": "bi-radar",
      "OVERSPEED": "bi-speedometer2",
      "DANGER ZONE": "bi-geo-alt-fill",
      "TRAFFIC SIGNAL STOP": "bi-stoplights-fill"
    };

    if (this.bannerTitleEl) {
      this.bannerTitleEl.innerText = titles[alert.type] || alert.title || alert.type;
    }
    if (this.bannerMsgEl) {
      this.bannerMsgEl.innerText = alert.message || "Alert conditions detected by vehicle telemetry.";
    }
    if (this.bannerIconEl) {
      const iconClass = icons[alert.type] || "bi-exclamation-triangle-fill";
      this.bannerIconEl.className = `bi ${iconClass}`;
    }

    this.bannerEl.classList.add("show");

    // Dynamic styling based on severity
    if (alert.severity === "CRITICAL" || alert.type === "ACCIDENT") {
      this.bannerEl.style.background = "linear-gradient(90deg, #990000, #D90429, #EF233C)";
    } else if (alert.type === "ALCOHOL DETECTED" || alert.type === "UNAUTHORISED DRIVER") {
      this.bannerEl.style.background = "linear-gradient(90deg, #C70039, #E63946, #FF5722)";
    } else {
      this.bannerEl.style.background = "linear-gradient(90deg, #7F2B00, #E65100, #F97316)";
    }
  }

  acknowledgeAlert() {
    this.stopActiveSiren();
    this.currentAlert = null;
    if (this.bannerEl) {
      this.bannerEl.classList.remove("show");
    }

    // Inform backend
    fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command: "ACK" })
    }).catch(err => console.error("ACK failed:", err));

    if (window.DashboardApp) {
      window.DashboardApp.showToast("Alert acknowledged.", "success");
    }
  }
}

// Global instance
window.AlertManager = new AlertSystem();

