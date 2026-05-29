/* Transactions view. List grouped by day + filter bar + add/edit modal. */
(function () {
  "use strict";
  const { api, fmtAED, fmtDate, escapeHtml, debounce, toast } = window.U;

  let cachedCategories = null;
  let cachedTemplates = null;

  const TX_TYPE_LABELS = {
    income_bank: "Income (Bank)",
    income_petty_external: "Income (Petty — external)",
    petty_to_bank: "Petty → Bank deposit",
    expense: "Expense",
    transfer_bank_to_petty: "Transfer (Bank → Petty)",
    recurring: "Recurring (manual)",
    loan_repay_owed: "Loan repayment (I owe)",
    loan_lend: "Loan — lending out",
    loan_repay_received: "Loan repayment received",
    receivable: "Receivable (reimbursable spend)",
  };

  const TYPE_ICON = {
    expense: "−",
    recurring: "↻",
    income_bank: "+",
    income_petty_external: "+",
    petty_to_bank: "→",
    transfer_bank_to_petty: "⇄",
    loan_repay_owed: "↩",
    loan_lend: "→",
    loan_repay_received: "↩",
    receivable: "⟳",
  };

  async function ensureCategories() {
    if (cachedCategories) return cachedCategories;
    const r = await api("/api/transactions/categories");
    cachedCategories = r.ok ? r.data.categories : [];
    return cachedCategories;
  }

  async function ensureTemplates() {
    const r = await api("/api/templates");
    cachedTemplates = r.ok ? r.data.templates : [];
    return cachedTemplates;
  }

  async function render(container) {
    container.innerHTML = `
      <div class="neo-card filter-bar">
        <div class="filter-row">
          <input class="neo-input" id="f-q" placeholder="Search description or memo…">
          <select class="neo-input" id="f-type">
            <option value="">All types</option>
            ${Object.entries(TX_TYPE_LABELS).map(([k,v]) => `<option value="${k}">${v}</option>`).join("")}
          </select>
          <select class="neo-input" id="f-source">
            <option value="">All sources</option>
            <option value="bank">Bank</option>
            <option value="petty">Petty Cash</option>
          </select>
          <select class="neo-input" id="f-cat"><option value="">All categories</option></select>
        </div>
        <div class="filter-row">
          <input class="neo-input" id="f-from" type="date" placeholder="From">
          <input class="neo-input" id="f-to"   type="date" placeholder="To">
          <input class="neo-input" id="f-min"  type="number" step="0.01" placeholder="Min AED">
          <input class="neo-input" id="f-max"  type="number" step="0.01" placeholder="Max AED">
          <button class="neo-btn" id="btn-trash" type="button">Trash</button>
          <button class="neo-btn neo-btn-primary" id="btn-new" type="button">+ New</button>
        </div>
        <div class="filter-summary" id="filter-summary"></div>
      </div>
      <div class="quick-bar neo-card" id="quick-bar" style="display:none">
        <span class="muted">Quick add:</span>
        <div class="chips" id="quick-chips"></div>
      </div>
      <div id="tx-list"></div>`;

    // Populate category filter
    const cats = await ensureCategories();
    const catSel = document.getElementById("f-cat");
    cats.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c.id; opt.textContent = c.name;
      catSel.appendChild(opt);
    });

    const reload = debounce(() => loadList(false), 250);
    ["f-q","f-type","f-source","f-cat","f-from","f-to","f-min","f-max"].forEach(id => {
      const e = document.getElementById(id);
      e.addEventListener("input", reload);
      e.addEventListener("change", reload);
    });

    document.getElementById("btn-new").addEventListener("click", () => openForm(null, () => loadList(false)));

    const trashBtn = document.getElementById("btn-trash");
    let trashMode = false;
    trashBtn.addEventListener("click", () => {
      trashMode = !trashMode;
      trashBtn.classList.toggle("neo-btn-primary", trashMode);
      trashBtn.textContent = trashMode ? "Hide trash" : "Trash";
      loadList(trashMode);
    });

    // Quick-add templates
    if (window.App.isAdmin) {
      const tpls = await ensureTemplates();
      if (tpls.length) {
        document.getElementById("quick-bar").style.display = "";
        document.getElementById("quick-chips").innerHTML = tpls.map(t => `
          <button class="neo-btn chip" data-tpl='${escapeAttr(JSON.stringify(t))}'>
            ${escapeHtml(t.description)} • ${fmtAED(t.amount)}
          </button>`).join("");
        document.querySelectorAll(".chip").forEach(btn => {
          btn.addEventListener("click", () => {
            const tpl = JSON.parse(btn.dataset.tpl);
            openForm(null, () => loadList(false), { template: tpl });
          });
        });
      }
    }

    await loadList(false);

    async function loadList(includeTrash) {
      const params = new URLSearchParams();
      const map = { q:"f-q", type:"f-type", source:"f-source", category_id:"f-cat",
                    date_from:"f-from", date_to:"f-to", amount_min:"f-min", amount_max:"f-max" };
      for (const [k,id] of Object.entries(map)) {
        const v = document.getElementById(id).value;
        if (v) params.set(k, v);
      }
      if (includeTrash) params.set("include_deleted", "1");
      const r = await api(`/api/transactions?${params.toString()}`);
      const list = document.getElementById("tx-list");
      const sumEl = document.getElementById("filter-summary");
      if (!r.ok) { list.innerHTML = `<div class="neo-card empty-state">Failed to load.</div>`; return; }

      if (r.data.summary) {
        sumEl.textContent = `${r.data.summary.count} transactions — ${fmtAED(r.data.summary.total)} total`;
      } else { sumEl.textContent = ""; }

      if (!r.data.days.length) {
        list.innerHTML = `<div class="neo-card empty-state">No transactions${includeTrash ? " in trash" : " yet"}.</div>`;
        return;
      }
      list.innerHTML = r.data.days.map(day => `
        <section class="day-group">
          <header class="day-header">
            <span>${escapeHtml(fmtDate(day.date))}</span>
            <span class="muted">${fmtAED(day.subtotal)}</span>
          </header>
          <div class="day-items">
            ${day.items.map(it => renderRow(it, includeTrash)).join("")}
          </div>
        </section>
      `).join("");
      list.querySelectorAll("[data-tx]").forEach(el => {
        el.addEventListener("click", () => openDetail(parseInt(el.dataset.tx, 10), () => loadList(includeTrash)));
      });
    }
  }

  function renderRow(t, trashMode) {
    const icon = TYPE_ICON[t.type] || "·";
    const catBadge = t.category ? `<span class="badge">${escapeHtml(t.category.name)}</span>` : `<span class="badge muted">Uncategorised</span>`;
    const recBadge = t.type === "receivable" ? `<span class="badge badge-receivable">Receivable</span>` : "";
    return `<button class="tx-row neo-inset" data-tx="${t.id}">
      <span class="tx-icon" data-type="${t.type}">${icon}</span>
      <span class="tx-main">
        <span class="tx-desc">${escapeHtml(t.description)}</span>
        <span class="tx-meta">${catBadge} ${recBadge} <span class="pill pill-${t.source}">${t.source === "bank" ? "Bank" : "Petty"}</span>${t.attachment_path ? ' <span class="badge">📎</span>' : ""}${t.splits.length ? ` <span class="badge">${t.splits.length} splits</span>` : ""}</span>
      </span>
      <span class="tx-amt">${fmtAED(t.amount)}</span>
    </button>`;
  }

  function escapeAttr(s) { return s.replace(/'/g, "&#39;"); }

  // -------- Add/Edit form --------
  async function openForm(txId, onSaved, opts = {}) {
    const cats = await ensureCategories();
    const isEdit = !!txId;
    let initial = {
      date: new Date().toISOString().slice(0,10),
      type: "expense", source: "bank", amount: "", category_id: "",
      description: "", memo: "", splits: [],
    };

    let linkedMeta = null;  // {type, id} when editing a linked transaction
    if (isEdit) {
      const r = await api(`/api/transactions/${txId}`);
      if (!r.ok) { toast("Could not load transaction", "error"); return; }
      const tx = r.data.transaction;
      if (tx.linked_type) linkedMeta = { type: tx.linked_type, id: tx.linked_id };
      initial = {
        date: tx.date, type: tx.type, source: tx.source,
        amount: (tx.amount/100).toFixed(2),
        category_id: tx.category_id || "",
        description: tx.description, memo: tx.memo || "",
        splits: tx.splits.map(s => ({ category_id: s.category_id || "", amount: (s.amount/100).toFixed(2), memo: s.memo || "" })),
      };
    } else if (opts.template) {
      initial.amount = (opts.template.amount/100).toFixed(2);
      initial.source = opts.template.source;
      initial.category_id = opts.template.category_id || "";
      initial.description = opts.template.description;
      initial.type = "expense";
    }

    const lockedAttr = linkedMeta ? "disabled" : "";
    const linkedBanner = linkedMeta ? `
      <div class="linked-warn" style="margin-bottom:12px">
        This transaction is linked to a <strong>${escapeHtml(linkedMeta.type)}</strong>
        (#${linkedMeta.id}). To change <strong>amount</strong>, <strong>type</strong>, or
        <strong>source</strong>, edit the ${escapeHtml(linkedMeta.type)} record in its own tab.
        You can still update the date, category, description, memo, and attachment here.
      </div>` : "";

    const html = `
      <h2>${isEdit ? "Edit" : "New"} transaction</h2>
      ${linkedBanner}
      <form id="tx-form" enctype="multipart/form-data">
        <div class="grid-2">
          <label>Date <input class="neo-input" name="date" type="date" value="${initial.date}" required></label>
          <label>Type
            <select class="neo-input" name="type" required ${lockedAttr}>
              ${Object.entries(TX_TYPE_LABELS).map(([k,v]) =>
                `<option value="${k}" ${k===initial.type?'selected':''}>${v}</option>`).join("")}
            </select>
          </label>
          <label>Amount (AED)
            <input class="neo-input" name="amount" type="number" step="0.01" min="0.01" value="${initial.amount}" required ${lockedAttr}>
          </label>
          <label>Source
            <select class="neo-input" name="source" required ${lockedAttr}>
              <option value="bank" ${initial.source==='bank'?'selected':''}>Bank</option>
              <option value="petty" ${initial.source==='petty'?'selected':''}>Petty Cash</option>
            </select>
          </label>
          <label>Category
            <select class="neo-input" name="category_id">
              <option value="">Uncategorised</option>
              ${cats.map(c => `<option value="${c.id}" ${c.id==initial.category_id?'selected':''}>${escapeHtml(c.name)}</option>`).join("")}
            </select>
          </label>
          <label>Description
            <input class="neo-input" name="description" value="${escapeHtml(initial.description)}" required>
          </label>
        </div>
        <label>Memo <textarea class="neo-input" name="memo" rows="2">${escapeHtml(initial.memo)}</textarea></label>

        <div class="splits-section">
          <div class="splits-head">
            <strong>Split across categories</strong>
            <button type="button" class="neo-btn neo-btn-ghost" id="add-split">+ split</button>
          </div>
          <div id="splits-list"></div>
          <div class="muted small" id="splits-hint"></div>
        </div>

        <label>Attachment (JPG/PNG, max 5 MB)
          <input class="neo-input" type="file" name="attachment" accept="image/png,image/jpeg">
        </label>

        <div class="form-actions">
          <button type="button" class="neo-btn" id="cancel-btn">Cancel</button>
          <button type="submit" class="neo-btn neo-btn-primary">${isEdit ? "Save" : "Add"}</button>
        </div>
        <p class="form-error" id="form-error"></p>
      </form>`;
    const m = window.U.modal(html);

    const splits = [...initial.splits];
    renderSplits();

    function renderSplits() {
      const wrap = document.getElementById("splits-list");
      wrap.innerHTML = splits.map((s,i) => `
        <div class="split-row">
          <select class="neo-input" data-i="${i}" data-k="category_id">
            <option value="">Uncategorised</option>
            ${cats.map(c => `<option value="${c.id}" ${c.id==s.category_id?'selected':''}>${escapeHtml(c.name)}</option>`).join("")}
          </select>
          <input class="neo-input" data-i="${i}" data-k="amount" type="number" step="0.01" min="0.01" value="${s.amount}" placeholder="AED">
          <input class="neo-input" data-i="${i}" data-k="memo" value="${escapeHtml(s.memo)}" placeholder="Memo">
          <button type="button" class="neo-btn neo-btn-ghost" data-rm="${i}">×</button>
        </div>`).join("");
      wrap.querySelectorAll("[data-k]").forEach(inp => {
        inp.addEventListener("input", () => {
          splits[parseInt(inp.dataset.i,10)][inp.dataset.k] = inp.value;
          updateHint();
        });
      });
      wrap.querySelectorAll("[data-rm]").forEach(b => {
        b.addEventListener("click", () => { splits.splice(parseInt(b.dataset.rm,10),1); renderSplits(); });
      });
      updateHint();
    }
    function updateHint() {
      const total = splits.reduce((acc,s) => acc + (parseFloat(s.amount)||0), 0);
      const tx = parseFloat(document.querySelector("[name=amount]").value) || 0;
      document.getElementById("splits-hint").textContent =
        splits.length ? `Splits sum to AED ${total.toFixed(2)} — must equal transaction AED ${tx.toFixed(2)}` : "";
    }
    document.querySelector("[name=amount]").addEventListener("input", updateHint);
    document.getElementById("add-split").addEventListener("click", () => {
      splits.push({ category_id: "", amount: "", memo: "" });
      renderSplits();
    });
    document.getElementById("cancel-btn").addEventListener("click", m.close);

    document.getElementById("tx-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = e.currentTarget;
      const err = document.getElementById("form-error");
      err.textContent = "";
      const body = {
        date: f.date.value,
        type: f.type.value,
        source: f.source.value,
        amount: window.U.moneyStr(f.amount),
        category_id: f.category_id.value ? parseInt(f.category_id.value,10) : null,
        description: f.description.value.trim(),
        memo: f.memo.value.trim(),
        splits: splits.map(s => ({
          category_id: s.category_id ? parseInt(s.category_id,10) : null,
          amount: (s.amount ?? "").toString().trim() || "0",
          memo: s.memo,
        })),
      };
      const method = isEdit ? "PUT" : "POST";
      const url = isEdit ? `/api/transactions/${txId}` : "/api/transactions";

      let r = await api(url, { method, body });
      if (r.status === 409 && r.data.duplicate) {
        const ex = r.data.existing;
        const sure = confirm(`A similar transaction exists: ${ex.description} (${ex.amount_aed} on ${ex.date}). Add anyway?`);
        if (!sure) return;
        body.confirm_duplicate = true;
        r = await api(url, { method, body });
      }
      if (!r.ok) { err.textContent = r.data?.error || "Save failed"; return; }

      const newId = isEdit ? txId : r.data.transaction.id;

      // Attachment upload (after the row exists).
      const file = f.attachment.files[0];
      if (file) {
        const fd = new FormData(); fd.append("file", file);
        const up = await fetch(`/api/transactions/${newId}/attachment`, { method: "POST", body: fd });
        if (!up.ok) { toast("Attachment upload failed", "error"); }
      }

      if (!isEdit && r.data.offer_template) {
        if (confirm(`Save this as a quick-add template? (${body.description} — AED ${body.amount})`)) {
          await api("/api/templates", { method: "POST", body: {
            description: body.description, amount: body.amount,
            source: body.source, category_id: body.category_id,
          }});
          cachedTemplates = null;
        }
      }
      m.close();
      toast(isEdit ? "Saved" : "Added");
      if (onSaved) onSaved();
    });
  }

  // -------- Detail / edit-history / delete --------
  async function openDetail(txId, onChange) {
    const r = await api(`/api/transactions/${txId}`);
    if (!r.ok) { toast("Could not load", "error"); return; }
    const t = r.data.transaction;
    const cat = t.category ? t.category.name : "Uncategorised";
    const linked = t.linked_records || [];

    const splitsHTML = t.splits.length
      ? `<ul class="detail-splits">${t.splits.map(s => `<li>${escapeHtml(s.category_name || "Uncategorised")} — ${fmtAED(s.amount)}${s.memo ? ` • ${escapeHtml(s.memo)}` : ""}</li>`).join("")}</ul>`
      : "";
    const editsHTML = t.edits.length
      ? `<details class="edit-history"><summary>Edit history (${t.edits.length})</summary>
          <ul>${t.edits.map(e => `<li><span class="muted">${escapeHtml(e.changed_at)}</span> ${escapeHtml(e.field_name)}: <em>${escapeHtml(e.old_value)}</em> → <strong>${escapeHtml(e.new_value)}</strong> ${window.App.isAdmin ? `<button class="neo-btn neo-btn-ghost btn-revert" data-edit="${e.id}">revert</button>` : ""}</li>`).join("")}</ul>
        </details>` : "";
    const attachmentHTML = t.attachment_path
      ? `<img class="attachment-thumb" src="/api/transactions/${t.id}/attachment" alt="attachment">`
      : "";
    const linkedHTML = linked.length
      ? `<div class="linked-warn"><strong>Linked to:</strong> ${linked.map(l => `${l.kind} #${l.id}`).join(", ")}</div>` : "";
    const adminBtns = window.App.isAdmin && !t.is_deleted
      ? `<button class="neo-btn" id="btn-edit">Edit</button>
         <button class="neo-btn neo-btn-danger" id="btn-del">Delete</button>`
      : (t.is_deleted ? `<button class="neo-btn neo-btn-primary" id="btn-restore">Restore</button>` : "");

    const m = window.U.modal(`
      <h2>${escapeHtml(t.description)}</h2>
      <p class="muted">${fmtDate(t.date)} • ${TX_TYPE_LABELS[t.type] || t.type} • ${t.source === "bank" ? "Bank" : "Petty Cash"} • ${escapeHtml(cat)}</p>
      <div class="detail-amount">${fmtAED(t.amount)}</div>
      ${t.type === "receivable" ? `
        <div class="linked-warn receivable-note">
          ⟳ <strong>Receivable</strong> — this spend is off-budget and pending reimbursement.
          To settle or convert it to an expense, go to
          <a href="#/loans" class="tab-link" id="link-to-loans">Loans &amp; Receivables</a>.
        </div>` : ""}
      ${t.memo ? `<p>${escapeHtml(t.memo)}</p>` : ""}
      ${splitsHTML}
      ${attachmentHTML}
      ${linkedHTML}
      ${editsHTML}
      <div class="form-actions">
        ${adminBtns}
        <button class="neo-btn" id="btn-close">Close</button>
      </div>`);

    document.getElementById("btn-close").addEventListener("click", m.close);
    // Deep-link to Loans & Receivables tab from the receivable note.
    document.getElementById("link-to-loans")?.addEventListener("click", (e) => {
      e.preventDefault();
      m.close();
      window.location.hash = "#/loans";
    });


    const editBtn = document.getElementById("btn-edit");
    if (editBtn) editBtn.addEventListener("click", () => { m.close(); openForm(t.id, onChange); });
    const delBtn = document.getElementById("btn-del");
    if (delBtn) delBtn.addEventListener("click", async () => {
      let body = {};
      if (linked.length) {
        if (!confirm(`This transaction is linked to ${linked.length} record(s). Delete anyway?`)) return;
        body.confirm_linked = true;
      } else if (!confirm("Delete this transaction? (Recoverable from trash for 5 days.)")) return;
      const dr = await api(`/api/transactions/${t.id}`, { method: "DELETE", body });
      if (!dr.ok) { toast(dr.data?.error || "Delete failed", "error"); return; }
      m.close(); toast("Moved to trash"); if (onChange) onChange();
    });
    const restoreBtn = document.getElementById("btn-restore");
    if (restoreBtn) restoreBtn.addEventListener("click", async () => {
      const rr = await api(`/api/transactions/${t.id}/restore`, { method: "POST" });
      if (!rr.ok) { toast("Restore failed", "error"); return; }
      m.close(); toast("Restored"); if (onChange) onChange();
    });
    document.querySelectorAll(".btn-revert").forEach(b => {
      b.addEventListener("click", async () => {
        if (!confirm("Revert this field?")) return;
        const rr = await api(`/api/transactions/${t.id}/revert/${b.dataset.edit}`, { method: "POST" });
        if (!rr.ok) { toast(rr.data?.error || "Revert failed", "error"); return; }
        m.close(); openDetail(t.id, onChange);
      });
    });
  }

  window.Transactions = { render };
})();
