/* Metals dashboard front end. Vanilla JS + Chart.js. All data from /api/*. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
let CFG = { display_tz: "UTC" };
const charts = {};
const fmt = {
  n: (v, d = 2) => (v == null || isNaN(v)) ? "n/a" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }),
  i: (v) => (v == null || isNaN(v)) ? "n/a" : Math.round(v).toLocaleString("en-US"),
  pct: (v, d = 2) => (v == null || isNaN(v)) ? "n/a" : (v >= 0 ? "+" : "") + Number(v).toFixed(d) + "%",
  sgn: (v, d = 2) => (v == null || isNaN(v)) ? "n/a" : (v >= 0 ? "+" : "") + Number(v).toFixed(d),
  t: (iso) => { if (!iso) return "n/a"; try { const d = new Date(iso.length === 10 ? iso + "T00:00:00Z" : iso); if (iso.length === 10) return iso;
      return d.toLocaleString("en-US", { timeZone: CFG.display_tz, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" }); } catch { return iso; } },
  age: (min) => min == null ? "" : min < 90 ? `${Math.round(min)} min ago` : min < 2880 ? `${(min / 60).toFixed(1)} h ago` : `${Math.round(min / 1440)} d ago`,
};
const cls = (v, thr = 0) => v == null ? "flat" : v > thr ? "up" : v < -thr ? "down" : "flat";
const ord = (v) => { if (v == null || isNaN(v)) return "n/a"; const n = Math.round(v), s = ["th", "st", "nd", "rd"], k = n % 100; return n + (s[(k - 20) % 10] || s[k] || s[0]); };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let EXPORTED = !!window.__EXPORT__ || !!window.__REMOTE_DATA__;
let DATA_STAMP = window.__EXPORT_STAMP__ || "";
let DATA_MODE = window.__EXPORT__ ? "saved" : "none";
async function loadRemote() {
  /* Saved/hosted pages: try the published data.json first (fresh), fall back to the embedded copy. */
  const url = window.__REMOTE_DATA__; if (!url) return false;
  try {
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), 12000);
    const r = await fetch(url + (url.includes("?") ? "&" : "?") + "t=" + Date.now(), { cache: "no-store", signal: ctl.signal });
    clearTimeout(t);
    if (!r.ok) return false;
    const d = await r.json();
    if (!d || !d["/api/config"]) return false;
    window.__EXPORT__ = d; DATA_STAMP = d._stamp || DATA_STAMP; DATA_MODE = "live"; EXPORTED = true;
    return true;
  } catch { return false; }
}
async function api(path) {
  if (EXPORTED) {
    if (!window.__EXPORT__) throw new Error("no data: the published data could not be fetched and nothing is embedded");
    const X = window.__EXPORT__;
    if (path in X) return X[path];
    const base = path.split("?")[0];
    const alt = Object.keys(X).find(k => k.split("?")[0] === base);
    if (alt) return X[alt];
    throw new Error("not included in this saved snapshot");
  }
  const r = await fetch(path); if (!r.ok) throw new Error(`${path}: ${r.status}`); return r.json();
}
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

/* ---------- theme / nav ---------- */
(function initTheme() {
  const saved = (() => { try { return localStorage.getItem("theme"); } catch { return null; } })();
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme").onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch {}
    Object.values(charts).forEach(c => { c.options.scales.x.ticks.color = css("--muted"); c.options.scales.y.ticks.color = css("--muted"); c.update(); });
  };
})();
const loaded = {};
function showTab(name) {
  $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.tab === name));
  $$(".tab").forEach(s => s.classList.toggle("hidden", s.id !== `tab-${name}`));
  if (!loaded[name]) { loaded[name] = true; (TABS[name] || (() => {}))().then(() => glance(name)).catch(e => { $(`#tab-${name}`).innerHTML = `<div class="card bad">Failed to load: ${esc(e.message)}</div>`; }); }
}
async function glance(name) {
  const host = $(`#tab-${name}`);
  if (!host) return;
  let box = $(".glance", host);
  if (!box) { box = document.createElement("div"); box.className = "glance"; box.innerHTML = `<div class="glance-h"><i>✦</i> At a glance</div><p class="muted">Summarising…</p>`; host.prepend(box); }
  try {
    const s = await api(`/api/snapshot/${name}`);
    box.innerHTML = `<div class="glance-h"><i>✦</i> At a glance <span class="muted small">plain-English read of this tab, generated from the same data</span></div><p>${s.sentences.map(esc).join(" ")}</p>`;
  } catch (e) { box.innerHTML = `<div class="glance-h">At a glance</div><p class="muted">Summary unavailable (${esc(e.message)}).</p>`; }
}
window.addEventListener("hashchange", () => showTab(location.hash.slice(1) || "overview"));

/* ---------- charts ---------- */
function lineChart(id, series, opts = {}) {
  const el = document.getElementById(id); if (!el) return;
  if (charts[id]) charts[id].destroy();
  const muted = css("--muted"), line = css("--line");
  /* Align every series on the union of timestamps (category axis), so multi-series charts with different
     dates still line up; missing values are gaps. */
  const labels = [...new Set(series.flatMap(s => s.points.map(p => p.t)))].sort();
  const fmtLabel = (t) => opts.intraday ? fmt.t(t).replace(/,? \d{4}/, "") : String(t).slice(0, 10);
  const datasets = series.map((s, i) => {
    const m = new Map(s.points.map(p => [p.t, p.v]));
    return { label: s.label, data: labels.map(t => (m.has(t) && m.get(t) != null) ? m.get(t) : null),
      borderColor: s.color || [css("--au"), css("--ag"), css("--info"), css("--mixed")][i % 4],
      borderWidth: 1.6, pointRadius: 0, pointHitRadius: 6, tension: 0.15, yAxisID: s.axis || "y", fill: false, spanGaps: true, stepped: s.stepped || false };
  });
  charts[id] = new Chart(el, {
    type: "line",
    data: { labels, datasets },
    options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false }, normalized: true,
      plugins: { legend: { display: series.length > 1, labels: { color: muted, boxWidth: 10 } },
        tooltip: { callbacks: { title: (it) => fmtLabel(labels[it[0].dataIndex]), label: (c) => `${c.dataset.label}: ${c.parsed.y == null ? "n/a" : Number(c.parsed.y).toLocaleString("en-US", { maximumFractionDigits: 2 })}` } } },
      scales: { x: { ticks: { color: muted, maxTicksLimit: 8, maxRotation: 0, autoSkip: true, callback: (v, i) => fmtLabel(labels[i]) }, grid: { color: line } },
        y: { ticks: { color: muted }, grid: { color: line }, position: "left" },
        ...(series.some(s => s.axis === "y1") ? { y1: { position: "right", ticks: { color: muted }, grid: { drawOnChartArea: false } } } : {}) } }
  });
}

