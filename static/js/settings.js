/* Settings tab — passwords, categories, templates, theme info, version. §17 */
(function () {
  "use strict";
  const { api, escapeHtml, fmtAED, toast } = window.U;

  async function render(container) {
    container.innerHTML = `<div class="muted">Loading…</div>`;
    const [verR, catR, tplR, accR] = await Promise.all([
      api("/api/settings/version"),
      api("/api/settings/categories"),
      api("/api/templates"),
      window.App.isAdmin ? api("/api/settings/accounts") : Promise.resolve({ ok: true, data: { accounts: [] } }),
    ]);

    const adminBlock = window.App.isAdmin ? `
      <div class="neo-card">
        <h2>Accounts</h2>
        <p class="muted">Change a stored password (admin only). Both accounts are managed here.</p>
        <form id="pw-form" class="grid-2">
          <label>Account
            <select class="neo-input" name="target_username">
              ${accR.data.accounts.map(a => `<option value="${escapeHtml(a.username)}">${escapeHtml(a.username)} (${a.role})</option>`).join("")}
            </select>
          </label>
          <label>New password (≥6 chars)
            <input class="neo-input" type="password" name="new_password" minlength="6" autocomplete="new-password" required>
          </label>
          <button type="submit" class="neo-btn neo-btn-primary" style="grid-column:1/-1">Update password</button>
          <p class="form-error" id="pw-err" style="grid-column:1/-1"></p>
        </form>
      </div>` : "";

    container.innerHTML = `
      ${adminBlock}
      <div class="neo-card">
        <h2>Categories</h2>
        ${window.App.isAdmin ? `
          <form id="cat-form" style="display:flex;gap:8px;margin-bottom:14px">
            <input class="neo-input" name="name" placeholder="New category name" required>
            <button type="submit" class="neo-btn neo-btn-primary">Add</button>
          </form>` : ""}
        <div id="cat-list">${renderCategories(catR.ok ? catR.data.categories : [])}</div>
      </div>

      <div class="neo-card">
        <h2>Quick-add templates</h2>
        <p class="muted">Templates appear as one-tap buttons in the Transactions tab. New ones are auto-suggested after 3 identical entries.</p>
        <div id="tpl-list">${renderTemplates(tplR.ok ? tplR.data.templates : [])}</div>
      </div>

      <div class="neo-card">
        <h2>Theme</h2>
        <p class="muted">Use the moon icon in the sidebar to toggle dark mode. Stored in this browser's localStorage.</p>
      </div>

      <div class="neo-card">
        <h2>About</h2>
        <p class="muted">Version: <strong>${escapeHtml(verR.ok ? verR.data.version : "—")}</strong></p>
        <p class="muted">Signed in as <strong>${escapeHtml(window.App.username)}</strong> (${escapeHtml(window.App.role)}).</p>
      </div>`;

    if (window.App.isAdmin) {
      document.getElementById("pw-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = e.currentTarget;
        const err = document.getElementById("pw-err"); err.textContent = "";
        const r = await api("/api/settings/change-password", { method: "POST", body: {
          target_username: f.target_username.value, new_password: f.new_password.value,
        }});
        if (!r.ok) { err.textContent = r.data?.error || "Failed"; return; }
        f.new_password.value = "";
        toast("Password updated");
      });

      document.getElementById("cat-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = e.currentTarget;
        const r = await api("/api/settings/categories", { method: "POST", body: { name: f.name.value }});
        if (!r.ok) { toast(r.data?.error || "Failed", "error"); return; }
        f.name.value = "";
        toast("Category added"); render(container);
      });

      bindCategoryActions(container);
      bindTemplateActions(container);
    }
  }

  function renderCategories(cats) {
    const active = cats.filter(c => !c.is_deleted);
    const deleted = cats.filter(c => c.is_deleted);
    if (!active.length && !deleted.length) return `<p class="muted">No categories.</p>`;
    return `<table style="width:100%;border-collapse:collapse">
      <thead><tr><th>Name</th><th>Origin</th>${window.App.isAdmin ? "<th></th>" : ""}</tr></thead>
      <tbody>
        ${active.map(c => `<tr>
          <td><strong>${escapeHtml(c.name)}</strong></td>
          <td class="muted">${c.is_default ? "default" : "custom"}</td>
          ${window.App.isAdmin ? `<td>
            <button class="neo-btn neo-btn-ghost btn-rename" data-id="${c.id}" data-name="${escapeHtml(c.name)}">Rename</button>
            <button class="neo-btn neo-btn-ghost btn-del-cat" data-id="${c.id}" data-name="${escapeHtml(c.name)}">Delete</button>
          </td>` : ""}
        </tr>`).join("")}
        ${deleted.map(c => `<tr style="opacity:0.5">
          <td><span style="text-decoration:line-through">${escapeHtml(c.name)}</span></td>
          <td class="muted">deleted (transactions preserved)</td>
          ${window.App.isAdmin ? "<td></td>" : ""}
        </tr>`).join("")}
      </tbody>
    </table>`;
  }

  function renderTemplates(tpls) {
    if (!tpls.length) return `<p class="muted">No templates yet.</p>`;
    return `<table style="width:100%;border-collapse:collapse">
      <thead><tr><th>Description</th><th>Amount</th><th>Source</th><th>Category</th>${window.App.isAdmin ? "<th></th>" : ""}</tr></thead>
      <tbody>
        ${tpls.map(t => `<tr>
          <td>${escapeHtml(t.description)}</td>
          <td>${fmtAED(t.amount)}</td>
          <td>${t.source}</td>
          <td>${escapeHtml(t.category_name || "Uncategorised")}</td>
          ${window.App.isAdmin ? `<td><button class="neo-btn neo-btn-ghost btn-del-tpl" data-id="${t.id}">Delete</button></td>` : ""}
        </tr>`).join("")}
      </tbody>
    </table>`;
  }

  function bindCategoryActions(container) {
    container.querySelectorAll(".btn-rename").forEach(b => b.addEventListener("click", async () => {
      const newName = prompt(`Rename '${b.dataset.name}' to:`, b.dataset.name);
      if (!newName || newName === b.dataset.name) return;
      const r = await api(`/api/settings/categories/${b.dataset.id}`, { method: "PUT", body: { name: newName }});
      if (!r.ok) { toast(r.data?.error || "Failed", "error"); return; }
      toast("Renamed"); render(container);
    }));
    container.querySelectorAll(".btn-del-cat").forEach(b => b.addEventListener("click", async () => {
      if (!confirm(`Delete '${b.dataset.name}'? If any transactions reference it, the category is soft-deleted (data preserved).`)) return;
      const r = await api(`/api/settings/categories/${b.dataset.id}`, { method: "DELETE" });
      if (!r.ok) { toast(r.data?.error || "Failed", "error"); return; }
      toast(`${r.data.action} category`); render(container);
    }));
  }
  function bindTemplateActions(container) {
    container.querySelectorAll(".btn-del-tpl").forEach(b => b.addEventListener("click", async () => {
      if (!confirm("Delete this template?")) return;
      const r = await api(`/api/templates/${b.dataset.id}`, { method: "DELETE" });
      if (!r.ok) { toast("Failed", "error"); return; }
      toast("Deleted"); render(container);
    }));
  }

  window.Settings = { render };
})();
