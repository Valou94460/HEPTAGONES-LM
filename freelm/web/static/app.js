/* FreeLM — logique de l'interface : navigation, filtres, tableau de bord,
   vue en direct, rapports détaillés et exports. */
"use strict";

(() => {
  const C = FreeLMCharts;
  const { fmt, esc } = C;
  const $ = id => document.getElementById(id);
  const PAGE = 200;
  const LIST_KEYS = ["server", "product", "user", "department"];
  const LIVE_REFRESH_MS = 60000;
  const STALE_MINUTES = 60;
  const DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];

  /* Navigation : rapports regroupés par usage. */
  const NAV = [
    ["Pilotage", ["dashboard", "live"]],
    ["Analyse", ["summary", "timeseries", "breakdown", "matrix", "heatmap"]],
    ["Optimisation", ["efficiency", "optimization", "contention", "denials"]],
    ["Données", ["sessions", "collections"]],
  ];
  const ICONS = {
    dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    live: '<circle cx="12" cy="12" r="2.5"/><path d="M7.8 16.2a6 6 0 0 1 0-8.4M16.2 7.8a6 6 0 0 1 0 8.4M4.9 19.1a10 10 0 0 1 0-14.2M19.1 4.9a10 10 0 0 1 0 14.2"/>',
    summary: '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
    timeseries: '<path d="M3 17l5-6 4 3 7-8"/><path d="M3 21h18"/>',
    breakdown: '<path d="M12 3a9 9 0 1 0 9 9h-9z"/><path d="M15 3.5A9 9 0 0 1 20.5 9H15z"/>',
    matrix: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    heatmap: '<rect x="3" y="3" width="5" height="5" rx="1"/><rect x="10" y="3" width="5" height="5" rx="1"/><rect x="17" y="3" width="4" height="5" rx="1"/><rect x="3" y="10" width="5" height="5" rx="1"/><rect x="10" y="10" width="5" height="5" rx="1"/><rect x="3" y="17" width="5" height="4" rx="1"/>',
    efficiency: '<path d="M12 13l4-4"/><path d="M3.5 17a9 9 0 1 1 17 0"/><circle cx="12" cy="13" r="1"/>',
    optimization: '<circle cx="12" cy="12" r="9"/><path d="M15 9.5c-.6-1-1.7-1.5-3-1.5-1.7 0-3 .9-3 2s1 1.7 3 2 3 .9 3 2-1.3 2-3 2c-1.3 0-2.4-.5-3-1.5M12 6.5V8M12 16v1.5"/>',
    contention: '<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
    denials: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    sessions: '<path d="M4 6h16M4 12h16M4 18h10"/>',
    collections: '<path d="M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z"/><path d="M4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    other: '<circle cx="12" cy="12" r="9"/>',
  };
  const SEV_LABEL = { critical: "Critique", serious: "Important", warning: "Attention", info: "Info" };
  const KPI_TARGET = {
    utilization: { type: "timeseries", metric: "utilization" },
    users: { type: "breakdown", dimension: "user" },
    hours: { type: "breakdown", dimension: "department" },
    saturated: { type: "contention" },
    denials: { type: "denials" },
    savings: { type: "optimization" },
    surplus: { type: "efficiency" },
  };
  const KPI_COLOR = { utilization: 1, users: 3, hours: 7, saturated: 4, denials: 2 };

  const state = {
    type: "dashboard", preset: "30d", start: "", end: "",
    server: [], product: [], user: [], department: [],
    granularity: "day", metric: "peak", dimension: "user",
  };
  let meta = null;
  let current = null;       // dernier résultat affiché
  let shownType = null;     // page dont le squelette est en place
  let requestId = 0;
  const table = { sortKey: null, sortDir: 1, search: "", shown: PAGE };
  const multis = {};
  let liveFilter = "";

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
  function parseTime(v) {
    if (!v) return null;
    const d = new Date(String(v).replace(" ", "T"));
    return isNaN(d) ? null : d;
  }
  function ago(v) {
    const d = parseTime(v);
    if (!d) return "—";
    const min = Math.round((Date.now() - d.getTime()) / 60000);
    if (min < 1) return "à l'instant";
    if (min < 60) return `il y a ${min} min`;
    if (min < 48 * 60) return `il y a ${Math.round(min / 60)} h`;
    return "le " + fmt(v, "datetime");
  }
  function since(v) {
    const d = parseTime(v);
    if (!d) return "";
    const min = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000));
    if (min < 60) return `${min} min`;
    if (min < 24 * 60) return `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, "0")}`;
    return `${Math.floor(min / 1440)} j ${Math.floor((min % 1440) / 60)} h`;
  }
  const minutesOld = v => { const d = parseTime(v); return d ? (Date.now() - d.getTime()) / 60000 : Infinity; };

  /* -- Petits composants ----------------------------------------------------- */
  function initials(name) {
    const parts = String(name || "?").replace(/[()]/g, " ").trim().split(/[\s._-]+/).filter(Boolean);
    return ((parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : (parts[0] || "")[1] || "")).toUpperCase();
  }
  function avatarColor(key) {
    let h = 0;
    for (const ch of String(key || "")) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return `var(--series-${(h % 8) + 1})`;
  }
  const avatar = (name, key) => `<span class="avatar" style="background:${avatarColor(key || name)}" aria-hidden="true">${esc(initials(name))}</span>`;
  const icon = key => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[key] || ICONS.other}</svg>`;
  const empty = text => `<div class="empty-state">${esc(text)}</div>`;
  function meterClass(v) { return v >= 0.9 ? "full" : v >= 0.7 ? "hot" : ""; }
  function pctWidth(v) { return Math.max(0, Math.min(100, (v || 0) * 100)).toFixed(1) + "%"; }

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
  let lastHash = "";
  function saveHash() {
    const q = toQuery();
    q.set("preset", state.preset);
    const hash = "#" + q.toString();
    if (hash === location.hash) return;
    // Une entrée d'historique par état : Précédent / Suivant naviguent entre les vues.
    if (location.hash) history.pushState(null, "", hash);
    else history.replaceState(null, "", hash);
    lastHash = hash;
  }
  function loadHash() {
    const q = new URLSearchParams(location.hash.slice(1));
    for (const k of ["type", "preset", "start", "end", "granularity", "metric", "dimension"]) if (q.get(k)) state[k] = q.get(k);
    for (const k of LIST_KEYS) state[k] = q.getAll(k);
    if (meta && !meta.reports[state.type]) state.type = "dashboard";
  }

  /* -- Sélecteur multiple (pastille + panneau) --------------------------------- */
  function createMulti(container, label, options, key) {
    container.innerHTML = "";
    const button = document.createElement("button");
    button.type = "button"; button.className = "ms-button";
    button.setAttribute("aria-haspopup", "listbox");
    button.innerHTML = `<span class="ms-label">${esc(label)}</span><span class="value"></span>`;
    const text = button.querySelector(".value");
    container.appendChild(button);
    let panel = null;

    function refreshLabel() {
      const sel = state[key];
      text.textContent = sel.length === 0 ? "Tous" : sel.length === 1 ? (options.find(o => o.value === sel[0]) || { label: sel[0] }).label : `${sel.length} sélectionnés`;
      button.classList.toggle("active", sel.length > 0);
      button.title = sel.length ? sel.join(", ") : `Tous les ${label.toLowerCase()} (${options.length})`;
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
      const matches = () => { const q = search.value.toLowerCase(); return options.filter(o => (o.label + " " + (o.hint || "")).toLowerCase().includes(q)); };
      const render = () => {
        list.innerHTML = "";
        matches().slice(0, 500).forEach(o => {
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
        state[key] = [...new Set([...state[key], ...matches().map(o => o.value)])];
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
  document.addEventListener("keydown", ev => { if (ev.key === "Escape") { Object.values(multis).forEach(m => m.close()); closeMenu(); } });

  /* -- Thème ------------------------------------------------------------------ */
  function setTheme(choice, save = true) {
    if (choice === "light" || choice === "dark") document.documentElement.dataset.theme = choice;
    else delete document.documentElement.dataset.theme;
    document.querySelectorAll("[data-theme-choice]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.themeChoice === (choice || "auto"))));
    if (save) { try { localStorage.setItem("freelm_theme", choice); } catch (e) { /* stockage indisponible */ } }
  }

  /* -- Menu mobile ------------------------------------------------------------ */
  function closeMenu() { $("sidebar").classList.remove("open"); $("menu").setAttribute("aria-expanded", "false"); }

  /* -- Initialisation ------------------------------------------------------------ */
  async function init() {
    let theme = "auto";
    try { theme = localStorage.getItem("freelm_theme") || "auto"; } catch (e) { /* stockage indisponible */ }
    setTheme(theme, false);
    document.querySelectorAll("[data-theme-choice]").forEach(b => b.addEventListener("click", () => setTheme(b.dataset.themeChoice)));
    $("menu").addEventListener("click", ev => {
      ev.stopPropagation();
      const open = $("sidebar").classList.toggle("open");
      $("menu").setAttribute("aria-expanded", String(open));
    });
    document.addEventListener("click", ev => { if (!$("sidebar").contains(ev.target)) closeMenu(); });

    loadHash();
    try {
      meta = await fetchJSON("api/meta");
    } catch (e) {
      showError("Impossible de charger les métadonnées : " + e.message);
      return;
    }
    if (!meta.reports[state.type]) state.type = "dashboard";
    $("version").textContent = meta.version || "";
    fetchJSON("api/me").then(me => {
      if (me.auth === "none") return;
      const name = me.display_name || me.user;
      $("user-badge").hidden = false;
      $("user-name").textContent = name;
      $("user-name").title = me.department ? `${me.user} — ${me.department}` : me.user;
      const av = $("user-avatar");
      av.textContent = initials(name); av.style.background = avatarColor(me.user);
      if (me.auth === "ldap") {
        const b = $("logout");
        b.hidden = false;
        b.addEventListener("click", async () => {
          await fetch("api/logout", { method: "POST", credentials: "same-origin" }).catch(() => null);
          location.replace("login.html");
        });
      }
    }).catch(() => null);
    $("coverage").textContent = meta.data_start ? `Données du ${fmt(meta.data_start, "date")} au ${fmt(meta.data_end, "date")}` : "Aucune donnée collectée";
    renderNav();
    renderHealth();

    // Filtres multiples
    multis.server = createMulti($("ms-server"), "Serveurs", meta.servers.map(s => ({ value: s, label: s })), "server");
    multis.product = createMulti($("ms-product"), "Produits", meta.products.map(p => ({ value: p.name, label: p.name, hint: p.servers.join(", ") })), "product");
    multis.user = createMulti($("ms-user"), "Utilisateurs", meta.users.map(u => ({ value: u.username, label: u.display_name ? `${u.display_name} (${u.username})` : u.username, hint: u.department || "" })), "user");
    const deps = meta.departments.map(d => ({ value: d, label: d }));
    deps.push({ value: "(non renseigné)", label: "(non renseigné)" });
    multis.department = createMulti($("ms-department"), "Services", deps, "department");

    // Période
    document.querySelectorAll("#presets [data-preset]").forEach(b => b.addEventListener("click", () => {
      state.preset = b.dataset.preset; applyPreset(); changed();
    }));
    for (const id of ["start", "end"]) $(id).addEventListener("change", ev => {
      if (!ev.target.value) return;
      state[id] = ev.target.value; state.preset = "custom"; changed();
    });
    $("reset").addEventListener("click", () => {
      Object.assign(state, { server: [], product: [], user: [], department: [] });
      Object.values(multis).forEach(m => m.refreshLabel()); changed();
    });
    document.querySelectorAll("[data-export]").forEach(b => b.addEventListener("click", () => doExport(b.dataset.export)));
    $("health").addEventListener("click", () => go({ type: "collections" }));

    // Liens internes des pages (délégation) : data-go="type" [+ data-*]
    $("view").addEventListener("click", ev => {
      const t = ev.target.closest("[data-go]");
      if (!t) return;
      const extra = {};
      for (const k of ["metric", "dimension", "granularity"]) if (t.dataset[k]) extra[k] = t.dataset[k];
      go({ type: t.dataset.go, ...extra });
    });

    let resizeTimer, lastWidth = window.innerWidth;
    window.addEventListener("resize", () => {
      if (window.innerWidth === lastWidth) return;
      lastWidth = window.innerWidth;
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => current && redraw(), 150);
    });

    // Lien collé / navigation précédent-suivant : l'URL fait foi.
    const fromUrl = () => {
      if (location.hash === lastHash) return;   // déjà affiché
      lastHash = location.hash;
      loadHash();
      applyPreset();
      Object.values(multis).forEach(m => m.refreshLabel());
      table.sortKey = null;
      syncControls();
      saveHash();
      load();
    };
    window.addEventListener("popstate", fromUrl);
    window.addEventListener("hashchange", fromUrl);

    // Vue en direct : rafraîchissement automatique tant qu'elle est affichée.
    setInterval(() => { if (state.type === "live" && !document.hidden) load({ silent: true }); }, LIVE_REFRESH_MS);

    applyPreset();
    changed();
  }

  function renderNav() {
    const nav = $("nav");
    const placed = new Set(NAV.flatMap(([, items]) => items));
    const groups = NAV.map(([g, items]) => [g, items.filter(k => meta.reports[k])]);
    const others = Object.keys(meta.reports).filter(k => !placed.has(k));
    if (others.length) groups[1][1].push(...others);
    nav.innerHTML = groups.filter(([, items]) => items.length).map(([g, items]) =>
      `<div class="nav-group">${esc(g)}</div>` + items.map(k =>
        `<button type="button" class="nav-item" data-type="${esc(k)}">${icon(k)}<span>${esc(meta.reports[k])}</span></button>`).join("")
    ).join("");
    nav.querySelectorAll(".nav-item").forEach(b => b.addEventListener("click", () => { closeMenu(); go({ type: b.dataset.type }); }));
  }

  function go(changes) {
    if (changes.type && changes.type !== state.type) table.sortKey = null;
    Object.assign(state, changes);
    changed();
    window.scrollTo({ top: 0 });
  }

  function renderHealth() {
    const h = $("health");
    const cols = meta.collections || [];
    if (!cols.length) { h.hidden = true; return; }
    const failing = cols.filter(c => c.status === "ERROR");
    const last = cols.map(c => c.last_run).sort().pop();
    const stale = minutesOld(last) > STALE_MINUTES;
    h.hidden = false;
    h.innerHTML = failing.length
      ? `<span class="dot" style="background:var(--critical)"></span><span>⚠ ${failing.length} collecteur(s) en échec</span>`
      : stale
        ? `<span class="dot" style="background:var(--warning)"></span><span>⏸ <span class="txt">Dernière collecte </span>${esc(ago(last))}</span>`
        : `<span class="dot" style="background:var(--good)"></span><span>✓ <span class="txt">Collectes OK · </span>${esc(ago(last))}</span>`;
    h.title = cols.map(c => `${c.server}/${c.collector} : ${c.status} (${fmt(c.last_run, "datetime")})${c.message ? " — " + c.message : ""}`).join("\n");
    const item = document.querySelector('.nav-item[data-type="collections"]');
    if (item && failing.length && !item.querySelector(".badge")) item.insertAdjacentHTML("beforeend", `<span class="badge sev sev-critical">${failing.length}</span>`);
  }

  /* -- Chargement ---------------------------------------------------------------- */
  function syncControls() {
    document.querySelectorAll("#presets [data-preset]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.preset === state.preset)));
    $("start").value = state.start; $("end").value = state.end;
    document.querySelectorAll(".nav-item").forEach(t => {
      if (t.dataset.type === state.type) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
    });
    $("reset").hidden = !LIST_KEYS.some(k => state[k].length);
    $("page-title").textContent = (meta && meta.reports[state.type]) || "";
    document.title = `${$("page-title").textContent} — FreeLM`;
    document.querySelectorAll("[data-opt]").forEach(b => b.setAttribute("aria-pressed", String(state[b.dataset.opt] === b.dataset.value)));
    const dim = $("dimension");
    if (dim) dim.value = state.dimension;
  }
  function changed() {
    syncControls();
    saveHash();
    load();
  }
  function toLogin() {
    try { sessionStorage.setItem("freelm_next_hash", location.hash); } catch (e) { /* stockage indisponible */ }
    location.replace("login.html" + location.hash);
  }
  async function fetchJSON(url) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (r.status === 401) {
      const body = await r.json().catch(() => ({}));
      if (body.login) toLogin();
      throw new Error(body.error || "Authentification requise");
    }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    return body;
  }
  function showError(msg) { const e = $("error"); e.textContent = msg; e.hidden = !msg; }

  const pageKind = type => (type === "dashboard" || type === "live" ? type : "report");

  async function load(opts = {}) {
    const id = ++requestId;
    const view = $("view");
    if (!opts.silent) {
      if (shownType !== state.type) skeleton(state.type);
      else if (pageKind(state.type) === "report") $("table-title").textContent = "Chargement…";
      view.classList.add("loading");
    }
    try {
      const result = await fetchJSON("api/report?" + toQuery().toString());
      if (id !== requestId) return;   // réponse obsolète
      showError("");
      current = result;
      table.shown = PAGE;
      if (result.currency) C.setCurrency(result.currency);
      $("page-desc").textContent = result.description || "";
      render(result);
    } catch (e) {
      if (id === requestId) {
        showError(e.message);
        if (pageKind(state.type) === "report" && $("table-title")) $("table-title").textContent = meta.reports[state.type] || "";
      }
    } finally {
      if (id === requestId) view.classList.remove("loading");
    }
  }

  /* -- Squelettes de page --------------------------------------------------------- */
  function skeleton(type) {
    shownType = type;
    const view = $("view");
    const kind = pageKind(type);
    if (kind === "dashboard") {
      view.innerHTML = `<div class="kpi-grid">${'<div class="kpi skel"></div>'.repeat(6)}</div>` +
        `<div class="grid"><div class="card span-8 skel tall"></div><div class="card span-4 skel tall"></div></div>`;
      return;
    }
    if (kind === "live") {
      view.innerHTML = `<div class="kpi-grid">${'<div class="kpi skel"></div>'.repeat(4)}</div><div class="live-grid">${'<div class="card skel tall"></div>'.repeat(3)}</div>`;
      return;
    }
    view.innerHTML = `
      ${reportOptions(type)}
      <div id="kpis" class="kpis"></div>
      <section id="chart-card" class="card" hidden>
        <div class="card-head"><h2 id="chart-title"></h2><div id="legend" class="legend"></div></div>
        <div id="chart" class="chart"></div>
      </section>
      <ul id="notes" class="notes"></ul>
      <section class="card">
        <div class="card-head">
          <h2 id="table-title">Chargement…</h2>
          <div class="table-tools">
            <span id="row-count" class="muted"></span>
            <input type="search" id="table-search" placeholder="Rechercher dans le tableau" aria-label="Rechercher dans le tableau">
          </div>
        </div>
        <div class="table-wrap"><table id="table"></table></div>
        <div class="table-foot"><button id="more" class="btn ghost" type="button" hidden>Afficher plus</button></div>
      </section>`;
    $("table-search").value = table.search;
    $("table-search").addEventListener("input", ev => { table.search = ev.target.value.toLowerCase(); table.shown = PAGE; renderTable(); });
    $("more").addEventListener("click", () => { table.shown += PAGE * 5; renderTable(); });
    view.querySelectorAll("[data-opt]").forEach(b => b.addEventListener("click", () => { state[b.dataset.opt] = b.dataset.value; changed(); }));
    const dim = $("dimension");
    if (dim) {
      for (const [k, v] of Object.entries(meta.dimensions)) dim.add(new Option(v, k));
      dim.value = state.dimension;
      dim.addEventListener("change", ev => { state.dimension = ev.target.value; changed(); });
    }
    syncControls();
  }
  function segmented(key, label, options) {
    return `<div class="field inline"><span>${esc(label)}</span><div class="segmented" role="group" aria-label="${esc(label)}">` +
      options.map(([v, l]) => `<button type="button" data-opt="${key}" data-value="${v}" aria-pressed="${state[key] === v}">${esc(l)}</button>`).join("") + "</div></div>";
  }
  function reportOptions(type) {
    if (type === "timeseries") {
      return `<div class="options" id="options">` +
        segmented("granularity", "Pas", [["hour", "Heure"], ["day", "Jour"], ["week", "Semaine"], ["month", "Mois"]]) +
        segmented("metric", "Mesure", [["peak", "Pic de jetons"], ["avg", "Moyenne"], ["utilization", "Taux d'occupation"]]) + "</div>";
    }
    if (type === "breakdown" || type === "matrix") {
      return `<div class="options" id="options"><label class="field inline"><span>Par</span><select id="dimension"></select></label></div>`;
    }
    return "";
  }

  function render(r) {
    const kind = pageKind(r.type);
    if (kind === "dashboard") renderDashboard(r);
    else if (kind === "live") renderLive(r);
    else renderReport(r);
  }
  function redraw() {
    if (current.type !== shownType) return;   // page en cours de changement
    if (pageKind(current.type) === "report") renderChart(current);
    else render(current);
  }

  /* -- Tableau de bord --------------------------------------------------------------- */
  function card(span, title, sub, action, body, id) {
    return `<section class="card ${span}"${id ? ` id="${id}"` : ""}><div class="card-head"><div><h2>${title}</h2>${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</div>${action || ""}</div><div class="card-body">${body}</div></section>`;
  }
  const goLink = (type, label, extra = "") => `<button type="button" class="link" data-go="${type}" ${extra}>${esc(label)} →</button>`;

  function renderDashboard(r) {
    const v = $("view");
    v.innerHTML =
      `<section class="alerts" id="alerts" aria-label="Alertes"></section>` +
      `<section class="kpi-grid" id="kpi-grid" aria-label="Indicateurs clés"></section>` +
      `<div class="grid">` +
      `<section class="card span-8"><div class="card-head"><div><h2>Taux d'occupation</h2><div class="sub">Heures ouvrées, par jour</div></div><div id="trend-legend" class="legend"></div></div><div id="trend-chart" class="chart"></div></section>` +
      card("span-4", `<span class="pulse" id="live-pulse"></span>En direct`, "", goLink("live", "Détail"), `<div id="live-card"></div>`) +
      card("span-6", "Occupation par produit", "Barre : moyenne en heures ouvrées · trait : pic", goLink("summary", "Synthèse"), `<div id="product-meters" class="meter-list"></div>`) +
      card("span-6", "Utilisateurs les plus actifs", "Heures d'utilisation sur la période", goLink("breakdown", "Répartition", 'data-dimension="user"'), `<div id="top-users" class="people"></div>`) +
      card("span-4", "Refus par jour", "Demandes de licence refusées", goLink("denials", "Refus"), `<div id="denials-chart" class="chart flush"></div>`) +
      card("span-4", "Profil horaire", "Occupation moyenne par jour et heure", goLink("heatmap", "Détail"), `<div id="mini-heat"></div>`) +
      card("span-4", "Serveurs et échéances", "", goLink("collections", "Collectes"), `<div id="servers"></div>`) +
      `</div>`;

    // Alertes
    const alerts = r.alerts || [];
    $("alerts").innerHTML = alerts.length ? alerts.map(a =>
      `<div class="alert sev-${esc(a.severity)}"><span class="sev-icon" aria-hidden="true">${a.severity === "info" ? "i" : "!"}</span>` +
      `<div class="alert-body"><div class="sev-label">${esc(SEV_LABEL[a.severity] || a.severity)}</div><div class="alert-title">${esc(a.title)}</div>` +
      `<div class="alert-detail">${esc(a.detail || "")}</div></div>` +
      (a.target && meta.reports[a.target] ? goLink(a.target, "Voir") : "") + `</div>`).join("")
      : `<div class="alerts-ok"><span class="status-dot sev-good" aria-hidden="true">✓</span>Aucune alerte : collectes à jour, pas de refus ni d'échéance proche.</div>`;

    // KPI
    const grid = $("kpi-grid");
    grid.innerHTML = r.kpis.map(k => {
      const target = KPI_TARGET[k.key];
      const tag = target ? "button" : "div";
      return `<${tag} ${target ? 'type="button" ' : ""}class="${target ? "kpi-card" : "kpi"}" data-key="${esc(k.key)}">` +
        `<span class="label">${esc(k.label)}</span><span class="value">${fmt(k.value, k.type)}</span>${deltaHtml(k)}<span class="spark"></span></${tag}>`;
    }).join("");
    grid.querySelectorAll(".kpi-card").forEach(b => b.addEventListener("click", () => go({ ...KPI_TARGET[b.dataset.key] })));
    r.kpis.forEach((k, i) => {
      if (k.spark) C.sparkline(grid.children[i].querySelector(".spark"), k.spark, `var(--series-${KPI_COLOR[k.key] || 1})`);
    });

    // Tendance d'occupation
    const tr = r.trend;
    if (tr.x.length && tr.business_utilization.some(x => x !== null)) {
      C.line($("trend-chart"), $("trend-legend"), {
        title: "Taux d'occupation", format: "pct", xType: "day", x: tr.x, height: 260, direct: false,
        series: [
          { name: "Moyenne heures ouvrées", values: tr.business_utilization, color: "var(--series-1)", area: true },
          { name: "Pic du jour", values: tr.peak_utilization, color: "var(--series-2)", dash: true },
        ],
      });
    } else $("trend-chart").innerHTML = empty("Aucune donnée d'occupation sur la période.");

    renderLiveCard(r.live);

    // Produits
    const prods = r.products || [];
    $("product-meters").innerHTML = prods.length ? prods.slice(0, 8).map(p =>
      `<div class="meter-row"><div class="name" title="${esc(p.product)} — ${esc(p.server)}">${esc(p.product)}<small>${esc(p.server)}</small></div>` +
      `<div class="num">${fmt(p.business_utilization, "pct")} · pic ${fmt(p.peak, "int")}/${fmt(p.issued, "int")}</div>` +
      `<div class="meter" title="Moyenne ${fmt(p.business_utilization, "pct")}, pic ${fmt(p.peak_utilization, "pct")}"><span class="fill ${meterClass(p.business_utilization)}" style="width:${pctWidth(p.business_utilization)}"></span>` +
      (p.peak_utilization !== null && p.peak_utilization !== undefined ? `<span class="mark" style="left:calc(${pctWidth(p.peak_utilization)} - 1px)"></span>` : "") + `</div></div>`
    ).join("") + (prods.length > 8 ? `<div class="more-link">${goLink("summary", `Voir les ${prods.length} produits`)}</div>` : "")
      : empty("Aucun produit sur la période.");

    // Utilisateurs
    const users = r.top_users || [];
    const maxH = Math.max(1, ...users.map(u => u.hours));
    $("top-users").innerHTML = users.length ? users.map(u =>
      `<div class="person">${avatar(u.display_name || u.username, u.username)}<div class="who"><div>${esc(u.display_name || u.username)}</div>` +
      `<small>${esc(u.department || u.username)}</small></div><b class="num">${fmt(u.hours, "hours")}</b>` +
      `<div class="bar"><i style="width:${(100 * u.hours / maxH).toFixed(1)}%"></i></div></div>`).join("")
      : empty("Aucune session sur la période.");

    // Refus
    const den = r.denials_daily;
    if (den.values.some(x => x)) {
      C.columns($("denials-chart"), { title: "Refus", x: den.x, values: den.values, xType: "day", format: "int", color: "var(--serious)", height: 190 });
    } else $("denials-chart").innerHTML = `<div class="empty-state good">✓ Aucun refus sur la période</div>`;

    // Profil horaire compact : 7 lignes (jours) × 24 colonnes (heures)
    if (r.heatmap.has_data) {
      const values = DAYS.map((_, wd) => r.heatmap.values.map(row => row[wd]));
      C.heatmap($("mini-heat"), null, { compact: true, rows: DAYS, cols: r.heatmap.rows, values });
    } else $("mini-heat").innerHTML = empty("Pas encore de profil horaire.");

    // Serveurs et échéances
    const srv = r.servers || [];
    const SRV = { ok: ["good", "✓", "Opérationnel"], error: ["critical", "!", "Collecte en échec"], stale: ["warning", "⏸", "Collecte arrêtée"] };
    let html = srv.length ? `<div class="status-list">` + srv.map(s => {
      const [sev, sym, label] = SRV[s.status] || SRV.ok;
      return `<div class="status-row" title="${esc(label)}${s.message ? " — " + esc(s.message) : ""}"><span class="status-dot sev-${sev}" aria-hidden="true">${sym}</span>` +
        `<div><div>${esc(s.server)}</div><small>${esc(label)} · ${esc(ago(s.last_run))}</small></div>` +
        `<span class="num muted">${s.issued ? `${fmt(s.in_use, "int")}/${fmt(s.issued, "int")}` : ""}</span></div>`;
    }).join("") + `</div>` : empty("Aucun serveur collecté.");
    const exp = r.expirations || [];
    html += `<div class="sep"></div><h3>Échéances de licences</h3>`;
    html += exp.length ? `<div class="status-list compact">` + exp.slice(0, 4).map(e => {
      const sev = e.days_left <= 7 ? "critical" : e.days_left <= 30 ? "warning" : "info";
      const when = e.days_left < 0 ? "expirée" : e.days_left === 0 ? "aujourd'hui" : `J-${e.days_left}`;
      return `<div class="status-row"><span class="status-dot sev-${sev}" aria-hidden="true">${sev === "info" ? "i" : "!"}</span>` +
        `<div><div>${esc(e.product)}</div><small>${esc(e.server)} · ${fmt(e.expires_on, "date")}</small></div><span class="badge sev sev-${sev}">${esc(when)}</span></div>`;
    }).join("") + (exp.length > 4 ? `<small class="muted">+ ${exp.length - 4} autre(s)</small>` : "") + `</div>`
      : `<div class="empty-state small">Aucune échéance dans les 120 prochains jours.</div>`;
    $("servers").innerHTML = html;
  }

  function deltaHtml(k) {
    if (k.previous === null || k.previous === undefined) {
      return k.key === "savings" || k.key === "surplus"
        ? `<span class="delta"><span class="vs">selon le dimensionnement recommandé</span></span>`
        : `<span class="delta"><span class="vs">pas de période précédente</span></span>`;
    }
    const diff = k.type === "pct" ? (k.value - k.previous) * 100 : k.delta;
    let text;
    if (diff === null || diff === undefined) text = "nouveau";
    else if (Math.abs(diff) < (k.type === "pct" ? 0.05 : 0.0005)) text = "= stable";
    else {
      const arrow = diff > 0 ? "▲" : "▼";
      text = k.type === "pct"
        ? `${arrow} ${new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 }).format(Math.abs(diff))} pt`
        : `${arrow} ${new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 }).format(Math.abs(diff) * 100)} %`;
    }
    let cls = "";
    if (diff && k.trend !== "neutral") cls = (diff > 0) === (k.trend === "up_good") ? "good" : "bad";
    return `<span class="delta ${cls}">${text} <span class="vs">vs ${fmt(k.previous, k.type)}</span></span>`;
  }

  function renderLiveCard(live) {
    const box = $("live-card");
    const pulse = $("live-pulse");
    if (!live || !live.updated_at) {
      pulse.classList.add("off");
      box.innerHTML = empty("Aucun sondage lmstat disponible.");
      return;
    }
    pulse.classList.toggle("off", minutesOld(live.updated_at) > STALE_MINUTES);
    box.innerHTML = `<div class="live-hero"><div class="donut" id="live-donut"></div><div class="live-stats">` +
      `<div><b>${fmt(live.in_use, "int")}</b> / ${fmt(live.issued, "int")} jetons utilisés</div>` +
      `<div><b>${fmt(live.users, "int")}</b> utilisateur${live.users > 1 ? "s" : ""} connecté${live.users > 1 ? "s" : ""}</div>` +
      `<small class="muted">Sondage ${esc(ago(live.updated_at))}</small></div></div>` +
      `<div class="meter-list tight">` + live.products.slice(0, 4).map(p => {
        const u = p.issued ? p.in_use / p.issued : 0;
        return `<div class="meter-row"><div class="name">${esc(p.product)}</div><div class="num">${fmt(p.in_use, "int")}/${fmt(p.issued, "int")}</div>` +
          `<div class="meter"><span class="fill ${meterClass(u)}" style="width:${pctWidth(u)}"></span></div></div>`;
      }).join("") + `</div>`;
    C.donut($("live-donut"), live.in_use, live.issued);
  }

  /* -- Vue en direct ------------------------------------------------------------------ */
  function renderLive(r) {
    const live = r.live || { products: [] };
    const v = $("view");
    const stale = minutesOld(live.updated_at) > STALE_MINUTES;
    v.innerHTML =
      `<section class="kpi-grid">${r.kpis.map(k => `<div class="kpi"><span class="label">${esc(k.label)}</span><span class="value">${fmt(k.value, k.type)}</span></div>`).join("")}</section>` +
      `<div class="live-bar"><span><span class="pulse${stale || !live.updated_at ? " off" : ""}"></span>` +
      (live.updated_at ? `Dernier sondage ${esc(ago(live.updated_at))} (${fmt(live.updated_at, "datetime")}) · actualisation automatique chaque minute` : "Aucun sondage lmstat disponible") +
      `</span><input type="search" id="live-search" placeholder="Rechercher un produit ou un utilisateur" aria-label="Rechercher un produit ou un utilisateur"></div>` +
      `<section class="live-grid" id="live-grid"></section>`;
    const search = $("live-search");
    search.value = liveFilter;
    search.addEventListener("input", ev => { liveFilter = ev.target.value.toLowerCase(); drawLiveCards(live); });
    drawLiveCards(live);
  }
  function drawLiveCards(live) {
    const q = liveFilter;
    const items = live.products.filter(p => !q || (p.product + " " + p.server + " " + p.users.map(u => u.user + " " + u.username + " " + u.host).join(" ")).toLowerCase().includes(q));
    $("live-grid").innerHTML = items.length ? items.map(p => {
      const u = p.utilization || 0;
      const [sev, label] = p.issued && p.free !== null && p.free <= 0 ? ["critical", "Saturé"] : u >= 0.85 ? ["warning", "Tendu"] : ["good", "Disponible"];
      const users = p.users.length ? p.users.map(x =>
        `<div class="user">${avatar(x.user, x.username)}<div><div>${esc(x.user)}</div><small>${esc(x.host || "")}${x.department ? " · " + esc(x.department) : ""}</small></div>` +
        `<small title="depuis le ${fmt(x.since, "datetime")}">${x.licenses > 1 ? `${x.licenses} jetons · ` : ""}${esc(since(x.since))}</small></div>`).join("")
        : `<small class="muted">${p.in_use ? "Utilisateurs non identifiés pour l'instant" : "Aucun utilisateur"}</small>`;
      return `<article class="card live-card"><div class="top"><div><h2>${esc(p.product)}</h2><small>${esc(p.server)}</small></div>` +
        `<span class="badge sev sev-${sev}">${label}</span></div>` +
        `<div class="count">${fmt(p.in_use, "int")} <small>/ ${fmt(p.issued, "int")} jetons · ${fmt(p.free, "int")} libre${p.free > 1 ? "s" : ""}</small></div>` +
        `<div class="meter"><span class="fill ${meterClass(u)}" style="width:${pctWidth(u)}"></span></div>` +
        (p.queued ? `<span class="badge sev sev-serious">${p.queued} en file d'attente</span>` : "") +
        `<div class="users">${users}</div></article>`;
    }).join("") : empty(q ? "Aucun produit ne correspond à la recherche." : "Aucun produit suivi.");
  }

  /* -- Rapports détaillés ---------------------------------------------------------------- */
  function renderReport(r) {
    if (!$("table-title")) skeleton(r.type);
    $("kpis").innerHTML = r.kpis.map(x => `<div class="kpi"><div class="label">${esc(x.label)}</div><div class="value">${fmt(x.value, x.type)}</div></div>`).join("");
    $("notes").innerHTML = r.notes.map(n => `<li>${esc(n)}</li>`).join("");
    renderChart(r);
    $("table-title").textContent = r.title;
    renderTable();
  }

  function renderChart(r) {
    const card = $("chart-card");
    const c = r.chart;
    if (!card) return;
    if (!c || c.kind === "matrix") { card.hidden = true; return; }
    card.hidden = false;
    $("chart-title").textContent = c.title;
    if (c.kind === "line") C.line($("chart"), $("legend"), c.series.length === 1 ? { ...c, series: [{ ...c.series[0], area: true }] } : c);
    else if (c.kind === "bar") C.bar($("chart"), $("legend"), c);
    else if (c.kind === "heatmap") C.heatmap($("chart"), $("legend"), c);
  }

  const NUMERIC = new Set(["int", "float", "pct", "hours", "money"]);
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

  function adviceBadge(v) {
    const sev = /^Pénurie/.test(v) ? "critical" : /^Saturations/.test(v) ? "warning" : /^Réduire/.test(v) ? "info" : "good";
    return `<span class="badge sev sev-${sev}">${esc(v)}</span>`;
  }

  function renderTable() {
    const r = current;
    const t = $("table");
    if (!r || !t) return;
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
      let content;
      if (c.key === "status") {
        const ok = v === "OK";
        content = `<span class="status"><span class="dot" style="background:var(${ok ? "--good" : "--critical"})"></span>${ok ? "✓ OK" : "⚠ " + esc(v)}</span>`;
      } else if (c.key === "advice" && v) content = adviceBadge(v);
      else if (c.type === "pct" && !heat && v !== null && v !== undefined) {
        content = `<span class="cell-meter"><span>${esc(fmt(v, c.type))}</span><i><b class="${meterClass(v)}" style="width:${pctWidth(v)}"></b></i></span>`;
      } else content = esc(fmt(v, c.type));
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
    const html = button.innerHTML;
    button.disabled = true;
    button.textContent = "Export…";
    try {
      const r = await fetch("api/export?" + q.toString(), { credentials: "same-origin" });
      if (r.status === 401) { toLogin(); return; }
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
      button.innerHTML = html;
    }
  }

  init();
})();