/* ---------- Overview ---------- */
function changesRow(ch) {
  const keys = [["1d", "1 day"], ["1w", "1 week"], ["1m", "1 month"], ["3m", "3 months"], ["ytd", "YTD"], ["1y", "1 year"]];
  return `<div class="changes">${keys.map(([k, l]) => { const c = ch?.[k]; return `<div><span>${l}${c ? ` · from ${c.base_ts}` : ""}</span>${c ? `<b class="chg ${cls(c.pct)}">${fmt.pct(c.pct)}</b> <span class="muted" style="display:inline">${fmt.sgn(c.abs)}</span>` : "n/a"}</div>`; }).join("")}</div>`;
}
function provRow(p, extra = "") {
  return `<div class="prov">${esc(p.name || p.series_id)} · <b>${esc(p.instrument_type || "")}</b>${p.contract ? ` · contract <b>${esc(p.contract)}</b>` : ""} · source ${esc(p.source || "")} · ${esc(p.delay_label || "")}
    · quote ${fmt.t(p.quote_ts)}${p.age_minutes != null ? ` (${fmt.age(p.age_minutes)})` : ""}${extra}</div>`;
}
async function tabOverview() {
  const o = await api("/api/overview");
  const s = o.session;
  $("#session-pill").textContent = s.open ? "COMEX Globex open" : "COMEX Globex closed";
  $("#session-pill").className = "pill " + (s.open ? "open" : "closed");
  $("#session-pill").title = s.rule + " · now " + s.now_et + " ET";
  const metalCard = (m, name, sym) => {
    const c = m.changes, p = m.provenance, il = m.intraday_last, f = m.front;
    const c1 = c.changes?.["1d"];
    return `<div class="card">
      <h2><span class="dot ${sym}"></span>${name} <span class="muted small">COMEX front-month futures, USD/troy oz</span></h2>
      <div class="price">${fmt.n(c.last)}<small>$/oz · ${c.last_ts}</small></div>
      <div class="chg ${cls(c1?.pct)}" style="font-size:18px">${fmt.sgn(c1?.abs)} (${fmt.pct(c1?.pct)}) <span class="muted small">vs prior daily close ${c1 ? c1.base_ts : ""}</span></div>
      ${changesRow(c.changes)}
      <div class="kv">
        <span class="k">Last 5-min bar</span><span>${il ? `${fmt.n(il.value)} at ${fmt.t(il.ts)}` : "n/a (market closed or not collected yet)"}</span>
        <span class="k">Front contract</span><span>${f ? `${esc(f.meta?.short_name || "")} (${esc(f.meta?.contract || "")}) · expires ${esc(f.meta?.expire || "?")} · OI ${fmt.i(f.value)} contracts (Yahoo, indicative)` : "n/a"}</span>
        <span class="k">Realized vol (ann.)</span><span>10d ${fmt.n(m.realized_vol["10d"], 1)}% · 21d ${fmt.n(m.realized_vol["21d"], 1)}% · 63d ${fmt.n(m.realized_vol["63d"], 1)}%</span>
      </div>
      <div class="ranges" data-chart="${sym}-chart" data-series="${sym === "au" ? "gold" : "silver"}">
        ${["1d", "5d", "1m", "6m", "1y", "5y", "max"].map(r => `<button data-range="${r}" class="${r === "6m" ? "active" : ""}">${r}</button>`).join("")}
      </div>
      <div class="chart-wrap"><canvas id="${sym}-chart"></canvas></div>
      ${provRow(p)}
    </div>`;
  };
  const r = o.ratio;
  const rel = o.related;
  const relRow = (id, label, unit, d = 2, note = "") => { const x = rel[id]; if (!x || !x.changes.available) return ""; const c = x.changes.changes; const c1 = c?.["1d"];
    const isPct = ["dxy", "copper_fut", "wti_fut", "spx", "gld", "slv", "iau", "paxg", "tip_etf", "dtwexbgs", "stak_spot_gold_daily", "stak_spot_silver_daily"].includes(id);
    return `<tr><td>${esc(label)}<div class="muted small">${esc(note || x.provenance.name)}</div></td><td class="num">${fmt.n(x.changes.last, d)}</td>
      <td class="num chg ${cls(isPct ? c1?.pct : c1?.abs)}">${isPct ? fmt.pct(c1?.pct) : fmt.sgn(c1?.abs, d) + (unit === "pct" ? " pp" : "")}</td>
      <td class="num chg ${cls(isPct ? c?.["1w"]?.pct : c?.["1w"]?.abs)}">${isPct ? fmt.pct(c?.["1w"]?.pct) : fmt.sgn(c?.["1w"]?.abs, d)}</td>
      <td class="num chg ${cls(isPct ? c?.ytd?.pct : c?.ytd?.abs)}">${isPct ? fmt.pct(c?.ytd?.pct) : fmt.sgn(c?.ytd?.abs, d)}</td><td class="muted small">${x.changes.last_ts}</td></tr>`; };
  $("#tab-overview").innerHTML = `
    <div class="grid c2">${metalCard(o.metals.gold, "Gold", "au")}${metalCard(o.metals.silver, "Silver", "ag")}</div>
    <div class="grid c2" style="margin-top:14px">
      <div class="card"><h2>Gold / silver ratio <span class="muted small">continuous futures, same-day closes</span></h2>
        ${r.available ? `<div class="price">${fmt.n(r.value, 1)}<small>prior ${fmt.n(r.prev, 1)} · ${r.ts}</small></div>
        <div class="kv"><span class="k">Percentile</span><span>1y ${ord(r.percentile_1y)} · 5y ${ord(r.percentile_5y)} · 10y ${ord(r.percentile_10y)}</span>
        <span class="k">5-year range</span><span>${fmt.n(r.stats_5y.min, 1)} – ${fmt.n(r.stats_5y.max, 1)} (mean ${fmt.n(r.stats_5y.mean, 1)})</span>
        <span class="k">10-year range</span><span>${fmt.n(r.stats_10y.min, 1)} – ${fmt.n(r.stats_10y.max, 1)} (mean ${fmt.n(r.stats_10y.mean, 1)})</span></div>
        <div class="chart-wrap"><canvas id="ratio-chart"></canvas></div>
        <div class="prov">Ratio of gold to silver continuous-futures closes. A low percentile means silver is expensive relative to gold by the standards of the window; it says nothing by itself about where the ratio goes next.</div>` : "n/a"}
      </div>
      <div class="card"><h2>Related markets <span class="muted small">daily closes; levels and changes</span></h2>
        <table><thead><tr><th>Market</th><th class="num">Last</th><th class="num">1d</th><th class="num">1w</th><th class="num">YTD</th><th>As of</th></tr></thead><tbody>
          ${relRow("dxy", "US dollar — ICE DXY", "index", 2, "ICE DXY (euro-heavy); not the Fed broad index")}
          ${relRow("dtwexbgs", "US dollar — Fed broad trade-weighted", "index", 2, "FRED DTWEXBGS; different construction from DXY, published with a lag")}
          ${relRow("dgs10", "10y Treasury nominal yield (FRED)", "pct", 2) || relRow("ust_10y", "10y Treasury nominal yield (Treasury)", "pct", 2) || relRow("us10y", "10y Treasury yield (CBOE ^TNX)", "pct", 2)}
          ${relRow("dfii10", "10y real yield (TIPS, FRED)", "pct", 2) || relRow("ust_real_10y", "10y real yield (TIPS par, Treasury)", "pct", 2)}
          ${relRow("t10yie", "10y breakeven inflation (FRED)", "pct", 2) || relRow("ust_be_10y", "10y breakeven (derived: Treasury nominal − real)", "pct", 2)}
          ${relRow("dff", "Fed funds effective (FRED)", "pct", 2) || relRow("nyfed_effr", "Fed funds effective (NY Fed)", "pct", 2)}
          ${relRow("copper_fut", "Copper futures (COMEX)", "USD/lb", 3)}
          ${relRow("wti_fut", "WTI crude futures (NYMEX)", "USD/bbl", 2)}
          ${relRow("spx", "S&P 500", "index", 0)}
          ${relRow("vix", "VIX", "index", 2)}
          ${relRow("gld", "GLD (gold ETF proxy, price)", "USD", 2)}
          ${relRow("slv", "SLV (silver ETF proxy, price)", "USD", 2)}
          ${relRow("paxg", "PAXG (tokenized gold)", "USD/oz", 2)}
          ${relRow("stak_spot_gold_daily", "Gold spot — aggregated (StakTrakr)", "USD/oz", 2, "third-party aggregate; last print of each UTC day")}
          ${relRow("stak_spot_silver_daily", "Silver spot — aggregated (StakTrakr)", "USD/oz", 2, "third-party aggregate; last print of each UTC day")}
        </tbody></table>
        <div class="prov">Yields and real yields come from FRED (H.15 constant-maturity) when reachable, otherwise the Treasury par curves (see the Status tab). Each row is its own series; nothing is spliced.</div>
      </div>
    </div>
    <div class="card" style="margin-top:14px"><h2>Definitions</h2><div class="small">
      <b>Spot vs futures vs ETF.</b> The headline numbers are COMEX front-month futures (delayed, Yahoo Finance), not London spot or the LBMA benchmark. ETF prices (GLD/SLV/IAU) are share prices that embed fees and trade only in US equity hours. The StakTrakr figure is a third-party aggregation of spot quotes with no published methodology.
      <b>Rollover.</b> The continuous series switches contract on Yahoo's own schedule; the contract in force is recorded daily and switches are flagged on the Positioning tab. History is not back-adjusted.
      <b>"Today".</b> Change vs the previous daily close of the same series; during a session the current bar updates intraday. Times shown in ${esc(CFG.display_tz)}.
    </div></div>`;
  // charts
  const load = async (sym, series, range) => {
    const intraday = range === "1d" || range === "5d";
    const sid = intraday ? `${series}_fut_5m` : `${series}_fut_cont`;
    const d = await api(`/api/series/${sid}?range=${range}`);
    lineChart(`${sym}-chart`, [{ label: d.series.name, points: d.points, color: sym === "au" ? css("--au") : css("--ag") }], { intraday });
  };
  $$(".ranges").forEach(rg => rg.onclick = (e) => { const b = e.target.closest("button"); if (!b) return; $$("button", rg).forEach(x => x.classList.toggle("active", x === b)); load(rg.dataset.chart.split("-")[0], rg.dataset.series, b.dataset.range); });
  await load("au", "gold", "6m"); await load("ag", "silver", "6m");
  const rr = await api("/api/ratio?range=5y");
  lineChart("ratio-chart", [{ label: "Gold/silver ratio", points: rr.points, color: css("--info") }]);
}

