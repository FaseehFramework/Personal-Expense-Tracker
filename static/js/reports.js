/* Reports tab — month summary, comparison, period analysis, history, exports. §11 */
(function () {
  "use strict";
  const { api, fmtAED, escapeHtml, toast } = window.U;

  async function render(container) {
    const now = new Date();
    const monthDefault = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}`;
    const periodFrom = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}-01`;
    const periodTo = new Date(now.getFullYear(), now.getMonth()+1, 0).toISOString().slice(0,10);

    container.innerHTML = `
      <div class="neo-card">
        <div class="topbar">
          <h2>Month summary</h2>
          <div class="topbar-spacer"></div>
          <input class="neo-input narrow" id="ms-month" value="${monthDefault}" pattern="\\d{4}-\\d{2}">
          <button class="neo-btn" id="ms-load">Load</button>
        </div>
        <div id="ms-out"></div>
      </div>

      <div class="neo-card">
        <h2>Compare months (up to 3)</h2>
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
          <input class="neo-input narrow" id="cmp-1" placeholder="YYYY-MM" pattern="\\d{4}-\\d{2}">
          <input class="neo-input narrow" id="cmp-2" placeholder="YYYY-MM" pattern="\\d{4}-\\d{2}">
          <input class="neo-input narrow" id="cmp-3" placeholder="YYYY-MM" pattern="\\d{4}-\\d{2}">
          <button class="neo-btn" id="cmp-load">Compare</button>
        </div>
        <div id="cmp-out"><p class="muted">Pick at least 2 months and click Compare.</p></div>
      </div>

      <div class="neo-card">
        <h2>Period analysis (pie)</h2>
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
          <input class="neo-input narrow" id="pa-from" type="date" value="${periodFrom}">
          <input class="neo-input narrow" id="pa-to" type="date" value="${periodTo}">
          <select class="neo-input narrow" id="pa-source">
            <option value="">Any source</option>
            <option value="bank">Bank</option>
            <option value="petty">Petty</option>
          </select>
          <button class="neo-btn" id="pa-load">Analyse</button>
        </div>
        <div id="pa-out"></div>
      </div>

      <div class="neo-card">
        <h2>Budget history</h2>
        <div id="hist-out"></div>
      </div>

      <div class="neo-card">
        <h2>Export &amp; backup</h2>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px">
          <input class="neo-input narrow" id="exp-from" type="date" placeholder="From">
          <input class="neo-input narrow" id="exp-to" type="date" placeholder="To">
          <button class="neo-btn neo-btn-primary" id="exp-raw">Download raw CSV</button>
          <button class="neo-btn neo-btn-primary" id="exp-sum">Download summary CSV</button>
          ${window.App.isAdmin ? `<button class="neo-btn" id="exp-db">Download SQLite backup</button>` : ""}
        </div>
        <p class="muted small">Raw export contains every transaction with all fields and split details. Summary contains monthly aggregates per category and source, plus budget vs actual.</p>
      </div>`;

    document.getElementById("ms-load").addEventListener("click", () => loadMonthSummary());
    document.getElementById("cmp-load").addEventListener("click", () => loadComparison());
    document.getElementById("pa-load").addEventListener("click", () => loadPeriod());
    document.getElementById("exp-raw").addEventListener("click", () => downloadExport("raw"));
    document.getElementById("exp-sum").addEventListener("click", () => downloadExport("summary"));
    if (window.App.isAdmin) {
      document.getElementById("exp-db").addEventListener("click", () => {
        window.location.href = "/api/reports/backup-db";
      });
    }

    // Initial loads.
    loadMonthSummary();
    loadHistory();
  }

  async function loadMonthSummary() {
    const month = document.getElementById("ms-month").value;
    const out = document.getElementById("ms-out");
    out.innerHTML = `<div class="muted">Loading…</div>`;
    const r = await api(`/api/reports/month-summary/${month}`);
    if (!r.ok) { out.innerHTML = `<div class="empty-state">${r.data?.error || "Failed"}</div>`; return; }
    const d = r.data;
    const badge = d.closed ? `<span class="badge">closed</span>` : `<span class="badge">in progress</span>`;
    out.innerHTML = `
      <p>${badge}</p>
      <div class="tile-grid">
        ${miniTile("Budget set", fmtAED(d.budget))}
        ${miniTile("Spent", fmtAED(d.spent))}
        ${miniTile("Budget income", fmtAED(d.budget_income))}
        ${miniTile("Variance", fmtAED(d.variance), d.variance < 0 ? "neg" : "")}
        ${miniTile("Rollover (next)", fmtAED(d.rollover))}
        ${miniTile("Saved", fmtAED(d.savings))}
        ${miniTile("Cascade in", fmtAED(d.cascade_in), d.cascade_in > 0 ? "neg" : "")}
        ${miniTile("Cascade out", fmtAED(d.cascade_out), d.cascade_out > 0 ? "neg" : "")}
        ${miniTile("Outstanding receivables", fmtAED(d.outstanding_receivables))}
        ${miniTile("Loans they owe me", fmtAED(d.loans_owed_to_me))}
        ${miniTile("Loans I owe", fmtAED(d.loans_i_owe))}
      </div>`;
  }

  function miniTile(label, value, cls = "") {
    return `<div class="neo-card tile ${cls}"><div class="label">${label}</div><div class="value" style="font-size:1.15rem">${value}</div></div>`;
  }

  async function loadComparison() {
    const months = ["cmp-1","cmp-2","cmp-3"].map(id => document.getElementById(id).value.trim()).filter(Boolean);
    if (months.length < 2) {
      document.getElementById("cmp-out").innerHTML = `<p class="muted">Enter at least 2 months.</p>`;
      return;
    }
    const r = await api(`/api/reports/compare?months=${months.join(",")}`);
    const out = document.getElementById("cmp-out");
    if (!r.ok) { out.innerHTML = `<div class="empty-state">${r.data?.error || "Failed"}</div>`; return; }
    if (!r.data.months.length) { out.innerHTML = `<p class="muted">Not enough data yet — check back after at least 2 completed months.</p>`; return; }
    const groups = r.data.months.map(m => ({
      label: m.month,
      bars: [
        { label: "Budget", value: m.budget, color: "#3d6ae6" },
        { label: "Spent",  value: m.spent,  color: "#c87f0a" },
        { label: "Saved",  value: m.savings, color: "#2e8b57" },
      ],
    }));
    out.innerHTML = window.Charts.barGroup(groups);
  }

  async function loadPeriod() {
    const from = document.getElementById("pa-from").value;
    const to   = document.getElementById("pa-to").value;
    const src  = document.getElementById("pa-source").value;
    const qs = new URLSearchParams({ from, to });
    if (src) qs.set("source", src);
    const r = await api(`/api/reports/period?${qs}`);
    const out = document.getElementById("pa-out");
    if (!r.ok) { out.innerHTML = `<div class="empty-state">Failed</div>`; return; }
    if (!r.data.slices.length) { out.innerHTML = `<p class="muted">Not enough data yet — check back after at least 1 transaction in the selected period.</p>`; return; }
    const slices = r.data.slices.map(s => ({ label: s.category_name, value: s.amount }));
    out.innerHTML = `<p class="muted">Total: <strong>${fmtAED(r.data.total)}</strong></p>` + window.Charts.pie(slices);
  }

  async function loadHistory() {
    const out = document.getElementById("hist-out");
    const r = await api("/api/reports/budget-history");
    if (!r.ok) { out.innerHTML = `<div class="empty-state">Failed</div>`; return; }
    if (!r.data.rows.length) { out.innerHTML = `<p class="muted">Not enough data yet — check back after at least 1 completed month.</p>`; return; }
    out.innerHTML = `<table style="width:100%;border-collapse:collapse">
      <thead><tr>
        <th>Month</th><th>Budget set</th><th>Actual spend</th><th>Variance</th>
        <th>Rollover</th><th>Saved</th><th>Cascade</th>
      </tr></thead>
      <tbody>${r.data.rows.map(row => `<tr>
        <td><strong>${escapeHtml(row.month)}</strong></td>
        <td>${fmtAED(row.budget_set)}</td>
        <td>${fmtAED(row.actual_spend)}</td>
        <td class="${row.variance < 0 ? "neg" : ""}">${fmtAED(row.variance)}</td>
        <td>${fmtAED(row.rollover_amount)}</td>
        <td>${fmtAED(row.savings_amount)}</td>
        <td class="${row.negative_cascade > 0 ? "neg" : ""}">${fmtAED(row.negative_cascade)}</td>
      </tr>`).join("")}</tbody>
    </table>`;
  }

  function downloadExport(kind) {
    const from = document.getElementById("exp-from").value;
    const to = document.getElementById("exp-to").value;
    const qs = new URLSearchParams();
    if (from) qs.set("from", from);
    if (to) qs.set("to", to);
    window.location.href = `/api/reports/export/${kind}?${qs}`;
  }

  window.Reports = { render };
})();
