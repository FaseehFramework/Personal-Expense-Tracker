/* Loans & Receivables tab. Per §8.3 we have three sections:
   - They Owe Me  (loans direction='owed')
   - I Owe Them   (loans direction='owe')
   - Receivables  (reimbursements pending) */
(function () {
  "use strict";
  const { api, fmtAED, fmtDate, escapeHtml, toast } = window.U;

  async function render(container) {
    container.innerHTML = `<div class="muted">Loading…</div>`;
    const [loansR, recR] = await Promise.all([api("/api/loans"), api("/api/receivables")]);
    if (!loansR.ok || !recR.ok) {
      container.innerHTML = `<div class="neo-card empty-state">Failed to load.</div>`;
      return;
    }
    const owedToMe = loansR.data.loans.filter(l => l.direction === "owed");
    const iOwe = loansR.data.loans.filter(l => l.direction === "owe");

    container.innerHTML = `
      <div class="neo-card">
        <div class="topbar">
          <h2>They owe me</h2>
          <div class="topbar-spacer"></div>
          <span class="muted">${fmtAED(loansR.data.summary.owed_to_me_outstanding)} outstanding</span>
          ${window.App.isAdmin ? `<button class="neo-btn" id="btn-new-owed">+ New</button>` : ""}
        </div>
        <div id="owed-list">${renderLoanList(owedToMe, "owed")}</div>
      </div>

      <div class="neo-card">
        <div class="topbar">
          <h2>I owe them</h2>
          <div class="topbar-spacer"></div>
          <span class="muted">${fmtAED(loansR.data.summary.i_owe_outstanding)} outstanding</span>
          ${window.App.isAdmin ? `<button class="neo-btn" id="btn-new-owe">+ New</button>` : ""}
        </div>
        <div id="owe-list">${renderLoanList(iOwe, "owe")}</div>
      </div>

      <div class="neo-card">
        <div class="topbar">
          <h2>Receivables</h2>
          <div class="topbar-spacer"></div>
          <span class="muted">${fmtAED(recR.data.totals.outstanding)} outstanding</span>
          ${window.App.isAdmin ? `<button class="neo-btn" id="btn-new-rec">+ Log receivable</button>` : ""}
        </div>
        <div id="rec-list">${renderReceivableList(recR.data.receivables)}</div>
      </div>`;

    document.querySelectorAll("[data-loan]").forEach(b => {
      b.addEventListener("click", () => openLoanDetail(parseInt(b.dataset.loan, 10), () => render(container)));
    });
    document.querySelectorAll("[data-rec]").forEach(b => {
      b.addEventListener("click", () => openReceivableActions(parseInt(b.dataset.rec, 10), () => render(container)));
    });

    if (window.App.isAdmin) {
      document.getElementById("btn-new-owed")?.addEventListener("click", () => promptNewLoan("owed", () => render(container)));
      document.getElementById("btn-new-owe")?.addEventListener("click", () => promptNewLoan("owe", () => render(container)));
      document.getElementById("btn-new-rec")?.addEventListener("click", () => promptNewReceivable(() => render(container)));
    }
  }

  function renderLoanList(items, direction) {
    if (!items.length) return `<p class="muted">No ${direction === "owed" ? "outstanding receivables" : "debts"} recorded.</p>`;
    return items.map(l => `
      <button class="loan-row neo-inset" data-loan="${l.id}" style="display:flex;flex-direction:column;align-items:stretch;gap:8px;padding:14px 16px;width:100%;text-align:left;font:inherit;color:inherit;border:none;border-radius:14px;cursor:pointer;margin-bottom:10px;">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px">
          <strong>${escapeHtml(l.party_description)}</strong>
          <span>${fmtAED(l.remaining)} / ${fmtAED(l.total_amount)}</span>
        </div>
        <div class="progress"><div class="progress-fill" style="width:${l.progress_pct}%"></div></div>
        <div class="muted" style="font-size:0.85rem">${fmtDate(l.date)} • status: ${l.status}</div>
      </button>`).join("");
  }

  function renderReceivableList(items) {
    if (!items.length) return `<p class="muted">No receivables logged.</p>`;
    return `<table class="table-receivables" style="width:100%;border-collapse:collapse">
      <thead><tr><th>Description</th><th>Amount</th><th>Date</th><th>Month</th><th>Status</th><th>Actions</th></tr></thead>
      <tbody>
        ${items.map(r => `<tr>
          <td>${escapeHtml(r.description)}</td>
          <td>${fmtAED(r.amount)}</td>
          <td>${fmtDate(r.date_logged)}</td>
          <td>${escapeHtml(r.month)}</td>
          <td><span class="badge">${r.status}</span></td>
          <td>${r.status === 'outstanding' && window.App.isAdmin
            ? `<button class="neo-btn neo-btn-ghost" data-rec="${r.id}">Manage</button>`
            : `<span class="muted">—</span>`}</td>
        </tr>`).join("")}
      </tbody>
    </table>`;
  }

  // ---------- Loan detail / payment ----------
  async function openLoanDetail(loanId, onChange) {
    const r = await api(`/api/loans/${loanId}`);
    if (!r.ok) { toast("Could not load", "error"); return; }
    const l = r.data.loan;
    const payments = r.data.payments;
    const isOwed = l.direction === "owed";
    const payLabel = isOwed ? "Repayment received" : "Repayment made";

    const m = window.U.modal(`
      <h2>${escapeHtml(l.party_description)}</h2>
      <p class="muted">${isOwed ? "Owes me" : "I owe"} ${fmtAED(l.total_amount)} • Status: ${l.status}</p>
      <div class="progress" style="margin:10px 0"><div class="progress-fill" style="width:${l.progress_pct}%"></div></div>
      <p>${fmtAED(l.paid)} paid • ${fmtAED(l.remaining)} remaining</p>
      ${l.notes ? `<p>${escapeHtml(l.notes)}</p>` : ""}

      ${window.App.isAdmin && l.status !== "settled" ? `
      <h3>Add ${payLabel}</h3>
      <form id="pay-form" class="grid-2">
        <label>Date <input class="neo-input" type="date" name="date" value="${new Date().toISOString().slice(0,10)}" required></label>
        <label>Amount (AED) <input class="neo-input" type="number" min="0.01" step="0.01" max="${(l.remaining/100).toFixed(2)}" name="amount" required></label>
        <label>Source <select class="neo-input" name="source"><option value="bank">Bank</option><option value="petty">Petty</option></select></label>
        <label>Notes <input class="neo-input" name="notes"></label>
        <button type="submit" class="neo-btn neo-btn-primary" style="grid-column:1/-1">Record</button>
      </form>` : ""}

      <h3 style="margin-top:14px">Payment history</h3>
      ${payments.length ? `<table style="width:100%;border-collapse:collapse">
        <thead><tr><th>Date</th><th>Amount</th><th>Remaining after</th>${window.App.isAdmin ? "<th></th>" : ""}</tr></thead>
        <tbody>${payments.map(p => `<tr>
          <td>${fmtDate(p.date)}</td>
          <td>${fmtAED(p.amount)}</td>
          <td>${fmtAED(p.remaining_after)}</td>
          ${window.App.isAdmin ? `<td><button class="neo-btn neo-btn-ghost btn-del-pay" data-pid="${p.id}">×</button></td>` : ""}
        </tr>`).join("")}</tbody>
      </table>` : `<p class="muted">No payments recorded.</p>`}

      <div class="form-actions">
        ${window.App.isAdmin ? `<button class="neo-btn neo-btn-danger" id="btn-del-loan">Delete loan</button>` : ""}
        <button class="neo-btn" id="btn-close">Close</button>
      </div>`);
    document.getElementById("btn-close").addEventListener("click", m.close);
    document.getElementById("pay-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      const rr = await api(`/api/loans/${loanId}/payments`, { method: "POST", body: {
        date: f.date.value, amount: window.U.moneyStr(f.amount), source: f.source.value, notes: f.notes.value,
      }});
      if (!rr.ok) { toast(rr.data?.error || "Failed", "error"); return; }
      m.close(); toast("Payment recorded"); if (onChange) onChange();
    });
    document.querySelectorAll(".btn-del-pay").forEach(b => b.addEventListener("click", async () => {
      if (!confirm("Delete this payment? The linked transaction will also be soft-deleted.")) return;
      const rr = await api(`/api/loans/${loanId}/payments/${b.dataset.pid}`, { method: "DELETE" });
      if (!rr.ok) { toast("Failed", "error"); return; }
      m.close(); toast("Deleted"); if (onChange) onChange();
    }));
    document.getElementById("btn-del-loan")?.addEventListener("click", async () => {
      if (!confirm("Delete this entire loan and its linked transactions?")) return;
      const rr = await api(`/api/loans/${loanId}`, { method: "DELETE" });
      if (!rr.ok) { toast("Failed", "error"); return; }
      m.close(); toast("Deleted"); if (onChange) onChange();
    });
  }

  function promptNewLoan(direction, onSaved) {
    const today = new Date().toISOString().slice(0,10);
    const label = direction === "owed" ? "I lent money to..." : "I borrowed from...";
    const m = window.U.modal(`
      <h2>${label}</h2>
      ${direction === "owed" ? "<p class='muted'>This creates an off-budget outflow from the source wallet.</p>" : "<p class='muted'>This only tracks the debt — log any cash you actually received as a separate income transaction.</p>"}
      <form id="loan-form" class="grid-2">
        <label>Party (name or description) <input class="neo-input" name="party_description" required></label>
        <label>Amount (AED) <input class="neo-input" type="number" min="0.01" step="0.01" name="amount" required></label>
        <label>Date <input class="neo-input" type="date" name="date" value="${today}" required></label>
        ${direction === "owed" ? `<label>Source wallet <select class="neo-input" name="source"><option value="bank">Bank</option><option value="petty">Petty</option></select></label>` : `<input type="hidden" name="source" value="bank">`}
        <label style="grid-column:1/-1">Notes <textarea class="neo-input" name="notes" rows="2"></textarea></label>
        <button type="submit" class="neo-btn neo-btn-primary" style="grid-column:1/-1">Create</button>
        <p class="form-error" id="loan-err" style="grid-column:1/-1"></p>
      </form>`);
    document.getElementById("loan-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      const r = await api("/api/loans", { method: "POST", body: {
        direction, party_description: f.party_description.value,
        amount: window.U.moneyStr(f.amount), date: f.date.value,
        source: f.source.value, notes: f.notes.value,
      }});
      if (!r.ok) { document.getElementById("loan-err").textContent = r.data?.error || "Failed"; return; }
      m.close(); toast("Loan created"); if (onSaved) onSaved();
    });
  }

  // ---------- Receivables ----------
  function promptNewReceivable(onSaved) {
    const today = new Date().toISOString().slice(0,10);
    const month = today.slice(0,7);
    const m = window.U.modal(`
      <h2>Log a receivable</h2>
      <p class="muted">Money you've spent that someone (typically your employer) will reimburse. Off-budget until settled or converted.</p>
      <form id="rec-form" class="grid-2">
        <label style="grid-column:1/-1">Description <input class="neo-input" name="description" required></label>
        <label>Amount (AED) <input class="neo-input" type="number" min="0.01" step="0.01" name="amount" required></label>
        <label>Date <input class="neo-input" type="date" name="date" value="${today}" required></label>
        <label>Month it belongs to <input class="neo-input" name="month" value="${month}" pattern="\\d{4}-\\d{2}" required></label>
        <button type="submit" class="neo-btn neo-btn-primary" style="grid-column:1/-1">Log</button>
        <p class="form-error" id="rec-err" style="grid-column:1/-1"></p>
      </form>`);
    document.getElementById("rec-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      const r = await api("/api/receivables", { method: "POST", body: {
        description: f.description.value, amount: window.U.moneyStr(f.amount),
        date: f.date.value, month: f.month.value,
      }});
      if (!r.ok) { document.getElementById("rec-err").textContent = r.data?.error || "Failed"; return; }
      m.close(); toast("Receivable logged"); if (onSaved) onSaved();
    });
  }

  async function openReceivableActions(rid, onChange) {
    const m = window.U.modal(`
      <h2>Receivable actions</h2>
      <p class="muted">Choose how to resolve this receivable.</p>
      <div class="form-actions" style="flex-direction:column;align-items:stretch;gap:8px">
        <button class="neo-btn neo-btn-primary" id="btn-settle-bank">Settle — money received into Bank</button>
        <button class="neo-btn neo-btn-primary" id="btn-settle-petty">Settle — money received into Petty</button>
        <button class="neo-btn neo-btn-danger" id="btn-convert">Convert to expense (won't be reimbursed)</button>
        <button class="neo-btn" id="btn-del">Delete record</button>
        <button class="neo-btn" id="btn-cancel">Cancel</button>
      </div>`);
    document.getElementById("btn-cancel").addEventListener("click", m.close);
    document.getElementById("btn-settle-bank").addEventListener("click", () => doSettle("bank"));
    document.getElementById("btn-settle-petty").addEventListener("click", () => doSettle("petty"));
    document.getElementById("btn-convert").addEventListener("click", doConvert);
    document.getElementById("btn-del").addEventListener("click", doDelete);

    async function doSettle(dest) {
      const r = await api(`/api/receivables/${rid}/settle`, { method: "POST", body: { destination: dest }});
      if (!r.ok) { toast(r.data?.error || "Failed", "error"); return; }
      m.close(); toast(`Settled into ${dest}`); if (onChange) onChange();
    }
    async function doConvert() {
      if (!confirm("Converting marks this as an expense in the month it belongs to. If that month is already closed, the cascade will be replayed forward. Continue?")) return;
      const r = await api(`/api/receivables/${rid}/convert`, { method: "POST" });
      if (!r.ok) { toast(r.data?.error || "Failed", "error"); return; }
      const msg = r.data.replayed_months.length
        ? `Converted; replayed: ${r.data.replayed_months.join(", ")}`
        : "Converted to expense";
      m.close(); toast(msg); if (onChange) onChange();
    }
    async function doDelete() {
      if (!confirm("Delete this receivable record? Only allowed while outstanding.")) return;
      const r = await api(`/api/receivables/${rid}`, { method: "DELETE" });
      if (!r.ok) { toast(r.data?.error || "Failed", "error"); return; }
      m.close(); toast("Deleted"); if (onChange) onChange();
    }
  }

  window.Loans = { render };
})();
