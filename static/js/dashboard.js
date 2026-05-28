/* Dashboard view. Pulls /api/budget/summary and renders 6 tiles + cascade banner. */
(function () {
  "use strict";
  const { api, fmtAED, el, escapeHtml } = window.U;

  async function render(container) {
    container.innerHTML = `<div class="muted">Loading dashboard…</div>`;
    const { ok, data } = await api("/api/budget/summary");
    if (!ok) { container.innerHTML = `<div class="neo-card empty-state">Could not load dashboard.</div>`; return; }

    const cascadeBanner = data.cascade_in > 0
      ? `<div class="neo-card cascade-banner">
           <strong>Carried in:</strong> ${fmtAED(data.cascade_in)} deducted from this month's budget.
         </div>`
      : "";

    const remainingClass = data.remaining < 0 ? "neg" : "";
    const potNegative = data.savings < 0;
    const savingsSub = potNegative
      ? `Pot: <span class="neg">${fmtAED(data.savings)}</span>`
      : `Pot: ${fmtAED(data.savings)}`;
    const savingsTileCls = potNegative ? "neg" : "";

    container.innerHTML = `
      ${cascadeBanner}
      ${potNegative ? `<div class="neo-card pot-explain">
        Your savings pot is negative because a past month's savings were revised downward
        after a wishlist draw had already occurred. It will recover as future months close positively.
      </div>` : ""}
      <div class="tile-grid">
        ${tile("Remaining budget", fmtAED(data.remaining), `of ${fmtAED(data.monthly_budget)} this month`, remainingClass)}
        ${tile("Per-day remaining", `${fmtAED(data.per_day)} /day`, "Smoothed by upcoming recurring")}
        ${tile("Spent this month", fmtAED(data.spent), "Budget-affecting only")}
        ${tile("Bank balance", fmtAED(data.bank), "")}
        ${tile("Petty cash", fmtAED(data.petty), "")}
        ${tile("Saved this month", fmtAED(data.saved_this_month), savingsSub, savingsTileCls)}
      </div>
      <div class="neo-card upcoming-recurring">
        <h3>Upcoming this month</h3>
        <p class="muted" id="upcoming-line"></p>
        <div id="pending-recurring"></div>
      </div>`;

    document.getElementById("upcoming-line").textContent =
      data.upcoming_recurring > 0
        ? `Reserved for upcoming recurring payments: ${fmtAED(data.upcoming_recurring)}`
        : "No upcoming recurring payments this month.";

    // Streak insight card (§13) — show the strongest streak; dismissable.
    renderStreakCard(container);

    const pendResp = await api("/api/budget/recurring/pending");
    const list = document.getElementById("pending-recurring");
    if (!pendResp.ok || !pendResp.data.pending.length) {
      list.innerHTML = `<p class="muted">No recurring payments due today.</p>`;
      return;
    }
    list.innerHTML = pendResp.data.pending.map(p => `
      <div class="recurring-row neo-inset">
        <div class="grow">
          <strong>${escapeHtml(p.description)}</strong>
          <div class="muted">Due day ${p.trigger_day} • from ${p.source}</div>
        </div>
        <div class="confirm-form">
          <input class="neo-input narrow" type="number" step="0.01" min="0" value="${(p.pre_fill_amount/100).toFixed(2)}" id="rec-amt-${p.recurring_id}">
          <button class="neo-btn neo-btn-primary" data-rid="${p.recurring_id}">Confirm</button>
        </div>
      </div>
    `).join("");
    list.querySelectorAll("button[data-rid]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const rid = btn.dataset.rid;
        const amt = window.U.moneyStr(document.getElementById(`rec-amt-${rid}`));
        btn.disabled = true;
        const r = await api(`/api/budget/recurring/${rid}/confirm`, { method: "POST", body: { amount: amt } });
        if (!r.ok) { window.U.toast(r.data.error || "Failed", "error"); btn.disabled = false; return; }
        window.U.toast("Recurring confirmed");
        render(container);
      });
    });
  }

  function tile(label, value, sub, cls = "") {
    return `<div class="neo-card tile ${cls}">
      <div class="label">${label}</div>
      <div class="value">${value}</div>
      ${sub ? `<div class="sub">${sub}</div>` : ""}
    </div>`;
  }

  async function renderStreakCard(container) {
    const r = await api("/api/streaks");
    if (!r.ok || !r.data.streaks.length) return;
    const s = r.data.streaks[0];  // strongest streak
    const card = document.createElement("div");
    card.className = "neo-card streak-card";
    card.innerHTML = `
      <div style="display:flex;align-items:flex-start;gap:14px">
        <div class="grow">
          <h3 style="margin:0 0 6px 0">Spending pattern noticed</h3>
          <p style="margin:0">${window.U.escapeHtml(s.message)}</p>
          <p class="muted small" style="margin:6px 0 0 0">Median amount: ${window.U.fmtAED(s.median_amount)}</p>
        </div>
        ${window.App.isAdmin
          ? `<button class="neo-btn neo-btn-ghost" id="streak-dismiss" title="Dismiss">×</button>`
          : ""}
      </div>`;
    container.appendChild(card);
    document.getElementById("streak-dismiss")?.addEventListener("click", async () => {
      const dr = await api("/api/streaks/dismiss", { method: "POST", body: { signature: s.signature }});
      if (!dr.ok) { window.U.toast(dr.data?.error || "Failed", "error"); return; }
      window.U.toast("Dismissed");
      card.remove();
    });
  }

  window.Dashboard = { render };
})();