/* ---------- Today (briefing) ---------- */
async function tabToday() {
  const list = await api("/api/briefings");
  const render = async (date) => {
    const b = await api(`/api/briefing${date ? `?date=${date}` : ""}`);
    if (!b.available) { $("#brief-body").innerHTML = `<div class="card">No briefing for ${esc(date)}.</div>`; return; }
    const li = (arr, f) => arr.length ? `<ul>${arr.map(f).join("")}</ul>` : `<p class="muted">none</p>`;
    const interp = (m) => { const x = b.interpretations[m]; if (!x) return ""; return `<div class="card"><h2><span class="dot ${m === "gold" ? "au" : "ag"}"></span>${m[0].toUpperCase() + m.slice(1)}</h2>
      <p>${esc(x.text)}</p>
      ${x.aligned?.length ? `<h3>Pointing the same way</h3>${li(x.aligned, a => `<li><b>${esc(a.driver)}</b> — ${esc(a.stance)} · ${esc(a.kind)} · evidence ${esc(a.evidence_strength || "")}${a.delta_1d != null ? ` · Δ1d ${fmt.sgn(a.delta_1d)} ${esc(a.unit || "")}` : ""}<details><summary>mechanism</summary>${esc(a.mechanism)}</details></li>`)}` : ""}
      ${x.opposed?.length ? `<h3>Working against it</h3>${li(x.opposed, a => `<li><b>${esc(a.driver)}</b> — ${esc(a.stance)}${a.delta_1d != null ? ` · Δ1d ${fmt.sgn(a.delta_1d)} ${esc(a.unit || "")}` : ""}</li>`)}` : ""}
      ${x.context?.length ? `<details><summary>Context (slow-moving or stale, not today's explanation)</summary>${li(x.context, a => `<li>${esc(a.driver)} — ${esc(a.stance)} · ${esc(a.kind)} · ${esc(a.freshness)}</li>`)}</details>` : ""}
    </div>`; };
    $("#brief-body").innerHTML = `
      <div class="banner">Briefing for <b>${esc(b.date)}</b> (${esc(b.tz)}) generated ${fmt.t(b.generated_at)} · rules version ${esc(b.analytics_version)} · ${esc(b.language_note)}</div>
      <div class="grid c2">
        <div class="card brief"><h2>Observed facts</h2>${li(b.facts, f => `<li>${esc(f.text)} <span class="tag">${esc(f.source)}</span></li>`)}
          <h2 style="margin-top:12px">Top developments</h2>${b.developments.length ? `<ol>${b.developments.map(d => `<li>${d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.text)}</a>` : esc(d.text)} <span class="muted small">${esc(d.publisher || "")}${d.independent_publishers ? ` · ${d.independent_publishers} independent publisher(s)` : ""}${d.note ? ` · ${esc(d.note)}` : ""}</span></li>`).join("")}</ol>` : `<p class="muted">No qualifying items in the last 36 hours.</p>`}
        </div>
        <div class="card brief"><h2>Conflicting signals</h2>${li(b.conflicts, c => `<li><b>${esc(c.metal)}</b>: ${esc(c.text)}</li>`)}
          <h2 style="margin-top:12px">Unknowns / missing</h2>${li(b.unknowns, u => `<li>${esc(u)}</li>`)}
          <h2 style="margin-top:12px">Upcoming (14 days)</h2>${li(b.upcoming, u => `<li class="imp${u.importance}">${esc(u.when)} — ${esc(u.title)}</li>`)}
        </div>
      </div>
      <div class="grid c2" style="margin-top:14px">${interp("gold")}${interp("silver")}</div>
      <div class="grid c2" style="margin-top:14px">
        <div class="card brief"><h2>Evidence</h2>${li(b.evidence, e => `<li>${e.source_url ? `<a href="${esc(e.source_url)}" target="_blank" rel="noopener">${esc(e.claim)}</a>` : esc(e.claim)} <span class="tag">${esc(e.label)}</span> <span class="muted small">${esc(e.publisher || "")}${e.independent_publishers ? ` · ${e.independent_publishers} publisher(s)` : ""}</span></li>`)}</div>
        <div class="card brief"><h2>Versus previous briefing ${b.previous_date ? `<span class="muted small">(${esc(b.previous_date)})</span>` : ""}</h2>
          <h3>New</h3>${li(b.diff.new || [], x => `<li>${esc(x)}</li>`)}<h3>Changed</h3>${li(b.diff.changed || [], x => `<li>${esc(x)}</li>`)}
          <h3>Still unresolved</h3>${li(b.diff.unresolved || [], x => `<li>${esc(x)}</li>`)}${b.diff.note ? `<p class="muted small">${esc(b.diff.note)}</p>` : ""}</div>
      </div>`;
  };
  $("#tab-today").innerHTML = `<div class="filters"><label class="muted small">Archive: <select id="brief-date"><option value="">latest</option>${list.briefings.map(x => `<option value="${x.date}">${x.date}${x.versions > 1 ? ` (${x.versions} versions)` : ""}</option>`).join("")}</select></label>
    <button id="brief-regen" class="pill" style="cursor:pointer">Regenerate now</button></div><div id="brief-body"></div>`;
  $("#brief-date").onchange = (e) => render(e.target.value);
  $("#brief-regen").onclick = async () => { $("#brief-regen").textContent = "…"; await fetch("/api/jobs/briefing/run", { method: "POST" }); $("#brief-regen").textContent = "Regenerate now"; render(""); };
  await render("");
}

