// Card-style message boxes instead of the browser's own confirm() and alert() windows.
//   const yes = await AppDialog.confirm({ title: "Delete zone?", message: "This cannot be undone.", confirmText: "Delete", danger: true });
//   await AppDialog.alert({ title: "Missing details", message: "Enter the name first." });
// Escape or a click outside cancels. Enter confirms (for a danger box the safe button, Cancel, has the focus).
(() => {
  const ICONS = {
    ask:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9.5"/><path d="M9.5 9.2a2.6 2.6 0 1 1 3.7 2.4c-.8.4-1.2 1-1.2 1.9"/><circle cx="12" cy="17" r=".6" fill="currentColor"/></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5l9.5 16.5h-19z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.4" r=".6" fill="currentColor"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9.5"/><path d="M12 11v6"/><circle cx="12" cy="7.6" r=".6" fill="currentColor"/></svg>',
  };
  const escapeHtml = (t) => String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function open({ title, message, confirmText, cancelText, danger, single }) {
    return new Promise((resolve) => {
      const previous = document.activeElement;
      const backdrop = document.createElement("div");
      backdrop.className = "ad-backdrop";
      backdrop.setAttribute("role", "presentation");
      const paragraphs = String(message || "").split(/\n\s*\n/).map((p) => `<p class="ad-msg">${escapeHtml(p).replace(/\n/g, "<br>")}</p>`).join("");
      backdrop.innerHTML =
        `<div class="ad-card${danger ? " danger" : ""}" role="alertdialog" aria-modal="true" aria-labelledby="ad-title">` +
        `<div class="ad-icon">${danger ? ICONS.warn : single ? ICONS.info : ICONS.ask}</div>` +
        `<h3 class="ad-title" id="ad-title">${escapeHtml(title || (single ? "Notice" : "Are you sure?"))}</h3>${paragraphs}` +
        `<div class="ad-buttons">${single ? "" : `<button type="button" class="ad-cancel">${escapeHtml(cancelText || "Cancel")}</button>`}` +
        `<button type="button" class="ad-ok">${escapeHtml(confirmText || (single ? "OK" : "Yes"))}</button></div></div>`;
      document.body.appendChild(backdrop);
      requestAnimationFrame(() => backdrop.classList.add("show"));
      const ok = backdrop.querySelector(".ad-ok"), cancel = backdrop.querySelector(".ad-cancel");
      (danger && cancel ? cancel : ok).focus();

      let closed = false;
      function close(answer) {
        if (closed) return;
        closed = true;
        document.removeEventListener("keydown", onKey, true);
        backdrop.classList.remove("show");
        setTimeout(() => backdrop.remove(), 220);
        if (previous && previous.focus) { try { previous.focus(); } catch (_) {} }
        resolve(answer);
      }
      function onKey(e) {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(false); }
        else if (e.key === "Tab") {                                   // keep the focus inside the box
          const items = [cancel, ok].filter(Boolean);
          const i = items.indexOf(document.activeElement);
          e.preventDefault();
          items[(i + (e.shiftKey ? -1 : 1) + items.length) % items.length].focus();
        }
      }
      document.addEventListener("keydown", onKey, true);
      ok.addEventListener("click", () => close(true));
      if (cancel) cancel.addEventListener("click", () => close(false));
      backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) close(false); });
    });
  }

  window.AppDialog = {
    confirm: (o) => open(Object.assign({}, typeof o === "string" ? { message: o } : o)),
    alert: (o) => open(Object.assign({}, typeof o === "string" ? { message: o } : o, { single: true })).then(() => undefined),
  };
})();
