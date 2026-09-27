/* FreeLM — logique de l'interface : filtres, onglets, tableau, exports. */
"use strict";

(() => {
  const { fmt, esc } = FreeLMCharts;
  const $ = id => document.getElementById(id);
  const PAGE = 200;
  const LIST_KEYS = ["server", "product", "user", "department"];

  const state = {
    type: "summary", preset: "30d", start: "", end: "",
    server: [], product: [], user: [], department: [],
    granularity: "day", metric: "peak", dimension: "user",
  };
  let meta = null;
  let current = null;       // dernier résultat affiché
  let requestId = 0;
  const table = { sortKey: null, sortDir: 1, search: "", shown: PAGE };
  const multis = {};

  /* -- Dates ---------------------------------------------------------------- */
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  function presetRange(preset) {
    const today = new Date(); today.setHours(0, 0, 0, 0);
    switch (preset) {
      case "7d": return [addDays(today, -6), today];
      case "90d": return [addDays(today, -89), today];
      case "mtd": return [new Date(today.getFullYear(), today.getMonth(), 1), today];
      case "lastm": return [new Date(today.getFullYear(), today.getMonth() - 1, 1), new Date(today.getFullYear(), today.getMonth(), 0)];
      case "ytd": return [new Date(today.getFullYear(), 0, 1), today];
      case "lasty": return [new Date(today.getFullYear() - 1, 0, 1), new Date(today.getFullYear() - 1, 11, 31)];
      case "all": {
        const s = meta && meta.data_start ? new Date(meta.data_start.slice(0, 10) + "T00:00") : addDays(today, -29);
        const e = meta && meta.data_end ? new Date(meta.data_end.slice(0, 10) + "T00:00") : today;
        return [s, e < s ? s : e];
      }
      default: return [addDays(today, -29), today];
    }
  }
  function applyPreset() {
    if (state.preset === "custom" && state.start && state.end) return;
    if (state.preset === "custom") state.preset = "30d";
    const [s, e] = presetRange(state.preset);
    state.start = iso(s); state.end = iso(e);
  }

  /* -- État <-> URL (liens partageables) ------------------------------------- */
  function toQuery(extra = {}) {
    const q = new URLSearchParams();
    q.set("type", state.type);
    q.set("start", state.start); q.set("end", state.end);
    for (const k of LIST_KEYS) for (const v of state[k]) q.append(k, v);
    q.set("granularity", state.granularity); q.set("metric", state.metric); q.set("dimension", state.dimension);
    for (const [k, v] of Object.entries(extra)) q.set(k, v);
    return q;
  }
  function saveHash() {
    const q = toQuery();
    q.set("preset", state.preset);
    history.replaceState(null, "", "#" + q.toString());
  }
  function loadHash() {
    const q = new URLSearchParams(location.hash.slice(1));
    for (const k of ["type", "preset", "start", "end", "granularity", "metric", "dimension"]) if (q.get(k)) state[k] = q.get(k);
    for (const k of LIST_KEYS) state[k] = q.getAll(k);
  }

  /* -- Sélecteur multiple ------------------------------------------------------ */
  function createMulti(container, label, options, key) {
    container.innerHTML = "";
    const button = document.createElement("button");
    button.type = "button"; button.className = "ms-button";
    button.setAttribute("aria-haspopup", "listbox");
    const text = document.createElement("span");
    button.appendChild(text);
    container.appendChild(button);
    let panel = null;

    function refreshLabel() {
      const sel = state[key];
      text.textContent = sel.length === 0 ? `Tous (${options.length})` : sel.length === 1 ? (options.find(o => o.value === sel[0]) || { label: sel[0] }).label : `${sel.length} sélectionnés`;
      button.title = sel.length ? sel.join(", ") : `Tous les ${label.toLowerCase()}`;
    }
    function close() { if (panel) { panel.remove(); panel = null; button.setAttribute("aria-expanded", "false"); } }
    function open() {
      Object.values(multis).forEach(m => m.close());
      panel = document.createElement("div");
      panel.className = "ms-panel";
      panel.innerHTML = `<input type="search" placeholder="Filtrer ${esc(label.toLowerCase())}" aria-label="Filtrer">` +
        `<div class="ms-actions"><button type="button" data-a="all">Tout cocher</button><button type="button" data-a="none">Tout effacer</button></div>` +
        `<div class="ms-list" role="listbox" aria-multiselectable="true"></div>`;
      const list = panel.querySelector(".ms-list");
      const search = panel.querySelector("input");
      const render = () => {
        const q = search.value.toLowerCase();
        list.innerHTML = "";
        options.filter(o => (o.label + " " + (o.hint || "")).toLowerCase().includes(q)).slice(0, 500).forEach(o => {
          const l = document.createElement("label");
          l.innerHTML = `<input type="checkbox" ${state[key].includes(o.value) ? "checked" : ""}> <span>${esc(o.label)}</span>${o.hint ? `<small>${esc(o.hint)}</small>` : ""}`;
          l.querySelector("input").addEventListener("change", ev => {
            state[key] = ev.target.checked ? [...state[key], o.value] : state[key].filter(v => v !== o.value);
            refreshLabel(); changed();
          });
          list.appendChild(l);
        });
        if (!list.children.length) list.innerHTML = `<div class="muted" style="padding:6px">Aucun résultat</div>`;
      };
      search.addEventListener("input", render);
      panel.querySelector('[data-a="all"]').addEventListener("click", () => {
        const q = search.value.toLowerCase();
        const vis = options.filter(o => (o.label + " " + (o.hint || "")).toLowerCase().includes(q)).map(o => o.value);
        state[key] = [...new Set([...state[key], ...vis])];
        refreshLabel(); render(); changed();
      });
      panel.querySelector('[data-a="none"]').addEventListener("click", () => { state[key] = []; refreshLabel(); render(); changed(); });
      container.appendChild(panel);
      button.setAttribute("aria-expanded", "true");
      render();
      search.focus();
    }
    button.addEventListener("click", ev => { ev.stopPropagation(); panel ? close() : open(); });
    container.addEventListener("click", ev => ev.stopPropagation());
    refreshLabel();
    return { close, refreshLabel };
  }
  document.addEventListener("click", () => Object.values(multis).forEach(m => m.close()));
  document.addEventListener("keydown", ev => { if (ev.key === "Escape") Object.values(multis).forEach(m => m.close()); });

  /* -- Initialisation ------------------------------------------------------------ */
  async function init() {
    loadHash();
    try {
      meta = await fetchJSON("api/meta");
    } catch (e) {
      showError("Impossible de charger les métadonnées : " + e.message);
      return;
    }
    $("version").textContent = meta.version || "";
    const cov = meta.data_start ? `Données du ${fmt(meta.data_start, "date")} au ${fmt(meta.data_end, "date")}` : "Aucune donnée collectée";
    $("coverage").textContent = cov;
    renderHealth();

    // Onglets
    const tabs = $("tabs");
    for (const [key, label] of Object.entries(meta.reports)) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "tab"; b.setAttribute("role", "tab"); b.dataset.type = key; b.textContent = label;
      b.addEventListener("click", () => { state.type = key; table.sortKey = null; changed(); });
      tabs.appendChild(b);
    }
    // Dimensions
    const dim = $("dimension");
    for (const [k, v] of Object.entries(meta.dimensions)) dim.add(new Option(v, k));

    // Filtres multiples
    multis.server = createMulti($("ms-server"), "Serveurs", meta.servers.map(s => ({ value: s, label: s })), "server");
    multis.product = createMulti($("ms-product"), "Produits", meta.products.map(p => ({ value: p.name, label: p.name, hint: p.servers.join(", ") })), "product");
    multis.user = createMulti($("ms-user"), "Utilisateurs", meta.users.map(u => ({ value: u.username, label: u.display_name ? `${u.display_name} (${u.username})` : u.username, hint: u.department || "" })), "user");
    const deps = meta.departments.map(d => ({ value: d, label: d }));
    deps.push({ value: "(non renseigné)", label: "(non renseigné)" });
    multis.department = createMulti($("ms-department"), "Services", deps, "department");

    // Contrôles simples
    $("preset").addEventListener("change", ev => { state.preset = ev.target.value; if (state.preset !== "custom") applyPreset(); changed(); });
    for (const id of ["start", "end"]) $(id).addEventListener("change", ev => { state[id] = ev.target.value; state.preset = "custom"; changed(); });
    for (const id of ["granularity", "metric", "dimension"]) $(id).addEventListener("change", ev => { state[id] = ev.target.value; changed(); });
    $("reset").addEventListener("click", () => {
      Object.assign(state, { preset: "30d", server: [], product: [], user: [], department: [], granularity: "day", metric: "peak", dimension: "user" });
      applyPreset(); Object.values(multis).forEach(m => m.refreshLabel()); changed();
    });
    $("table-search").addEventListener("input", ev => { table.search = ev.target.value.toLowerCase(); table.shown = PAGE; renderTable(); });
    $("more").addEventListener("click", () => { table.shown += PAGE * 5; renderTable(); });
    document.querySelectorAll("[data-export]").forEach(b => b.addEventListener("click", () => doExport(b.dataset.export)));
    $("health").addEventListener("click", () => { state.type = "collections"; changed(); });
    let resizeTimer;
    window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => current && renderChart(current), 150); });

    // Lien collé / navigation précédent-suivant : l'URL fait foi.
    window.addEventListener("hashchange", () => {
      loadHash();
      applyPreset();
      Object.values(multis).forEach(m => m.refreshLabel());
      changed();
    });

    applyPreset();
    changed();
  }

  function renderHealth() {
    const h = $("health");
    const cols = meta.collections || [];
    if (!cols.length) { h.hidden = true; return; }
    const failing = cols.filter(c => c.status === "ERROR");
    const last = cols.map(c => c.last_run).sort().pop();
    h.hidden = false;
    h.innerHTML = failing.length
      ? `<span class="dot" style="background:var(--critical)"></span>⚠ ${failing.length} collecteur(s) en échec`
      : `<span class="dot" style="background:var(--good)"></span>✓ Collectes OK · ${fmt(last, "datetime")}`;
    h.title = cols.map(c => `${c.server}/${c.collector} : ${c.status} (${fmt(c.last_run, "datetime")})${c.message ? " — " + c.message : ""}`).join("\n");
  }

  /* -- Chargement ---------------------------------------------------------------- */
  function syncControls() {
    $("preset").value = state.preset;
    $("start").value = state.start; $("end").value = state.end;
    $("granularity").value = state.granularity; $("metric").value = state.metric; $("dimension").value = state.dimension;
    document.querySelectorAll(".tab").forEach(t => t.setAttribute("aria-selected", String(t.dataset.type === state.type)));
    document.querySelectorAll("#options [data-for]").forEach(f => { f.hidden = !f.dataset.for.split(" ").includes(state.type); });
  }
  function changed() {
    syncControls();
    saveHash();
    load();
  }
  async function fetchJSON(url) {
    const r = await fetch(url);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    return body;
  }
  function showError(msg) { const e = $("error"); e.textContent = msg; e.hidden = !msg; }

  async function load() {
    const id = ++requestId;
    $("main").style.opacity = "0.55";
    $("table-title").textContent = "Chargement…";
    try {
      const result = await fetchJSON("api/report?" + toQuery().toString());
      if (id !== requestId) return;   // réponse obsolète
      showError("");
      current = result;
      table.shown = PAGE;
      render(result);
    } catch (e) {
      if (id === requestId) showError(e.message);
    } finally {
      if (id === requestId) $("main").style.opacity = "";
    }
  }

  /* -- Rendu ------------------------------------------------------------------------ */
  function render(r) {
    const k = $("kpis");
    k.innerHTML = r.kpis.map(x => `<div class="kpi"><div class="label">${esc(x.label)}</div><div class="value">${fmt(x.value, x.type)}</div></div>`).join("");
    $("notes").innerHTML = r.notes.map(n => `<li>${esc(n)}</li>`).join("");
    renderChart(r);
    $("table-title").textContent = r.title;
    renderTable();
  }

  function renderChart(r) {
    const card = $("chart-card");
    const c = r.chart;
    if (!c || c.kind === "matrix") { card.hidden = true; return; }
    card.hidden = false;
    $("chart-title").textContent = c.title;
    if (c.kind === "line") FreeLMCharts.line($("chart"), $("legend"), c);
    else if (c.kind === "bar") FreeLMCharts.bar($("chart"), $("legend"), c);
    else if (c.kind === "heatmap") FreeLMCharts.heatmap($("chart"), $("legend"), c);
  }

  const NUMERIC = new Set(["int", "float", "pct", "hours"]);
  function sortedRows() {
    const r = current;
    let rows = r.rows;
    if (table.search) {
      rows = rows.filter(row => r.columns.some(c => {
        const v = row[c.key];
        return v !== null && v !== undefined && (String(v).toLowerCase().includes(table.search) || fmt(v, c.type).toLowerCase().includes(table.search));
      }));
    }
    if (table.sortKey) {
      const col = r.columns.find(c => c.key === table.sortKey);
      const num = col && NUMERIC.has(col.type);
      rows = [...rows].sort((a, b) => {
        const x = a[table.sortKey], y = b[table.sortKey];
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        return (num ? x - y : String(x).localeCompare(String(y), "fr")) * table.sortDir;
      });
    }
    return rows;
  }

  function renderTable() {
    const r = current;
    const t = $("table");
    if (!r) return;
    if (!r.rows.length) {
      t.innerHTML = `<tbody><tr><td class="empty">Aucune donnée pour ces filtres sur la période.</td></tr></tbody>`;
      $("row-count").textContent = "";
      $("more").hidden = true;
      return;
    }
    const rows = sortedRows();
    const heat = r.type === "matrix" || r.type === "heatmap";
    let max = 0;
    if (heat) for (const row of rows) for (const c of r.columns) if (NUMERIC.has(c.type) && c.key !== "total" && row[c.key] > max) max = row[c.key];

    const head = "<thead><tr>" + r.columns.map(c => {
      const sort = table.sortKey === c.key ? (table.sortDir > 0 ? "ascending" : "descending") : "none";
      return `<th class="${NUMERIC.has(c.type) ? "num" : ""}" data-key="${esc(c.key)}" aria-sort="${sort}" scope="col">${esc(c.label)}</th>`;
    }).join("") + "</tr></thead>";
    const body = rows.slice(0, table.shown).map(row => "<tr>" + r.columns.map(c => {
      const v = row[c.key];
      const cls = [NUMERIC.has(c.type) ? "num" : "", v === null || v === undefined ? "empty-cell" : ""].join(" ").trim();
      let style = "";
      if (heat && NUMERIC.has(c.type) && c.key !== "total" && v) {
        const pct = r.type === "heatmap" ? v : v / (max || 1);
        style = ` style="background:color-mix(in srgb, var(--seq-4) ${Math.round(pct * 55)}%, transparent)"`;
      }
      let content = fmt(v, c.type);
      if (c.key === "status") {
        const ok = v === "OK";
        content = `<span class="status"><span class="dot" style="background:var(${ok ? "--good" : "--critical"})"></span>${ok ? "✓ OK" : "⚠ " + esc(v)}</span>`;
      } else content = esc(content);
      return `<td class="${cls}"${style}>${content}</td>`;
    }).join("") + "</tr>").join("");
    t.innerHTML = head + "<tbody>" + body + "</tbody>";
    t.querySelectorAll("th").forEach(th => th.addEventListener("click", () => {
      const key = th.dataset.key;
      table.sortDir = table.sortKey === key ? -table.sortDir : (NUMERIC.has((r.columns.find(c => c.key === key) || {}).type) ? -1 : 1);
      table.sortKey = key;
      renderTable();
    }));
    const total = rows.length;
    $("row-count").textContent = total > table.shown ? `${table.shown} / ${total} lignes affichées (l'export contient tout)` : `${total} ligne${total > 1 ? "s" : ""}`;
    $("more").hidden = total <= table.shown;
  }

  /* -- Exports ------------------------------------------------------------------------ */
  async function doExport(format) {
    const q = format === "full" ? toQuery({ type: "full", format: "xlsx" }) : toQuery({ format });
    const button = document.querySelector(`[data-export="${format}"]`);
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Export…";
    try {
      const r = await fetch("api/export?" + q.toString());
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${r.status}`);
      }
      const blob = await r.blob();
      const cd = r.headers.get("Content-Disposition") || "";
      const m = /filename="([^"]+)"/.exec(cd);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = m ? m[1] : `FreeLM_export.${format === "full" ? "xlsx" : format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      showError("");
    } catch (e) {
      showError("Export impossible : " + e.message);
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  }

  init();
})();