/* ---------- Drivers ---------- */
async function tabDrivers() {
  const d = await api("/api/drivers");
  const row = (x) => `<div class="driver">
    <div class="head"><b>${esc(x.name)}</b> <span class="tag ${x.kind}">${esc(x.kind)}</span> <span class="tag ${x.freshness}">${esc(x.freshness)}${x.observation?.age_days != null ? ` · ${x.observation.age_days}d` : ""}</span>
      ${x.stance_gold ? `<span class="stance ${x.stance_gold}">gold: ${x.stance_gold}</span>` : ""} ${x.stance_silver ? `<span class="stance ${x.stance_silver}">silver: ${x.stance_silver}</span>` : ""}</div>
    <div class="kv" style="margin:4px 0"><span class="k">Latest</span><span>${fmt.n(x.observation?.last, 2)} ${esc(x.observation?.unit || "")} · ${esc(x.observation?.last_ts || "")} <span class="muted small">(${esc(x.observation?.source || "")})</span></span>
      <span class="k">Change</span><span>${x.delta_1d != null ? `1d ${fmt.sgn(x.delta_1d)} ${esc(x.unit)}` : ""}${x.delta_1w != null ? ` · 1w ${fmt.sgn(x.delta_1w)} ${esc(x.unit)}` : ""}${x.extra ? ` · ${esc(Object.entries(x.extra).filter(([k, v]) => v != null).map(([k, v]) => `${k} ${typeof v === "number" ? fmt.n(v, k.includes("pct") || k.includes("percentile") ? 0 : 0) : v}`).join(" · "))}` : ""}</span>
      <span class="k">Evidence</span><span>${esc(x.evidence?.strength || "")}${x.evidence?.recent_corr_60d != null ? ` · 60d corr ${fmt.n(x.evidence.recent_corr_60d, 2)}` : ""}${x.evidence?.recent_beta_60d != null ? ` · 60d beta ${fmt.n(x.evidence.recent_beta_60d, 2)}` : ""}${x.evidence?.note ? ` · ${esc(x.evidence.note)}` : ""}</span></div>
    <div class="mech">${esc(x.mechanism)}</div></div>`;
  const groups = {};
  d.drivers.forEach(x => (groups[x.group] = groups[x.group] || []).push(x));
  const names = { macro: "Macro", risk: "Risk & stress", liquidity: "Liquidity", positioning: "Positioning (weekly)", flows: "Investment flows", "silver-industrial": "Silver industrial", physical: "Curve & physical" };
  $("#tab-drivers").innerHTML = `<div class="banner">Stances are rule-based reads of the latest data, not forecasts. <b>catalyst</b> = ${esc(d.classes.catalyst)}; <b>condition</b> = ${esc(d.classes.condition)}; <b>background</b> = ${esc(d.classes.background)}. <b>mixed</b> = ${esc(d.legend.mixed)}. Generated ${fmt.t(d.generated_at)}.</div>
    <div class="grid c2">${Object.entries(groups).map(([g, xs]) => `<div class="card"><h2>${esc(names[g] || g)}</h2>${xs.map(row).join("")}</div>`).join("")}</div>`;
}

