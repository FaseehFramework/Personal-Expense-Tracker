/* SPA shell — Phase 2: live dashboard/transactions/budget; placeholders for the rest. */
(function () {
  "use strict";

  const appEl = document.getElementById("app");
  const role = appEl.dataset.role;
  const isAdmin = role === "admin";
  window.App = { role, isAdmin, username: appEl.dataset.username };

  // ---------- theme ----------
  if (localStorage.getItem("theme") === "dark") document.documentElement.dataset.theme = "dark";
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("theme", next);
  });

  // ---------- onboarding ----------
  if (appEl.dataset.needsOnboarding === "1") {
    showOnboarding();
  } else if (appEl.dataset.awaitingAdmin === "1") {
    document.getElementById("view").innerHTML = `
      <div class="neo-card empty-state">
        <h2>Setup pending</h2><p>An administrator still needs to complete first-launch onboarding.</p>
      </div>`;
    return;
  }

  function showOnboarding() {
    const modal = document.getElementById("onboarding-modal");
    modal.classList.remove("hidden");
    document.getElementById("onboarding-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = document.getElementById("onboarding-error"); err.textContent = "";
      const fd = new FormData(e.currentTarget);
      const body = {
        opening_bank: parseFloat(fd.get("opening_bank")) || 0,
        opening_petty: parseFloat(fd.get("opening_petty")) || 0,
        monthly_budget: parseFloat(fd.get("monthly_budget")) || 0,
      };
      const r = await window.U.api("/api/onboarding/complete", { method: "POST", body });
      if (!r.ok) { err.textContent = r.data?.error || "Could not complete"; return; }
      modal.classList.add("hidden");
      route();
    });
  }

  // ---------- tab routing ----------
  const TABS = {
    dashboard:    { title: "Dashboard",            mod: () => window.Dashboard },
    transactions: { title: "Transactions",         mod: () => window.Transactions },
    budget:       { title: "Budget",               mod: () => window.BudgetView },
    loans:        { title: "Loans & Receivables", mod: () => window.Loans },
    wishlist:     { title: "Wishlist",             mod: () => window.Wishlist },
    reports:      { title: "Reports",              mod: () => window.Reports },
    audit:        { title: "Audit Log",            mod: () => window.AuditLog },
    settings:     { title: "Settings",             mod: () => window.Settings },
  };

  function placeholder(heading, body) {
    return { render(c) { c.innerHTML = `<div class="neo-card placeholder"><div><h2>${heading}</h2><p class="muted">${body}</p></div></div>`; } };
  }

  function route() {
    if (appEl.dataset.needsOnboarding === "1") return; // wait until they finish
    const hash = window.location.hash.replace(/^#\//, "") || "dashboard";
    const tabKey = TABS[hash] ? hash : "dashboard";
    const tab = TABS[tabKey];
    document.getElementById("page-title").textContent = tab.title;
    document.querySelectorAll(".nav-item").forEach((el) => {
      el.classList.toggle("active", el.dataset.tab === tabKey);
    });
    const view = document.getElementById("view");
    view.innerHTML = "";
    const mod = tab.mod();
    if (mod && mod.render) mod.render(view);
    else view.innerHTML = `<div class="neo-card empty-state">Module not loaded.</div>`;
  }

  window.addEventListener("hashchange", route);
  if (!window.location.hash) window.location.hash = "#/dashboard";
  route();
})();
