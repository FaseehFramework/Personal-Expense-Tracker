/* Shared utilities used by every tab module. Exposed on window.U. */
(function () {
  "use strict";

  async function api(path, opts = {}) {
    const init = Object.assign({headers: {}}, opts);
    if (init.body && typeof init.body === "object" && !(init.body instanceof FormData)) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(init.body);
    }
    const resp = await fetch(path, init);
    let data = null;
    try { data = await resp.json(); } catch (e) { /* not json */ }
    return { ok: resp.ok, status: resp.status, data };
  }

  function fmtAED(fils) {
    if (fils === null || fils === undefined) return "AED —";
    const n = Number(fils) / 100;
    const sign = n < 0 ? "-" : "";
    const abs = Math.abs(n);
    return `${sign}AED ${abs.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
  }

  function fmtDate(iso) {
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  }

  function el(tag, attrs = {}, children = []) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v;
      else if (k === "html") e.innerHTML = v;
      else if (k === "on") {
        for (const [ev, fn] of Object.entries(v)) e.addEventListener(ev, fn);
      } else if (v !== null && v !== undefined) {
        e.setAttribute(k, v);
      }
    }
    for (const c of [].concat(children)) {
      if (c == null) continue;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return e;
  }

  function modal(contentHTML, opts = {}) {
    const backdrop = el("div", {class: "modal-backdrop", on: {click: (e) => { if (e.target === backdrop) close(); }}});
    const card = el("div", {class: "neo-card modal-card", html: contentHTML});
    backdrop.appendChild(card);
    document.body.appendChild(backdrop);
    function close() { backdrop.remove(); if (opts.onClose) opts.onClose(); }
    return { backdrop, card, close };
  }

  function toast(msg, kind = "info") {
    const t = el("div", {class: `toast toast-${kind}`}, [msg]);
    document.body.appendChild(t);
    setTimeout(() => t.classList.add("toast-show"), 10);
    setTimeout(() => { t.classList.remove("toast-show"); setTimeout(() => t.remove(), 300); }, 2400);
  }

  function escapeHtml(s) {
    if (s == null) return "";
    return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  }

  function debounce(fn, ms = 250) {
    let t = null;
    return function (...args) { clearTimeout(t); t = setTimeout(() => fn.apply(this, args), ms); };
  }

  // ---- money input helpers ----
  // Money values cross the wire as STRINGS, never floats. parseFloat round-trips
  // through IEEE 754 and lost-precision tickets in this app trace to spinner /
  // scroll-wheel accumulation on type=number inputs (e.g. 200 - 9*0.01 = 199.91
  // exactly in IEEE 754). The Python backend's aed_to_fils accepts strings and
  // converts them via Decimal, so a string round-trip is lossless.
  function moneyStr(input) {
    if (!input) return "";
    const raw = (input.value ?? "").toString().trim();
    return raw;
  }

  // Apply on the document once: prevent scroll-wheel from accidentally changing
  // a focused number input, and snap to 2dp on blur so the displayed value
  // matches what will be submitted.
  function installMoneyInputGuards() {
    if (document.__moneyInputGuardsInstalled) return;
    document.__moneyInputGuardsInstalled = true;

    document.addEventListener("wheel", (e) => {
      const t = e.target;
      if (t && t.tagName === "INPUT" && t.type === "number" && t === document.activeElement) {
        // Stop the spinner from incrementing/decrementing on scroll.
        t.blur();
      }
    }, { passive: true });

    document.addEventListener("blur", (e) => {
      const t = e.target;
      if (!t || t.tagName !== "INPUT" || t.type !== "number") return;
      // Only normalize fields the dev opts in via step="0.01" (money fields).
      if (t.step !== "0.01") return;
      const v = t.value.trim();
      if (v === "") return;
      // Use Decimal-safe parsing: avoid parseFloat's IEEE drift on the visible value.
      // We just snap textually if it's a clean decimal; otherwise let the form's
      // own validation flag it.
      const m = v.match(/^-?\d+(\.\d+)?$/);
      if (!m) return;
      const [whole, frac = ""] = v.replace(/^-/, "").split(".");
      const fracPadded = (frac + "00").slice(0, 2);
      t.value = (v.startsWith("-") ? "-" : "") + whole + "." + fracPadded;
    }, true);
  }
  // Auto-install on script load.
  if (document.readyState !== "loading") installMoneyInputGuards();
  else document.addEventListener("DOMContentLoaded", installMoneyInputGuards);

  window.U = { api, fmtAED, fmtDate, el, modal, toast, escapeHtml, debounce, moneyStr };
})();