/* ---------- Positioning & flows ---------- */
async function tabPositioning() {
  const el = $("#tab-positioning");
  el.innerHTML = `<div class="filters"><label class="muted small">Metal <select id="cot-metal"><option value="gold">Gold</option><option value="silver">Silver</option></select></label>
    <label class="muted small">Report <select id="cot-type"><option value="disagg_fut">Disaggregated · futures only</option><option value="disagg_futopt">Disaggregated · futures + options</option><option value="legacy_fut">Legacy · futures only</option><option value="legacy_futopt">Legacy · futures + options</option></select></label></div>
    <div class="grid c2"><div class="card" id="cot-card"></div><div class="card" id="curve-card"></div></div>
    <div class="grid c2" style="margin-top:14px"><div class="card" id="etf-card"></div><div class="card" id="oi-card"></div></div>`;
  const renderCot = async () => {
    const metal = $("#cot-metal").value, rt = $("#cot-type").value;
    const c = await api(`/api/cot?metal=${metal}&report_type=${rt}&weeks=260`);
    if (!c.available) { $("#cot-card").innerHTML = `<h2>COT</h2><p class="muted">No data.</p>`; return; }
    const k = c.context, dis = rt.startsWith("disagg");
    $("#cot-card").innerHTML = `<h2>CFTC positioning — ${esc(c.contract)} <span class="muted small">${esc(c.label)} · code ${esc(c.contract_code)}</span></h2>
      <div class="price">${fmt.i(k.value)}<small>${dis ? "managed-money" : "non-commercial"} net (contracts) · as of Tue ${esc(k.report_date)}</small></div>
      <div class="kv"><span class="k">Week-on-week</span><span class="chg ${cls(k.change_wow)}">${fmt.sgn(k.change_wow, 0)}</span>
        <span class="k">Percentile</span><span>1y ${ord(k.percentile_1y)} · 3y ${ord(k.percentile_3y)} · 5y ${ord(k.percentile_5y)}</span>
        <span class="k">Open interest</span><span>${fmt.i(k.open_interest)} (${fmt.sgn(k.oi_change_wow, 0)} w/w)</span>
        ${dis ? `<span class="k">MM share of OI</span><span>long ${fmt.n(k.mm_long_share_pct, 1)}% · short ${fmt.n(k.mm_short_share_pct, 1)}%</span>` : ""}
        <span class="k">Retrieved</span><span>${fmt.t(k.retrieved_at)}</span></div>
      <div class="chart-wrap tall"><canvas id="cot-chart"></canvas></div>
      <div class="prov">${esc(c.notes)}</div>`;
    const key = dis ? "mm_net" : "noncomm_net";
    const series = [{ label: dis ? "Managed money net" : "Non-commercial net", points: c.history.map(h => ({ t: h.report_date, v: h[key] })), color: css("--au") },
      { label: dis ? "Producer/merchant + swap net" : "Commercial net", points: c.history.map(h => ({ t: h.report_date, v: dis ? h.comm_like_net : h.comm_net })), color: css("--ag") },
      { label: "Open interest", points: c.history.map(h => ({ t: h.report_date, v: h.open_interest })), color: css("--info"), axis: "y1" }];
    lineChart("cot-chart", series);
  };
  $("#cot-metal").onchange = renderCot; $("#cot-type").onchange = renderCot;
  await renderCot();
  const cv = await api("/api/curve");
  $("#curve-card").innerHTML = `<h2>Futures curve <span class="muted small">listed contract months, latest daily close</span></h2>
    ${["gold", "silver"].map(m => { const c = cv[m]; if (!c || !c.points.length) return `<p class="muted">${m}: no curve data yet.</p>`;
      return `<h3>${m} · ${c.state || "n/a"} · active→next spread ${fmt.n(c.annualized_carry_pct, 2)}% annualized · continuous symbol = ${esc(c.continuous_contract || "?")}</h3>
      <table><thead><tr><th>Contract</th><th>Month</th><th class="num">Close</th><th class="num">vs active</th><th class="num">%</th><th class="num">Volume</th><th>As of</th></tr></thead><tbody>
      ${c.points.map(p => `<tr><td>${esc(p.contract)}${p.active ? ' <span class="tag catalyst">active</span>' : ""}</td><td>${esc(p.month)}</td><td class="num">${fmt.n(p.close)}</td><td class="num">${fmt.sgn(p.spread_vs_front)}</td><td class="num">${fmt.pct(p.spread_pct)}</td><td class="num">${fmt.i(p.volume)}</td><td class="muted small">${esc(p.ts)}</td></tr>`).join("")}</tbody></table>
      ${c.recent_rolls?.length ? `<div class="small muted">Recent continuous-series rolls: ${c.recent_rolls.map(r => esc(r.ts)).join(", ")}</div>` : ""}`; }).join("")}
    <div class="prov">${esc(cv.gold?.roll_method || "")}</div>`;
  const ev = await api("/api/etf?days=365");
  $("#etf-card").innerHTML = `<h2>Physically-backed ETF holdings <span class="muted small">ounces in trust (issuer pages)</span></h2>
    ${Object.entries(ev.funds).map(([sid, f]) => { if (!f.available) return `<p class="muted">${esc(f.series?.name || sid)}: not collected.</p>`;
      return `<div class="kv" style="margin-bottom:8px"><span class="k">${esc(f.series.name)}</span><span><b>${fmt.i(f.last.value)}</b> oz · as of ${esc(f.last.ts)} ${f.last.meta?.tonnes ? `· ${fmt.n(f.last.meta.tonnes, 1)} t` : ""} ${f.last.meta?.shares ? `· ${fmt.i(f.last.meta.shares)} shares` : ""}</span>
        <span class="k">Implied net metal change</span><span>last obs ${fmt.sgn(f.delta_1obs, 0)} oz · 7d ${fmt.sgn(f.delta_7d, 0)} · 30d ${fmt.sgn(f.delta_30d, 0)} <span class="muted small">(${f.n_obs} observations so far)</span></span></div>`; }).join("")}
    <div class="chart-wrap"><canvas id="etf-chart"></canvas></div>
    <div class="prov">${esc(ev.notes)} GLD is missing until its data URL is recovered (Status tab).</div>`;
  const fs = Object.entries(ev.funds).filter(([, f]) => f.available);
  if (fs.length) lineChart("etf-chart", fs.map(([sid, f], i) => ({ label: f.series.name, points: f.points, axis: i === 0 ? "y" : "y1" })));
  const oiG = await api("/api/series/gold_front_oi?range=1y"), oiS = await api("/api/series/silver_front_oi?range=1y");
  $("#oi-card").innerHTML = `<h2>Front-month open interest <span class="muted small">daily snapshot from Yahoo quote summary; indicative</span></h2><div class="chart-wrap"><canvas id="oi-chart"></canvas></div>
    <div class="prov">Open interest as reported by Yahoo for the contract the continuous symbol points at; it resets when that contract rolls. Official OI is in CME's daily bulletin (not collected: cmegroup.com blocks scripts). Weekly OI from the COT report is on the chart above.</div>`;
  lineChart("oi-chart", [{ label: "Gold front OI", points: oiG.points, color: css("--au") }, { label: "Silver front OI", points: oiS.points, color: css("--ag"), axis: "y1" }]);
}

/* ---------- Physical ---------- */
async function tabPhysical() {
  const p = await api("/api/physical"), pr = await api("/api/premiums");
  const comex = (m) => { const c = p.comex[m]; if (!c.available) return `<div class="card"><h2>COMEX ${m} warehouse stocks</h2><p class="muted">${esc(c.status)}</p><p class="small">${esc(p.definitions.pipeline)}</p></div>`;
    const t = c.total || {};
    return `<div class="card"><h2>COMEX ${m} warehouse stocks <span class="tag ${c.stale ? "stale" : "fresh"}">${esc(c.status)}</span></h2>
      <div class="kv"><span class="k">Report date</span><span>${esc(c.report_date)} (${c.age_days}d old)</span>
      <span class="k">Registered</span><span>${fmt.i(t.registered)} oz (${fmt.sgn(t.registered_chg, 0)})</span><span class="k">Eligible</span><span>${fmt.i(t.eligible)} oz (${fmt.sgn(t.eligible_chg, 0)})</span>
      <span class="k">Total</span><span>${fmt.i(t.total)} oz (${fmt.sgn(t.total_chg, 0)})</span>
      <span class="k">Front OI × size ÷ registered</span><span>${fmt.n(c.front_oi_oz_over_registered, 2)}× <span class="muted small">${esc(c.cover_note)}</span></span></div>
      <details><summary>By depository</summary><table><thead><tr><th>Depository</th><th class="num">Registered</th><th class="num">Eligible</th><th class="num">Total</th><th class="num">Δ total</th></tr></thead><tbody>
      ${c.depositories.map(d => `<tr><td>${esc(d.depository)}</td><td class="num">${fmt.i(d.registered)}</td><td class="num">${fmt.i(d.eligible)}</td><td class="num">${fmt.i(d.total)}</td><td class="num">${fmt.sgn(d.total_chg, 0)}</td></tr>`).join("")}</tbody></table></details>
      <div class="chart-wrap"><canvas id="comex-${m}"></canvas></div></div>`; };
  $("#tab-physical").innerHTML = `<div class="banner">${esc(p.definitions.caveat)}</div>
    <div class="grid c2">${comex("gold")}${comex("silver")}</div>
    <div class="card" style="margin-top:14px"><h2>Definitions</h2><div class="small"><b>Registered:</b> ${esc(p.definitions.registered)} <b>Eligible:</b> ${esc(p.definitions.eligible)}</div></div>
    <div class="card" style="margin-top:14px"><h2>Dealer premiums <span class="muted small">median dealer ask vs aggregated spot (StakTrakr feed)</span></h2>
      ${pr.items.length ? `<table><thead><tr><th>Product</th><th>Metal</th><th class="num">Size (oz)</th><th class="num">Median ask</th><th class="num">Low</th><th class="num">High</th><th class="num">Per oz</th><th class="num">Premium vs spot</th><th>Cheapest dealer</th><th>As of</th></tr></thead><tbody>
      ${pr.items.map(i => { const v = i.vendors?.[0]; return `<tr><td>${esc(i.product)}</td><td>${esc(i.metal)}</td><td class="num">${fmt.n(i.oz, 2)}</td><td class="num">${fmt.n(i.median)}</td><td class="num">${fmt.n(i.low)}</td><td class="num">${fmt.n(i.high)}</td><td class="num">${fmt.n(i.per_oz)}</td><td class="num"><b>${fmt.pct(i.premium_pct_vs_spot, 1)}</b></td><td>${v ? `${esc(v.vendor)} ${fmt.n(v.price)}${v.in_stock === false ? " (out of stock)" : ""}` : "<span class='muted'>detail not fetched</span>"}</td><td class="muted small">${fmt.t(i.ts)}</td></tr>`; }).join("")}</tbody></table>` : `<p class="muted">No retail data yet.</p>`}
      <div class="prov">${esc(pr.notes)}</div></div>`;
  ["gold", "silver"].forEach(m => { const c = p.comex[m]; if (c.available && c.history?.length) lineChart(`comex-${m}`, [{ label: "Registered", points: c.history.map(h => ({ t: h.report_date, v: h.registered })) }, { label: "Eligible", points: c.history.map(h => ({ t: h.report_date, v: h.eligible })) }, { label: "Total", points: c.history.map(h => ({ t: h.report_date, v: h.total })) }]); });
}

