/* Tiny dependency-free SVG charts. Just enough for §11.2 (bar comparison)
   and §11.3 (period pie). All sizing is fluid via viewBox. */
(function () {
  "use strict";
  const { fmtAED, escapeHtml } = window.U;

  // Grouped vertical bar chart.
  // groups = [{label, bars: [{label, value, color}]}]
  function barGroup(groups, opts = {}) {
    if (!groups.length) return "";
    const W = 720, H = 320;
    const pad = { l: 60, r: 16, t: 16, b: 60 };
    const innerW = W - pad.l - pad.r;
    const innerH = H - pad.t - pad.b;
    const maxVal = Math.max(1, ...groups.flatMap(g => g.bars.map(b => b.value)));
    const groupW = innerW / groups.length;
    const barCount = groups[0].bars.length;
    const barW = Math.max(6, groupW / (barCount + 1));

    const yTicks = 4;
    const ticks = Array.from({length: yTicks + 1}, (_, i) => {
      const v = (maxVal * i) / yTicks;
      const y = pad.t + innerH - (innerH * i) / yTicks;
      return `
        <line x1="${pad.l}" x2="${W - pad.r}" y1="${y}" y2="${y}" stroke="currentColor" stroke-opacity="0.08"/>
        <text x="${pad.l - 8}" y="${y + 4}" text-anchor="end" font-size="10" fill="currentColor" fill-opacity="0.6">${fmtAED(Math.round(v))}</text>`;
    }).join("");

    const bars = groups.map((g, gi) => {
      const gx = pad.l + gi * groupW + (groupW - barW * barCount) / 2;
      const groupBars = g.bars.map((b, bi) => {
        const h = (b.value / maxVal) * innerH;
        const x = gx + bi * barW;
        const y = pad.t + innerH - h;
        return `
          <rect x="${x + 2}" y="${y}" width="${barW - 4}" height="${h}" rx="3" fill="${b.color || "var(--accent)"}">
            <title>${escapeHtml(b.label)}: ${fmtAED(b.value)}</title>
          </rect>`;
      }).join("");
      const labelX = gx + (barW * barCount) / 2;
      return groupBars + `<text x="${labelX}" y="${H - 30}" text-anchor="middle" font-size="11" fill="currentColor">${escapeHtml(g.label)}</text>`;
    }).join("");

    const legend = (groups[0].bars).map((b, i) => {
      const x = pad.l + i * 110;
      return `<g transform="translate(${x},${H - 14})">
        <rect width="10" height="10" rx="2" fill="${b.color || "var(--accent)"}"/>
        <text x="14" y="9" font-size="11" fill="currentColor">${escapeHtml(b.label)}</text>
      </g>`;
    }).join("");

    return `<svg viewBox="0 0 ${W} ${H}" class="chart-svg" preserveAspectRatio="xMidYMid meet">${ticks}${bars}${legend}</svg>`;
  }

  // Pie chart with legend.
  // slices = [{label, value, color}]
  function pie(slices, opts = {}) {
    if (!slices.length) return "";
    const total = slices.reduce((a, s) => a + s.value, 0);
    if (total <= 0) return `<p class="muted">No data.</p>`;
    const W = 360, H = 320, cx = 150, cy = 150, r = 130;
    const palette = ["#3d6ae6", "#2e8b57", "#c87f0a", "#a04fc1", "#c0392b", "#3a99b7", "#a07c2c", "#7e5fdc"];
    let acc = 0;
    const paths = slices.map((s, i) => {
      const start = (acc / total) * Math.PI * 2 - Math.PI / 2;
      acc += s.value;
      const end = (acc / total) * Math.PI * 2 - Math.PI / 2;
      const x1 = cx + r * Math.cos(start), y1 = cy + r * Math.sin(start);
      const x2 = cx + r * Math.cos(end),   y2 = cy + r * Math.sin(end);
      const large = (end - start) > Math.PI ? 1 : 0;
      const color = s.color || palette[i % palette.length];
      const d = `M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${large} 1 ${x2},${y2} Z`;
      return `<path d="${d}" fill="${color}"><title>${escapeHtml(s.label)}: ${fmtAED(s.value)} (${((s.value/total)*100).toFixed(1)}%)</title></path>`;
    }).join("");
    const legendItems = slices.map((s, i) => {
      const color = s.color || palette[i % palette.length];
      const pct = ((s.value / total) * 100).toFixed(1);
      return `<div class="legend-row">
        <span class="legend-swatch" style="background:${color}"></span>
        <span class="grow">${escapeHtml(s.label)}</span>
        <span class="muted">${fmtAED(s.value)} • ${pct}%</span>
      </div>`;
    }).join("");
    return `<div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap">
      <svg viewBox="0 0 ${W} ${H}" class="chart-svg" style="max-width:340px;flex:0 0 340px">${paths}</svg>
      <div class="legend grow" style="min-width:220px">${legendItems}</div>
    </div>`;
  }

  window.Charts = { barGroup, pie };
})();
