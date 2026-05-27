/* Wishlist tab. §9 — items with projected savings coverage, purchase flow
   with confirm-shortfall modal, abandon, savings-pot display. */
(function () {
  "use strict";
  const { api, fmtAED, escapeHtml, toast } = window.U;

  async function render(container) {
    container.innerHTML = `<div class="muted">Loading…</div>`;
    const r = await api("/api/wishlist");
    if (!r.ok) { container.innerHTML = `<div class="neo-card empty-state">Failed to load.</div>`; return; }

    const potNegative = r.data.savings_pot < 0;
    container.innerHTML = `
      <div class="neo-card">
        <div class="topbar">
          <h2>Savings pot</h2>
          <div class="topbar-spacer"></div>
          <span class="big-amount ${potNegative ? "neg" : ""}">${fmtAED(r.data.savings_pot)}</span>
        </div>
        <p class="muted">Wishlist items draw from this pot first (in priority order). Anything beyond the pot is added to the target month's unified budget, with your confirmation.</p>
        ${potNegative ? `<div class="pot-explain inline">
          Your savings pot is negative because a past month's savings were revised downward
          after a wishlist draw had already occurred. It will recover as future months close positively.
        </div>` : ""}
      </div>

      <div class="neo-card">
        <div class="topbar">
          <h2>Active</h2>
          <div class="topbar-spacer"></div>
          ${window.App.isAdmin ? `<button class="neo-btn" id="btn-new">+ Wish</button>` : ""}
        </div>
        <div id="active-list">${renderItems(r.data.items.filter(i => i.status === "active"))}</div>
      </div>

      <div class="neo-card">
        <h2>Purchased &amp; abandoned</h2>
        <div>${renderItems(r.data.items.filter(i => i.status !== "active"), true)}</div>
      </div>`;

    document.getElementById("btn-new")?.addEventListener("click", () => promptNew(() => render(container)));
    document.querySelectorAll("[data-act]").forEach(b => {
      b.addEventListener("click", () => openActions(parseInt(b.dataset.id, 10), () => render(container)));
    });
  }

  function renderItems(items, history = false) {
    if (!items.length) return `<p class="muted">${history ? "Nothing here yet." : "No active wishes."}</p>`;
    return items.map(it => {
      const coverInfo = it.status === "active"
        ? `<div class="muted small">Savings will cover ${fmtAED(it.projected_savings_cover)} • Budget hit ${fmtAED(it.projected_shortfall)} in ${it.target_month}</div>`
        : `<div class="muted small">Savings drew ${fmtAED(it.savings_drawn)} • Budget charged ${fmtAED(it.budget_charged)} ${it.transaction_id ? "• Linked tx #" + it.transaction_id : ""}</div>`;
      return `<div class="wish-row neo-inset" style="padding:14px 16px;margin-bottom:10px;display:flex;flex-direction:column;gap:6px">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px">
          <strong>${escapeHtml(it.item_name)}</strong>
          <span>${fmtAED(it.estimated_amount)}</span>
        </div>
        <div class="muted small">Target ${escapeHtml(it.target_month)} • status ${it.status}${it.notes ? " • " + escapeHtml(it.notes) : ""}</div>
        ${coverInfo}
        ${window.App.isAdmin && it.status === "active"
          ? `<div style="display:flex;gap:8px;margin-top:6px"><button class="neo-btn" data-act="${it.id}">Manage</button></div>`
          : ""}
      </div>`;
    }).join("");
  }

  function promptNew(onSaved) {
    const month = new Date().toISOString().slice(0,7);
    const m = window.U.modal(`
      <h2>Add to wishlist</h2>
      <form id="wl-form" class="grid-2">
        <label style="grid-column:1/-1">Item name <input class="neo-input" name="item_name" required></label>
        <label>Estimated (AED) <input class="neo-input" type="number" min="0.01" step="0.01" name="estimated_amount" required></label>
        <label>Target month <input class="neo-input" name="target_month" value="${month}" pattern="\\d{4}-\\d{2}" required></label>
        <label style="grid-column:1/-1">Notes <textarea class="neo-input" name="notes" rows="2"></textarea></label>
        <button type="submit" class="neo-btn neo-btn-primary" style="grid-column:1/-1">Add</button>
        <p class="form-error" id="wl-err" style="grid-column:1/-1"></p>
      </form>`);
    document.getElementById("wl-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      const r = await api("/api/wishlist", { method: "POST", body: {
        item_name: f.item_name.value, estimated_amount: parseFloat(f.estimated_amount.value),
        target_month: f.target_month.value, notes: f.notes.value,
      }});
      if (!r.ok) { document.getElementById("wl-err").textContent = r.data?.error || "Failed"; return; }
      m.close(); toast("Wish added"); if (onSaved) onSaved();
    });
  }

  async function openActions(wid, onChange) {
    const pv = await api(`/api/wishlist/${wid}/preview`);
    if (!pv.ok) { toast("Could not load", "error"); return; }
    const it = pv.data.item;
    const today = new Date().toISOString().slice(0,10);
    const shortfallNote = pv.data.shortfall > 0
      ? `<div class="linked-warn">Pot covers ${fmtAED(pv.data.will_cover)} of ${fmtAED(it.estimated_amount)}. <strong>${fmtAED(pv.data.shortfall)} will be added to ${pv.data.target_month}'s budget.</strong></div>`
      : `<p class="muted">The savings pot will cover this purchase in full.</p>`;

    const m = window.U.modal(`
      <h2>${escapeHtml(it.item_name)}</h2>
      <p class="muted">Estimated ${fmtAED(it.estimated_amount)} • Target ${pv.data.target_month}</p>
      ${shortfallNote}

      <h3>Mark as purchased</h3>
      <form id="buy-form" class="grid-2">
        <label>Purchase date <input class="neo-input" type="date" name="date" value="${today}" required></label>
        <label>Actual amount (AED) <input class="neo-input" type="number" step="0.01" min="0.01" name="actual_amount" value="${(it.estimated_amount/100).toFixed(2)}" required></label>
        <label>Source <select class="neo-input" name="source"><option value="bank">Bank</option><option value="petty">Petty</option></select></label>
        <label>Category (optional)
          <select class="neo-input" name="category_id" id="cat-sel"><option value="">—</option></select>
        </label>
        <button type="submit" class="neo-btn neo-btn-primary" style="grid-column:1/-1">Buy &amp; reconcile</button>
        <p class="form-error" id="buy-err" style="grid-column:1/-1"></p>
      </form>

      <div class="form-actions" style="margin-top:14px">
        <button class="neo-btn neo-btn-danger" id="btn-abandon">Abandon</button>
        <button class="neo-btn" id="btn-close">Close</button>
      </div>`);

    // Populate categories.
    api("/api/transactions/categories").then(r => {
      if (r.ok) {
        document.getElementById("cat-sel").innerHTML =
          `<option value="">—</option>` + r.data.categories.map(c =>
            `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
      }
    });

    document.getElementById("btn-close").addEventListener("click", m.close);
    document.getElementById("btn-abandon").addEventListener("click", async () => {
      if (!confirm("Abandon this wish? (Pot is unaffected.)")) return;
      const r = await api(`/api/wishlist/${wid}/abandon`, { method: "POST" });
      if (!r.ok) { toast(r.data?.error || "Failed", "error"); return; }
      m.close(); toast("Abandoned"); if (onChange) onChange();
    });
    document.getElementById("buy-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      const body = {
        date: f.date.value, source: f.source.value,
        actual_amount: parseFloat(f.actual_amount.value),
        category_id: f.category_id.value ? parseInt(f.category_id.value, 10) : null,
      };
      let r = await api(`/api/wishlist/${wid}/purchase`, { method: "POST", body });
      if (r.status === 409 && r.data.error === "shortfall_requires_confirmation") {
        if (!confirm(`This will add ${fmtAED(r.data.shortfall)} to ${r.data.target_month}'s budget. Continue?`)) return;
        body.confirm_shortfall = true;
        r = await api(`/api/wishlist/${wid}/purchase`, { method: "POST", body });
      }
      if (!r.ok) { document.getElementById("buy-err").textContent = r.data?.error || "Failed"; return; }
      m.close(); toast(`Purchased — savings ${fmtAED(r.data.savings_drawn)}, budget ${fmtAED(r.data.budget_charged)}`); if (onChange) onChange();
    });
  }

  window.Wishlist = { render };
})();
