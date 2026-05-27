/* Budget view. Unified bucket + category sliders + recurring management. */
(function () {
  "use strict";
  const { api, fmtAED, escapeHtml, toast } = window.U;

  async function render(container) {
    container.innerHTML = `<div class="muted">Loading…</div>`;
    const [sumR, catR, recR, allCatsR] = await Promise.all([
      api("/api/budget/summary"),
      api("/api/budget/categories"),
      api("/api/budget/recurring"),
      api("/api/transactions/categories"),
    ]);
    if (!sumR.ok) { container.innerHTML = `<div class="neo-card empty-state">Failed to load.</div>`; return; }

    const s = sumR.data;
    container.innerHTML = `
      <div class="neo-card">
        <div class="topbar">
          <h2>${s.month}</h2>
          <div class="topbar-spacer"></div>
          ${window.App.isAdmin ? `<button class="neo-btn" id="btn-set-budget">Change budget</button>` : ""}
        </div>
        <div class="tile-grid">
          ${tile("Unified budget", fmtAED(s.monthly_budget))}
          ${tile("Remaining", fmtAED(s.remaining), s.remaining < 0 ? "neg" : "")}
          ${tile("Spent", fmtAED(s.spent))}
          ${tile("Per-day", `${fmtAED(s.per_day)} /day`)}
        </div>
      </div>

      <div class="neo-card">
        <div class="topbar">
          <h2>Category sub-buckets</h2>
          <div class="topbar-spacer"></div>
          ${window.App.isAdmin ? `<button class="neo-btn" id="btn-add-cat">+ Allocate</button>` : ""}
        </div>
        <div id="cat-allocs"></div>
      </div>

      <div class="neo-card">
        <div class="topbar">
          <h2>Recurring payments</h2>
          <div class="topbar-spacer"></div>
          ${window.App.isAdmin ? `<button class="neo-btn" id="btn-add-rec">+ New recurring</button>` : ""}
        </div>
        <div id="rec-list"></div>
      </div>`;

    renderCategories(catR.ok ? catR.data.allocations : [], allCatsR.ok ? allCatsR.data.categories : []);
    renderRecurring(recR.ok ? recR.data.recurring : [], allCatsR.ok ? allCatsR.data.categories : []);

    const btnSet = document.getElementById("btn-set-budget");
    if (btnSet) btnSet.addEventListener("click", () => promptBudget(s.month, s.monthly_budget, () => render(container)));
    const btnAddCat = document.getElementById("btn-add-cat");
    if (btnAddCat) btnAddCat.addEventListener("click", () => promptAddCategory(s.month, allCatsR.data.categories, () => render(container)));
    const btnAddRec = document.getElementById("btn-add-rec");
    if (btnAddRec) btnAddRec.addEventListener("click", () => promptAddRecurring(allCatsR.data.categories, () => render(container)));
  }

  function tile(label, value, cls = "") {
    return `<div class="neo-card tile ${cls}"><div class="label">${label}</div><div class="value">${value}</div></div>`;
  }

  function renderCategories(allocs, allCats) {
    const wrap = document.getElementById("cat-allocs");
    if (!allocs.length) { wrap.innerHTML = `<p class="muted">No category allocations yet.</p>`; return; }
    wrap.innerHTML = allocs.map(a => {
      const pct = a.allocated_amount > 0 ? Math.min(100, Math.round((a.spent / a.allocated_amount) * 100)) : 0;
      const over = a.spent > a.allocated_amount;
      return `<div class="cat-row neo-inset">
        <div class="cat-head">
          <strong>${escapeHtml(a.name)}</strong>
          <span class="muted">${fmtAED(a.spent)} of ${fmtAED(a.allocated_amount)}</span>
        </div>
        <div class="progress"><div class="progress-fill ${over?'neg':''}" style="width:${Math.min(100,pct)}%"></div></div>
        ${window.App.isAdmin ? `<div class="slider-row">
          <input type="range" min="0" max="${Math.max(a.allocated_amount, a.spent) + 200000}" step="1000" value="${a.allocated_amount}" data-cb="${a.id}" class="slider">
          <input type="number" step="0.01" min="0" class="neo-input narrow" value="${(a.allocated_amount/100).toFixed(2)}" data-cbamt="${a.id}">
          <button class="neo-btn neo-btn-ghost btn-del-cb" data-cb="${a.id}">Release</button>
        </div>` : ""}
      </div>`;
    }).join("");

    wrap.querySelectorAll(".slider").forEach(sl => {
      const id = sl.dataset.cb;
      const amt = wrap.querySelector(`[data-cbamt="${id}"]`);
      sl.addEventListener("input", () => { amt.value = (parseInt(sl.value,10)/100).toFixed(2); });
      sl.addEventListener("change", async () => {
        const r = await api(`/api/budget/categories/${id}`, { method: "PUT", body: { amount: parseFloat(amt.value) }});
        if (!r.ok) toast(r.data?.error || "Adjust failed", "error");
        else toast("Adjusted");
      });
      amt.addEventListener("change", async () => {
        sl.value = Math.round(parseFloat(amt.value) * 100);
        const r = await api(`/api/budget/categories/${id}`, { method: "PUT", body: { amount: parseFloat(amt.value) }});
        if (!r.ok) toast(r.data?.error || "Adjust failed", "error");
        else toast("Adjusted");
      });
    });
    wrap.querySelectorAll(".btn-del-cb").forEach(b => b.addEventListener("click", async () => {
      if (!confirm("Release this allocation back to the unified bucket?")) return;
      const r = await api(`/api/budget/categories/${b.dataset.cb}`, { method: "DELETE" });
      if (!r.ok) toast("Failed", "error"); else { toast("Released"); render(document.getElementById("view")); }
    }));
  }

  function renderRecurring(rec, allCats) {
    const wrap = document.getElementById("rec-list");
    if (!rec.length) { wrap.innerHTML = `<p class="muted">No recurring payments yet.</p>`; return; }
    wrap.innerHTML = rec.map(r => `
      <div class="rec-row neo-inset">
        <div class="grow">
          <strong>${escapeHtml(r.description)}</strong>
          <div class="muted">Day ${r.start_date.slice(8)} • ${fmtAED(r.base_amount)} • ${r.source} ${r.category_name ? `• ${escapeHtml(r.category_name)}` : ""}</div>
        </div>
        ${window.App.isAdmin ? `<button class="neo-btn neo-btn-ghost btn-del-rec" data-rid="${r.id}">Delete</button>` : ""}
      </div>
    `).join("");
    wrap.querySelectorAll(".btn-del-rec").forEach(b => b.addEventListener("click", async () => {
      if (!confirm("Delete this recurring payment? (Cannot be paused — only deleted.)")) return;
      const r = await api(`/api/budget/recurring/${b.dataset.rid}`, { method: "DELETE" });
      if (!r.ok) toast("Failed", "error"); else { toast("Deleted"); render(document.getElementById("view")); }
    }));
  }

  function promptBudget(month, current, onSave) {
    const m = window.U.modal(`
      <h2>Change monthly budget</h2>
      <p class="muted">Recalculates per-day from this point forward — past spending is not retroactively adjusted.</p>
      <form id="bg-form">
        <label>Month <input class="neo-input" name="month" value="${month}" required pattern="\\d{4}-\\d{2}"></label>
        <label>Budget (AED) <input class="neo-input" name="amount" type="number" min="1" step="1" value="${(current/100).toFixed(0)}" required></label>
        <div class="form-actions"><button type="button" class="neo-btn" id="bg-cancel">Cancel</button><button type="submit" class="neo-btn neo-btn-primary">Save</button></div>
      </form>`);
    document.getElementById("bg-cancel").addEventListener("click", m.close);
    document.getElementById("bg-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      const r = await api("/api/budget/monthly", { method: "PUT", body: { month: f.month.value, amount: parseFloat(f.amount.value) }});
      if (!r.ok) { toast(r.data?.error || "Failed", "error"); return; }
      m.close(); toast("Budget updated"); if (onSave) onSave();
    });
  }

  function promptAddCategory(month, allCats, onSave) {
    const m = window.U.modal(`
      <h2>Allocate to category</h2>
      <p class="muted">Reduces the unified bucket. The slider after creation only redistributes.</p>
      <form id="ac-form">
        <label>Category
          <select class="neo-input" name="category_id" required>
            <option value="">Select…</option>
            ${allCats.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("")}
          </select>
        </label>
        <label>Amount (AED) <input class="neo-input" name="amount" type="number" min="1" step="0.01" required></label>
        <div class="form-actions"><button type="button" class="neo-btn" id="ac-cancel">Cancel</button><button type="submit" class="neo-btn neo-btn-primary">Allocate</button></div>
        <p class="form-error" id="ac-err"></p>
      </form>`);
    document.getElementById("ac-cancel").addEventListener("click", m.close);
    document.getElementById("ac-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      const r = await api("/api/budget/categories", { method: "POST", body: {
        month, category_id: parseInt(f.category_id.value,10), amount: parseFloat(f.amount.value)
      }});
      if (!r.ok) { document.getElementById("ac-err").textContent = r.data?.error || "Failed"; return; }
      m.close(); toast("Allocated"); if (onSave) onSave();
    });
  }

  function promptAddRecurring(allCats, onSave) {
    const today = new Date().toISOString().slice(0,10);
    const m = window.U.modal(`
      <h2>New recurring payment</h2>
      <form id="rec-form">
        <label>Description <input class="neo-input" name="description" required></label>
        <label>Amount (AED) <input class="neo-input" name="amount" type="number" min="0.01" step="0.01" required></label>
        <label>Source
          <select class="neo-input" name="source"><option value="bank">Bank</option><option value="petty">Petty Cash</option></select>
        </label>
        <label>Category (optional)
          <select class="neo-input" name="category_id"><option value="">—</option>
            ${allCats.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("")}
          </select>
        </label>
        <label>Start date (trigger day each month) <input class="neo-input" name="start_date" type="date" value="${today}" required></label>
        <div class="form-actions"><button type="button" class="neo-btn" id="rec-cancel">Cancel</button><button type="submit" class="neo-btn neo-btn-primary">Add</button></div>
        <p class="form-error" id="rec-err"></p>
      </form>`);
    document.getElementById("rec-cancel").addEventListener("click", m.close);
    document.getElementById("rec-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      const r = await api("/api/budget/recurring", { method: "POST", body: {
        description: f.description.value, amount: parseFloat(f.amount.value),
        source: f.source.value, category_id: f.category_id.value ? parseInt(f.category_id.value,10) : null,
        start_date: f.start_date.value,
      }});
      if (!r.ok) { document.getElementById("rec-err").textContent = r.data?.error || "Failed"; return; }
      m.close(); toast("Recurring added"); if (onSave) onSave();
    });
  }

  window.BudgetView = { render };
})();