/* ---------- News ---------- */
async function tabNews() {
  const el = $("#tab-news");
  el.innerHTML = `<div class="filters">
    <select id="nf-metal"><option value="">All metals</option><option value="gold">Gold</option><option value="silver">Silver</option></select>
    <select id="nf-kind"><option value="">All kinds</option><option value="official">Official</option><option value="research">Research</option><option value="reporting">Reporting</option><option value="opinion">Opinion</option><option value="forecast">Forecast</option></select>
    <select id="nf-topic"><option value="">All topics</option>${["fed", "inflation", "employment", "dollar", "yields", "central-banks", "etf-flows", "comex-physical", "positioning", "industrial", "mine-supply", "recycling", "jewelry", "regional-demand", "geopolitics", "trade"].map(t => `<option>${t}</option>`).join("")}</select>
    <input id="nf-source" placeholder="source contains…" size="16"><input id="nf-since" type="date">
    <label class="muted small"><input type="checkbox" id="nf-cluster" checked> cluster duplicates</label></div><div class="card" id="news-list"></div>`;
  const render = async () => {
    const q = new URLSearchParams(); ["metal", "kind", "topic", "source"].forEach(k => { const v = $(`#nf-${k}`).value; if (v) q.set(k, v); });
    const since = $("#nf-since").value; if (since) q.set("since", since);
    q.set("clustered", $("#nf-cluster").checked); q.set("limit", "120");
    const n = await api(`/api/news?${q}`);
    $("#news-list").innerHTML = n.items.length ? n.items.map(i => `<div class="news-item"><div class="t"><a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.title)}</a></div>
      <div class="m">${esc(i.publisher || "")} · ${fmt.t(i.published_at || i.ingested_at)} · <span class="tag">${esc(i.kind)}</span>${i.metals.map(m => `<span class="tag">${esc(m)}</span>`).join("")}${i.topics.map(t => `<span class="tag">${esc(t)}</span>`).join("")}
        ${i.headline_only ? `<span class="tag">headline only</span>` : ""} · score ${fmt.n(i.score, 1)}${i.cluster_size > 1 ? ` · <b>${i.cluster_size} items, ${i.independent_publishers} independent publisher(s)</b>` : ""}</div>
      ${i.summary ? `<div class="small" style="margin-top:3px">${esc(i.summary)}</div>` : ""}
      ${i.also_reported_by?.length ? `<details><summary>also reported by ${i.also_reported_by.length}</summary>${i.also_reported_by.map(o => `<div class="small"><a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.title)}</a> — ${esc(o.publisher || "")}</div>`).join("")}</details>` : ""}</div>`).join("")
      : `<p class="muted">No items match.</p>`;
  };
  $$("#tab-news select, #tab-news input").forEach(x => x.onchange = render);
  await render();
}

/* ---------- Events ---------- */
async function tabEvents() {
  const e = await api("/api/events?days_ahead=60&days_back=7");
  $("#tab-events").innerHTML = `<div class="card"><h2>Scheduled events <span class="muted small">times in ${esc(CFG.display_tz)}</span></h2>
    ${e.events.map(x => `<div class="ev ${x.is_past ? "past" : ""} imp${x.importance}"><span>${esc(x.display_time)}</span><span>${esc(x.title)} <span class="tag">${esc(x.category)}</span>${x.notes ? `<span class="muted small"> — ${esc(x.notes)}</span>` : ""}${x.actual ? ` · actual ${esc(x.actual)}` : ""}${x.consensus ? ` · consensus ${esc(x.consensus)}` : ` <span class="muted small">· consensus unavailable</span>`}</span><span>${x.source_url ? `<a href="${esc(x.source_url)}" target="_blank" rel="noopener">source</a>` : ""}</span></div>`).join("")}
    <div class="prov">${esc(e.notes)}</div></div>`;
}

/* ---------- Fundamentals ---------- */
async function tabFundamentals() {
  const f = await api("/api/fundamentals");
  const block = (m) => `<div class="card"><h2><span class="dot ${m === "gold" ? "au" : "ag"}"></span>${m[0].toUpperCase() + m.slice(1)} — annual / quarterly</h2>
    <table><thead><tr><th>Metric</th><th>Period</th><th class="num">Value</th><th class="num">Prev period</th><th>Published</th><th>Source</th></tr></thead><tbody>
    ${f[m].map(x => x.available ? `<tr><td>${esc(x.label)}</td><td>${esc(x.latest.period)}</td><td class="num"><b>${fmt.n(x.latest.value, 1)}</b> ${esc(x.latest.unit || "")}</td><td class="num">${x.previous ? `${fmt.n(x.previous.value, 1)} <span class="muted small">(${esc(x.previous.period)})</span>` : ""}</td><td>${esc(x.latest.published_at || "")} <span class="muted small">${x.published_age_days != null ? `${x.published_age_days}d ago` : ""}</span></td><td class="small">${x.latest.source_url ? `<a href="${esc(x.latest.source_url)}" target="_blank" rel="noopener">${esc(x.latest.source)}</a>` : esc(x.latest.source || "")}${x.latest.notes ? `<div class="muted">${esc(x.latest.notes)}</div>` : ""}</td></tr>` : `<tr><td>${esc(x.label)}</td><td colspan="5" class="muted">not available from a primary source yet</td></tr>`).join("")}</tbody></table></div>`;
  $("#tab-fundamentals").innerHTML = `<div class="banner">${esc(f.notes)}</div><div class="grid c2">${block("gold")}${block("silver")}</div>`;
}

