/* Audit Log tab — paginated, type filter, search + date filter. §12 */
(function () {
  "use strict";
  const { api, escapeHtml, debounce } = window.U;

  let offset = 0;
  const LIMIT = 50;

  async function render(container) {
    container.innerHTML = `
      <div class="neo-card filter-bar">
        <div class="filter-row">
          <input class="neo-input" id="aud-q" placeholder="Search description…">
          <select class="neo-input" id="aud-type"><option value="">All event types</option></select>
          <input class="neo-input" id="aud-from" type="date">
          <input class="neo-input" id="aud-to" type="date">
        </div>
        <div class="filter-summary" id="aud-summary"></div>
      </div>
      <div id="aud-list"></div>
      <div class="pager" id="aud-pager" style="display:flex;justify-content:center;gap:12px;padding:18px 0"></div>`;

    const reload = debounce(() => { offset = 0; load(); }, 250);
    ["aud-q","aud-type","aud-from","aud-to"].forEach(id => {
      document.getElementById(id).addEventListener("input", reload);
      document.getElementById(id).addEventListener("change", reload);
    });

    offset = 0;
    await load();
  }

  async function load() {
    const params = new URLSearchParams({
      limit: LIMIT, offset,
      q: document.getElementById("aud-q").value,
      type: document.getElementById("aud-type").value,
      from: document.getElementById("aud-from").value,
      to: document.getElementById("aud-to").value,
    });
    const r = await api(`/api/audit-log?${params}`);
    if (!r.ok) {
      document.getElementById("aud-list").innerHTML = `<div class="neo-card empty-state">Failed to load.</div>`;
      return;
    }

    // Refresh the type dropdown but preserve selection.
    const typeSel = document.getElementById("aud-type");
    const selected = typeSel.value;
    typeSel.innerHTML = `<option value="">All event types (${r.data.total})</option>` +
      r.data.types.map(t => `<option value="${t.event_type}" ${t.event_type === selected ? "selected" : ""}>${escapeHtml(t.event_type)} (${t.count})</option>`).join("");

    document.getElementById("aud-summary").textContent =
      `${r.data.total} events • showing ${r.data.events.length} starting at #${offset + 1}`;

    if (!r.data.events.length) {
      document.getElementById("aud-list").innerHTML =
        `<div class="neo-card empty-state">No events match your filters.</div>`;
    } else {
      document.getElementById("aud-list").innerHTML = `
        <div class="neo-card">
          <table class="audit-table" style="width:100%;border-collapse:collapse">
            <thead><tr><th>When</th><th>Type</th><th>Description</th><th>Linked</th></tr></thead>
            <tbody>
              ${r.data.events.map(e => `<tr>
                <td class="muted">${escapeHtml(e.created_at)}</td>
                <td><span class="badge">${escapeHtml(e.event_type)}</span></td>
                <td>${escapeHtml(e.description)}</td>
                <td class="muted">${e.related_type ? `${escapeHtml(e.related_type)} #${e.related_id ?? "—"}` : "—"}</td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>`;
    }

    const pager = document.getElementById("aud-pager");
    const hasNext = offset + LIMIT < r.data.total;
    pager.innerHTML = `
      <button class="neo-btn" id="aud-prev" ${offset === 0 ? "disabled" : ""}>← Prev</button>
      <span class="muted" style="align-self:center">page ${Math.floor(offset / LIMIT) + 1}</span>
      <button class="neo-btn" id="aud-next" ${!hasNext ? "disabled" : ""}>Next →</button>`;
    document.getElementById("aud-prev").addEventListener("click", () => { offset = Math.max(0, offset - LIMIT); load(); });
    document.getElementById("aud-next").addEventListener("click", () => { offset += LIMIT; load(); });
  }

  window.AuditLog = { render };
})();
