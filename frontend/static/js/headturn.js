// Head-turn clip, shared by the face login (first page) and driver enrolment (admin panel).
// The driver looks straight, turns the head to the RIGHT, then to the LEFT, then looks straight again.
// Arrows on the camera picture show what to do; each step turns green as soon as the server sees the turn.
// The page takes pictures all the time and keeps them: they are the clip the server checks again (/api/authenticate or /api/enrol).
// The camera picture is shown as a mirror, so the arrow on the right of the screen is the driver's own right.
(() => {
  // three small chevrons in a row; the page makes them flow toward the side the head must turn to
  const ARROW = '<svg viewBox="0 0 72 48" aria-hidden="true"><path class="c1" d="M8 6l16 18-16 18"/><path class="c2" d="M28 6l16 18-16 18"/><path class="c3" d="M48 6l16 18-16 18"/></svg>';
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Puts the arrows on top of a camera box (any element with position: relative). Returns a small controller.
  function mountOverlay(box) {
    const old = box.querySelector(".ht-overlay");
    if (old) old.remove();
    const el = document.createElement("div");
    el.className = "ht-overlay";
    el.innerHTML =
      `<div class="ht-arrow ht-left" data-step="LEFT"><span>${ARROW}</span></div>` +
      `<div class="ht-arrow ht-right" data-step="RIGHT"><span>${ARROW}</span></div>` +
      `<div class="ht-straight" data-step="STRAIGHT"><span></span></div>` +
      `<div class="ht-label"></div>`;
    box.appendChild(el);
    const label = el.querySelector(".ht-label");
    const part = (name) => el.querySelector(`[data-step="${name}"]`);
    return {
      reset() { el.classList.remove("on"); el.querySelectorAll("[data-step]").forEach((n) => n.className = n.className.replace(/\bht-(active|done)\b/g, "").trim()); label.textContent = ""; label.classList.remove("show"); },
      active(name, text) { el.classList.add("on"); el.querySelectorAll("[data-step]").forEach((n) => n.classList.remove("ht-active")); if (name) part(name).classList.add("ht-active"); label.textContent = text || ""; label.classList.toggle("show", Boolean(text)); },
      done(name) { const n = part(name); n.classList.remove("ht-active"); n.classList.add("ht-done"); },
      hide() { el.classList.remove("on"); label.classList.remove("show"); el.querySelectorAll("[data-step]").forEach((n) => n.classList.remove("ht-active")); },
    };
  }

  async function post(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    let data = null; try { data = await res.json(); } catch (_) {}
    return { ok: res.ok, data };
  }

  /**
   * Takes the clip. options:
   *   video      the <video> element
   *   challenge  the object from /api/auth/challenge (token, turn_min, straight_max, base_pictures, run)
   *   overlay    from mountOverlay()
   *   say(text)  shows a message
   *   progress(p) 0..1 (optional)
   *   keep       how many pictures of each step go into the clip (default 10)
   * Resolves {ok, frames, message}. ok is false when a step was not seen in time (nothing should be sent then).
   */
  async function capture(options) {
    const { video, challenge, overlay } = options;
    const say = options.say || (() => {});
    const progress = options.progress || (() => {});
    const keep = options.keep || 10;
    const canvas = document.createElement("canvas");
    canvas.width = 640; canvas.height = 480;
    const ctx = canvas.getContext("2d");
    const frames = [];            // the clip: the starting pictures, then the last pictures of each step, in order
    let current = [];             // pictures of the step that is running now
    const need = challenge.run || 2;
    const turnMin = challenge.turn_min, straightMax = challenge.straight_max, basePictures = challenge.base_pictures || 4;
    let base = null;
    const baseValues = [];

    // one picture: keep it for the clip, ask the server how far the head is turned
    async function look() {
      ctx.drawImage(video, 0, 0, 640, 480);
      const shot = canvas.toDataURL("image/jpeg", 0.8);
      current.push(shot);
      const r = await post("/api/head-pose", { frame: shot });
      if (!r.ok || !r.data) return { faces: 0, yaw: null, error: (r.data && r.data.message) || "server" };
      return { faces: r.data.faces, yaw: r.data.yaw };
    }

    async function waitFor(name, test, seconds, text) {
      overlay.active(name, text);
      say(text);
      const until = Date.now() + seconds * 1000;
      let run = 0, crowded = false;
      current = [];
      while (Date.now() < until) {
        const t = Date.now();
        const p = await look();
        if (p.error) return { ok: false, message: p.error === "server" ? "Cannot reach the server." : p.error };
        crowded = p.faces > 1;
        if (p.yaw !== null && p.faces === 1) {             // pictures without a clear face are skipped, like on the server
          run = test(p.yaw - base) ? run + 1 : 0;
          if (run >= need) { overlay.done(name); frames.push(...current.slice(-keep)); return { ok: true }; }
        }
        await sleep(Math.max(0, 70 - (Date.now() - t)));
      }
      return { ok: false, message: crowded ? "Only one person should be in view." : `${name === "STRAIGHT" ? "Look straight at the camera." : `Turn your head ${name} clearly, until the arrow turns green.`}` };
    }

    overlay.reset();
    progress(0);
    // 0. look straight: the first pictures are the starting point (everyone's "straight" is a little different)
    say("Look straight at the camera and hold still...");
    overlay.active("STRAIGHT", "Look straight");
    const baseUntil = Date.now() + 8000;
    while (baseValues.length < basePictures && Date.now() < baseUntil) {
      const p = await look();
      if (p.error) return { ok: false, frames, message: p.error === "server" ? "Cannot reach the server." : p.error };
      if (p.faces === 1 && p.yaw !== null) { baseValues.push(p.yaw); frames.push(current[current.length - 1]); }
      else if (p.faces > 1) say("Only one person should be in view.");
      await sleep(70);
    }
    if (baseValues.length < basePictures) return { ok: false, frames, message: "Your face was not seen. Look at the camera." };
    base = baseValues.slice().sort((a, b) => a - b)[Math.floor((basePictures - 1) / 2)];
    if (basePictures % 2 === 0) { const s = baseValues.slice().sort((a, b) => a - b); base = (s[basePictures / 2 - 1] + s[basePictures / 2]) / 2; }
    overlay.done("STRAIGHT"); await sleep(150); overlay.reset();
    progress(0.25);

    // 1. RIGHT, 2. LEFT, 3. STRAIGHT
    let r = await waitFor("RIGHT", (d) => d <= -turnMin, 8, "Turn your head to the RIGHT");
    if (!r.ok) return { ok: false, frames, message: r.message };
    progress(0.5);
    r = await waitFor("LEFT", (d) => d >= turnMin, 9, "Now turn your head to the LEFT");
    if (!r.ok) return { ok: false, frames, message: r.message };
    progress(0.75);
    r = await waitFor("STRAIGHT", (d) => Math.abs(d) <= straightMax, 7, "Now look straight");
    if (!r.ok) return { ok: false, frames, message: r.message };
    progress(1);
    overlay.hide();
    return { ok: true, frames, message: "" };
  }

  window.HeadTurn = { mountOverlay, capture, post };
})();