/* ---------- Research ---------- */
async function tabResearch() {
  const r = await api("/api/research");
  const h1 = r.h1_gold_vs_real_yield, h2 = r.h2_gold_vs_dxy, h3 = r.h3_silver_fair_value;
  const regime = (h) => h.available ? `<div class="kv"><span class="k">Mean, 2008–2021</span><span>${fmt.n(h.regime_means["2008-2021"], 2)}</span><span class="k">2022–2023</span><span>${fmt.n(h.regime_means["2022-2023"], 2)}</span><span class="k">2024–now</span><span>${fmt.n(h.regime_means["2024-now"], 2)}</span><span class="k">Latest (${esc(h.latest?.t || "")})</span><span><b>${fmt.n(h.latest?.v, 2)}</b></span></div>` : `<p class="muted">${esc(h.reason || "unavailable")}</p>`;
  $("#tab-research").innerHTML = `<div class="banner">${r.caveats.map(esc).join(" · ")}</div>
    <div class="grid c2">
      <div class="card"><h2>H1 · Gold vs 10y real yield</h2><p class="small">${esc(h1.hypothesis)} <span class="muted">Source: ${esc(h1.source)}. Factor series: ${esc(h1.factor)}.</span></p>${regime(h1)}<div class="chart-wrap"><canvas id="h1"></canvas></div></div>
      <div class="card"><h2>H2 · Gold beta to the dollar (DXY)</h2><p class="small">${esc(h2.hypothesis)}</p>${regime(h2)}<div class="chart-wrap"><canvas id="h2"></canvas></div></div>
    </div>
    <div class="card" style="margin-top:14px"><h2>H3 · Silver fair-value indicator (in-sample fit, out-of-sample test)</h2>
      ${h3.available ? `<div class="grid c2"><div><h3>In-sample ${esc(h3.in_sample.window)} (n=${h3.in_sample.n})</h3><div class="kv"><span class="k">β breakeven</span><span>${fmt.n(h3.in_sample.beta_breakeven, 3)}</span><span class="k">β nominal 10y</span><span>${fmt.n(h3.in_sample.beta_nominal, 3)}</span><span class="k">R² / adj R²</span><span>${fmt.n(h3.in_sample.r2, 3)} / ${fmt.n(h3.in_sample.adj_r2, 3)}</span><span class="k">Reference</span><span class="small">${esc(h3.in_sample.reference)}</span></div></div>
        <div><h3>Out-of-sample ${esc(h3.out_of_sample.window)}</h3><div class="kv"><span class="k">Hit rate |I|≥10</span><span><b>${h3.out_of_sample.hit_rate_thr10 != null ? fmt.n(h3.out_of_sample.hit_rate_thr10 * 100, 1) + "%" : "n/a"}</b> on ${h3.out_of_sample.signals_thr10} signals</span><span class="k">Hit rate |I|≥5</span><span>${h3.out_of_sample.hit_rate_thr5 != null ? fmt.n(h3.out_of_sample.hit_rate_thr5 * 100, 1) + "%" : "n/a"} on ${h3.out_of_sample.signals_thr5} signals</span><span class="k">Read</span><span class="small">${esc(h3.out_of_sample.read)}</span></div></div></div>
        <div class="prov">Inputs: ${esc(JSON.stringify(h3.inputs))}. Explanatory fit on daily changes; the 15-session indicator is scored on the sign of the next 15-session return.</div>` : `<p class="muted">${esc(h3.reason || "unavailable")} (inputs ${esc(JSON.stringify(h3.inputs || {}))})</p>`}
    </div>`;
  if (h1.available) lineChart("h1", [{ label: "60d corr(gold ret, Δreal yield)", points: h1.points, color: css("--au") }]);
  if (h2.available) lineChart("h2", [{ label: "60d beta(gold ret, DXY ret)", points: h2.points, color: css("--info") }]);
}

/* ---------- Status ---------- */
async function tabStatus() {
  const s = await api("/api/status");
  $("#tab-status").innerHTML = `<div class="grid c2">
    <div class="card"><h2>Collector jobs</h2><table><thead><tr><th>Job</th><th>Last run</th><th>OK</th><th class="num">Rows</th><th>Last success</th><th>Message</th></tr></thead><tbody>
      ${s.jobs.map(j => `<tr><td class="mono">${esc(j.job)} <button class="pill" style="cursor:pointer" data-job="${esc(j.job)}">run</button></td><td class="small">${fmt.t(j.started_at)}</td><td class="${j.ok ? "ok" : "bad"}">${j.ok ? "ok" : "FAIL"}</td><td class="num">${fmt.i(j.rows)}</td><td class="small">${fmt.t(j.last_ok_at)}</td><td class="small">${esc(j.message || "")}</td></tr>`).join("")}</tbody></table>
      <div class="prov">Manual triggers run synchronously. Known limits: FRED keyless endpoint times out from this machine (add a free FRED_API_KEY to .env); cmegroup.com blocks scripts (COMEX stocks via data/inbox); GLD data URL changed.</div></div>
    <div class="card"><h2>Series inventory (${s.series.length})</h2><div style="max-height:600px;overflow:auto"><table><thead><tr><th>Series</th><th>Type</th><th>Source</th><th>Cadence</th><th>Last</th><th class="num">N</th></tr></thead><tbody>
      ${s.series.map(x => `<tr><td class="mono">${esc(x.id)}<div class="muted small">${esc(x.name)}</div></td><td>${esc(x.instrument_type)}</td><td class="small">${esc(x.source)}</td><td>${esc(x.cadence)}</td><td class="small">${esc((x.last_ts || "").slice(0, 16))}</td><td class="num">${fmt.i(x.n)}</td></tr>`).join("")}</tbody></table></div></div></div>`;
  $$("#tab-status button[data-job]").forEach(b => b.onclick = async () => { b.textContent = "…"; const r = await fetch(`/api/jobs/${b.dataset.job}/run`, { method: "POST" }).then(x => x.json()); alert(JSON.stringify(r)); loaded.status = false; showTab("status"); });
}

