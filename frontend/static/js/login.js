// First page: opens the camera by itself, waits until one face is in view, then asks the driver to turn the head
// RIGHT, then LEFT, then look STRAIGHT (arrows on the camera picture). The server checks the face and the turns,
// and on success this page shows ENGINE START and opens the dashboard.
(() => {
  const POLL_MS = 250, RETRY_AFTER_MS = 3000, ENGINE_SHOW_MS = 1200;
  const $ = (id) => document.getElementById(id);
  const video = $("cam"), box = $("camBox"), statusEl = $("status"), hintEl = $("hint"), camOff = $("camOff"), retry = $("retry");
  const progress = $("progress"), progressFill = $("progressFill");
  const overlay = HeadTurn.mountOverlay(box);
  const grab = document.createElement("canvas");
  let busy = false, seen = 0, stopped = false;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // text under the camera ("" normal, "ok" green, "bad" red); the text slides in when it changes
  function say(text, kind, hint) {
    if (statusEl.textContent !== text) {
      statusEl.textContent = text;
      statusEl.classList.remove("swap"); void statusEl.offsetWidth; statusEl.classList.add("swap");
    }
    statusEl.className = "status swap" + (kind ? " " + kind : "");
    if (hint !== undefined) hintEl.textContent = hint;
  }
  // look of the camera frame: scanning, found, blinking, ok, bad (any mix)
  function frame(...states) { box.className = "cam " + states.join(" "); }
  // steps at the top: steps before n are done, step n is active
  function setStep(n) {
    [1, 2, 3].forEach((i) => {
      const el = $("step" + i);
      el.className = "step" + (i < n ? " done" : i === n ? " active" : "");
    });
    $("bar1").className = "bar" + (n > 1 ? " on" : "");
    $("bar2").className = "bar" + (n > 2 ? " on" : "");
  }
  function setProgress(p) { progress.classList.toggle("show", p !== null); progressFill.style.width = p === null ? "0" : Math.round(p * 100) + "%"; }

  function snap(w, h, quality) {
    grab.width = w; grab.height = h;
    grab.getContext("2d").drawImage(video, 0, 0, w, h);
    return grab.toDataURL("image/jpeg", quality);
  }
  async function post(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    let data = null; try { data = await res.json(); } catch (_) {}
    return { ok: res.ok, data };
  }

  async function startCamera() {
    retry.style.display = "none";
    camOff.style.display = "flex"; camOff.querySelector("span").textContent = "Opening the camera...";
    setStep(1); frame(); say("Opening the camera...");
    try {
      video.srcObject = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
      await video.play();
      camOff.style.display = "none";
      frame("scanning");
      say("Looking for your face...", "", "Only the driver should be in view.");
      watch();
    } catch (err) {
      camOff.style.display = "flex"; camOff.querySelector("span").textContent = "Camera is not available.";
      frame("bad");
      say("Camera is not available. Allow the camera in the browser, close other programs using it, then try again.", "bad");
      retry.style.display = "inline-flex";
    }
  }

  async function watch() {                       // wait for exactly one face, twice in a row
    while (!stopped) {
      if (!busy && video.videoWidth) {
        try {
          const r = await post("/api/face-present", { frame: snap(640, 480, 0.75) });
          const faces = r.ok && r.data ? r.data.faces : 0;
          seen = faces === 1 ? seen + 1 : 0;
          if (faces > 1) { frame("scanning", "bad"); say("Only one person should be in view.", "bad"); }
          else if (seen === 0) { frame("scanning"); say("Looking for your face..."); }
          else { frame("scanning", "found"); say("Face found. Hold still...", "ok"); }
          if (seen >= 2) { seen = 0; await login(); }
        } catch (_) { frame("bad"); say("Cannot reach the server.", "bad"); }
      }
      await sleep(POLL_MS);
    }
  }

  async function login() {
    busy = true;
    try {
      const c = await post("/api/auth/challenge");
      if (!c.ok || !c.data || !c.data.token) { frame("bad"); say("Cannot get the blink request from the server.", "bad"); await sleep(RETRY_AFTER_MS); return; }
      // Step 2: turn the head right, then left, then look straight (arrows on the picture, each turns green when seen)
      setStep(2); frame("found", "blinking");
      setProgress(0);
      const clip = await HeadTurn.capture({
        video, challenge: c.data, overlay, say: (t) => say(t, ""), progress: setProgress,
        keep: 4,                      // pictures kept per step: a shorter clip is checked faster (enrolment keeps more)
      });
      overlay.hide();
      if (!clip.ok) {
        setProgress(null); frame("bad"); setStep(1); overlay.reset();
        say("NOT DONE. " + clip.message, "bad", "Trying again in a few seconds.");
        await sleep(RETRY_AFTER_MS);
        frame("scanning"); say("Looking for your face...", "", "Only the driver should be in view.");
        return;
      }
      frame("found"); say("Checking your face...");
      const sentAt = performance.now();
      const r = await post("/api/authenticate", { frames: clip.frames, challenge: c.data.token });
      setProgress(null);
      // for finding slow logins: press F12, Console. "request" is the whole wait, "server" is what the check itself took.
      console.info(`Face login: ${clip.frames.length} pictures sent, request ${Math.round(performance.now() - sentAt)} ms, server check ${r.data && r.data.elapsed_ms} ms`, r.data && r.data.timing_ms);
      if (r.ok && r.data && r.data.status === "success" && r.data.authorized) { engineStart(r.data.driver); return; }
      const why = (r.data && r.data.message) || "Face check failed.";
      frame("bad"); setStep(1); overlay.reset();
      say("NOT AUTHORISED. The engine will not start. " + why, "bad", "Trying again in a few seconds.");
      await sleep(RETRY_AFTER_MS);
      frame("scanning"); say("Looking for your face...", "", "Only the driver should be in view.");
    } catch (_) {
      setProgress(null); setStep(1); frame("bad"); overlay.reset();
      say("Cannot reach the server. The engine will not start.", "bad");
      await sleep(RETRY_AFTER_MS);
    } finally { busy = false; }
  }

  function engineStart(driver) {
    stopped = true;
    setStep(4); frame("ok");
    if (video.srcObject) video.srcObject.getTracks().forEach((t) => t.stop());
    setTimeout(() => {
      $("loginView").style.display = "none";
      $("engineView").style.display = "block";
      $("engineWho").textContent = `Welcome, ${driver}. Opening the dashboard...`;
      setTimeout(() => { location.href = "/home"; }, ENGINE_SHOW_MS);
    }, 150);
  }

  retry.addEventListener("click", startCamera);
  startCamera();
})();