/* ---------- Calculator ---------- */
async function tabCalculator() {
  const o = await api("/api/overview");
  const px = {
    gold: { futures: o.metals.gold.changes.last, spot: o.related.stak_spot_gold_daily?.changes?.last, ts: o.metals.gold.changes.last_ts, contract: o.metals.gold.front?.meta?.contract },
    silver: { futures: o.metals.silver.changes.last, spot: o.related.stak_spot_silver_daily?.changes?.last, ts: o.metals.silver.changes.last_ts, contract: o.metals.silver.front?.meta?.contract },
  };
  const OZ = { oz: 1, g: 1 / 31.1034768, kg: 1000 / 31.1034768, ozav: 0.911458 };
  const saved = (() => { try { return JSON.parse(localStorage.getItem("calc") || "{}"); } catch { return {}; } })();
  const row = (m, label, dot) => `<div class="calc-row">
      <div class="calc-h"><span class="dot ${dot}"></span> ${label} <span class="muted small">price basis: $<span id="px-${m}"></span> per troy oz</span></div>
      <label>Amount <input type="number" id="amt-${m}" min="0" step="any" inputmode="decimal" value="${esc(saved["amt-" + m] ?? "")}" placeholder="0">
        <select id="unit-${m}"><option value="oz">troy oz</option><option value="g">grams</option><option value="kg">kilograms</option><option value="ozav">ounces (avoirdupois)</option></select> = <b class="calc-out" id="val-${m}">$0</b></label>
      <label>Or dollars <input type="number" id="usd-${m}" min="0" step="any" inputmode="decimal" value="${esc(saved["usd-" + m] ?? "")}" placeholder="0"> = <b class="calc-out" id="oz-${m}">0 troy oz</b> <span class="muted small" id="g-${m}"></span></label>
    </div>`;
  $("#tab-calculator").innerHTML = `<div class="grid c2">
    <div class="card"><h2>Metal → dollars, dollars → metal</h2>
      <div class="filters"><label class="muted small">Price basis <select id="basis">
        <option value="futures">COMEX front-month futures (delayed)</option>
        <option value="spot" ${px.gold.spot ? "" : "disabled"}>Aggregated spot (StakTrakr)</option>
        <option value="custom">Custom prices</option></select></label>
        <span id="custom-wrap" class="hidden">gold $<input type="number" id="cust-gold" step="any" style="width:110px"> silver $<input type="number" id="cust-silver" step="any" style="width:90px"></span></div>
      ${row("gold", "Gold", "au")}${row("silver", "Silver", "ag")}
      <div class="calc-total">Combined value <b id="val-total">$0</b></div>
      <div class="prov">Troy ounce = 31.1035 g; a "1 oz" coin is one troy ounce; an avoirdupois ounce (kitchen scale) is 28.35 g. These are paper prices as of the last quote (<span id="calc-ts"></span>). Dealers buy below and sell above them, and coins and bars carry premiums, so an actual sale nets less and a purchase costs more (see the Physical tab for today's premiums).</div>
    </div>
    <div class="card"><h2>Common items at today's price</h2>
      <table><thead><tr><th>Item</th><th class="num">Troy oz</th><th class="num">Metal value</th></tr></thead><tbody id="items"></tbody></table>
      <div class="prov">Metal content only. Fractional coins trade at higher premiums than 1 oz pieces; sterling silver is 92.5% silver.</div>
    </div></div>`;
  const items = [["1 oz gold coin (Eagle, Buffalo, Maple)", "gold", 1], ["1/2 oz gold coin", "gold", 0.5], ["1/4 oz gold coin", "gold", 0.25], ["1/10 oz gold coin", "gold", 0.1],
    ["1 g gold bar", "gold", 1 / 31.1034768], ["10 g gold bar", "gold", 10 / 31.1034768], ["1 oz gold bar", "gold", 1], ["100 g gold bar", "gold", 100 / 31.1034768], ["1 kg gold bar", "gold", 1000 / 31.1034768],
    ["1 oz silver coin (Eagle, Maple)", "silver", 1], ["10 oz silver bar", "silver", 10], ["100 oz silver bar", "silver", 100], ["1 kg silver bar", "silver", 1000 / 31.1034768],
    ["$1 face value pre-1965 US 90% silver coins", "silver", 0.715], ["100 g sterling silver (92.5%)", "silver", 92.5 / 31.1034768]];
  const cur = () => { const b = $("#basis").value; const g = b === "custom" ? +$("#cust-gold").value : b === "spot" ? px.gold.spot : px.gold.futures; const s = b === "custom" ? +$("#cust-silver").value : b === "spot" ? px.silver.spot : px.silver.futures; return { gold: g || 0, silver: s || 0 }; };
  const money = (v) => "$" + Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const recalc = () => {
    const p = cur(); let total = 0; const store = {};
    for (const m of ["gold", "silver"]) {
      $(`#px-${m}`).textContent = fmt.n(p[m]);
      const amt = parseFloat($(`#amt-${m}`).value) || 0, unit = $(`#unit-${m}`).value;
      const oz = amt * OZ[unit]; const v = oz * p[m]; total += v;
      $(`#val-${m}`).textContent = money(v) + (unit !== "oz" && amt ? ` (${oz.toFixed(3)} troy oz)` : "");
      const usd = parseFloat($(`#usd-${m}`).value) || 0; const oz2 = p[m] ? usd / p[m] : 0;
      $(`#oz-${m}`).textContent = `${oz2.toFixed(4)} troy oz`; $(`#g-${m}`).textContent = usd ? `(${(oz2 * 31.1034768).toFixed(2)} g)` : "";
      store["amt-" + m] = $(`#amt-${m}`).value; store["usd-" + m] = $(`#usd-${m}`).value; store["unit-" + m] = unit;
    }
    $("#val-total").textContent = money(total);
    $("#items").innerHTML = items.map(([n, m, oz]) => `<tr><td>${esc(n)}</td><td class="num">${oz.toFixed(3)}</td><td class="num">${money(oz * p[m])}</td></tr>`).join("");
    $("#custom-wrap").classList.toggle("hidden", $("#basis").value !== "custom");
    try { localStorage.setItem("calc", JSON.stringify(store)); } catch {}
  };
  $("#calc-ts").textContent = `${px.gold.ts}, ${px.gold.contract || "GC=F"} / ${px.silver.contract || "SI=F"}`;
  $$("#tab-calculator input, #tab-calculator select").forEach(el => { el.oninput = recalc; el.onchange = recalc; });
  $("#cust-gold").value = px.gold.futures?.toFixed(2) || ""; $("#cust-silver").value = px.silver.futures?.toFixed(2) || "";
  for (const m of ["gold", "silver"]) if (saved["unit-" + m]) $(`#unit-${m}`).value = saved["unit-" + m];
  recalc();
}

const TABS = { overview: tabOverview, today: tabToday, drivers: tabDrivers, positioning: tabPositioning, physical: tabPhysical, news: tabNews, events: tabEvents, fundamentals: tabFundamentals, research: tabResearch, calculator: tabCalculator, status: tabStatus };

(async function main() {
  if (window.__REMOTE_DATA__) await loadRemote();
  try { CFG = await api("/api/config"); } catch {}
  $("#tz-note").textContent = ` Times in ${CFG.display_tz}.`;
  if (EXPORTED) {
    document.body.classList.add("exported");
    const b = $("#export"); if (b) { b.textContent = DATA_MODE === "live" ? `Live · data as of ${DATA_STAMP}` : `Saved snapshot · ${DATA_STAMP}`; b.disabled = true; }
    const ban = $("#snapshot-banner");
    if (ban) ban.textContent = DATA_MODE === "live"
      ? `Live copy: data as of ${DATA_STAMP}. It refreshes every time you reload the page (republished from Tom's dashboard about every 20 minutes while his computer is on).`
      : `Saved snapshot from ${DATA_STAMP}. Fresh data could not be fetched right now; reload when online to update.`;
    if (DATA_MODE === "live") setInterval(async () => { if (await loadRemote()) { const b2 = $("#export"); if (b2) b2.textContent = `Live · data as of ${DATA_STAMP}`; Object.keys(loaded).forEach(k => loaded[k] = false); showTab(location.hash.slice(1) || "overview"); } }, 20 * 60 * 1000);
  } else {
    const b = $("#export"); if (b) b.onclick = () => { b.textContent = "Building…"; window.location.href = "/api/export"; setTimeout(() => { b.textContent = "Download snapshot"; }, 4000); };
  }
  const tick = () => { $("#clock").textContent = new Date().toLocaleString("en-US", { timeZone: CFG.display_tz, weekday: "short", hour: "2-digit", minute: "2-digit", timeZoneName: "short" }); };
  tick(); setInterval(tick, 30000);
  showTab(location.hash.slice(1) || "overview");
  setInterval(() => { if (!$("#tab-overview").classList.contains("hidden")) { loaded.overview = false; showTab("overview"); } }, 5 * 60 * 1000);
})();
