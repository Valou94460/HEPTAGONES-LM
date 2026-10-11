/* HeptagonesLM — logique de l'interface : navigation, filtres, tableau de bord,
   vue en direct, rapports détaillés et exports. */
"use strict";

(() => {
  const C = HeptagonesLMCharts;
  const { fmt, esc } = C;
  const $ = id => document.getElementById(id);
  const PAGE = 200;
  const LIST_KEYS = ["server", "product", "user", "department", "site"];
  const LIVE_REFRESH_MS = 60000;
  const STALE_MINUTES = 60;
  const DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];

  /* Navigation : six entrées de menu ; chaque rapport reste une vue (state.type) affichée sous forme d'onglet.
     Les anciens liens (#type=contention, #type=summary…) continuent donc de fonctionner. */
  const GROUPS = [
    { id: "dashboard", label: "Tableau de bord", desc: "Ce qui demande votre attention", icon: "dashboard", types: ["dashboard"] },
    { id: "live", label: "En direct", desc: "Qui utilise quoi, maintenant", icon: "live", types: ["live"] },
    { id: "analysis", label: "Analyse", desc: "Synthèse, usage, répartition, croisement", icon: "summary", types: ["summary", "timeseries", "breakdown", "matrix", "heatmap"] },
    { id: "optimization", label: "Optimisation", desc: "Efficacité, coûts, inutilisées, contention, refus", icon: "optimization", types: ["optimization", "efficiency", "unused", "named", "reservations", "contention", "denials"] },
    { id: "purchasing", label: "Achats", desc: "Prévision et renouvellements", icon: "forecast", types: ["forecast", "renewal"] },
    { id: "data", label: "Données", desc: "Sessions et état des collectes", icon: "collections", types: ["sessions", "collections"] },
  ];
  const ADMIN_GROUP = { id: "admin", label: "Administration", desc: "Serveurs, imports, coûts, rapports", icon: "admin", types: ["admin"] };
  const groupOf = type => GROUPS.concat([ADMIN_GROUP]).find(g => g.types.includes(type)) || GROUPS[2];
  const lastTab = {};      // dernier onglet ouvert de chaque entrée de menu
  const ICONS = {
    dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    live: '<circle cx="12" cy="12" r="2.5"/><path d="M7.8 16.2a6 6 0 0 1 0-8.4M16.2 7.8a6 6 0 0 1 0 8.4M4.9 19.1a10 10 0 0 1 0-14.2M19.1 4.9a10 10 0 0 1 0 14.2"/>',
    summary: '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
    timeseries: '<path d="M3 17l5-6 4 3 7-8"/><path d="M3 21h18"/>',
    breakdown: '<path d="M12 3a9 9 0 1 0 9 9h-9z"/><path d="M15 3.5A9 9 0 0 1 20.5 9H15z"/>',
    matrix: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    heatmap: '<rect x="3" y="3" width="5" height="5" rx="1"/><rect x="10" y="3" width="5" height="5" rx="1"/><rect x="17" y="3" width="4" height="5" rx="1"/><rect x="3" y="10" width="5" height="5" rx="1"/><rect x="10" y="10" width="5" height="5" rx="1"/><rect x="3" y="17" width="5" height="4" rx="1"/>',
    unused: '<rect x="4" y="6" width="16" height="12" rx="2"/><path d="M4 10h16M9 14h6"/>',
    named: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 11l2 2 4-4"/>',
    reservations: '<path d="M7 3v4M17 3v4"/><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M9 13l2 2 4-4"/>',
    efficiency: '<path d="M12 13l4-4"/><path d="M3.5 17a9 9 0 1 1 17 0"/><circle cx="12" cy="13" r="1"/>',
    optimization: '<circle cx="12" cy="12" r="9"/><path d="M15 9.5c-.6-1-1.7-1.5-3-1.5-1.7 0-3 .9-3 2s1 1.7 3 2 3 .9 3 2-1.3 2-3 2c-1.3 0-2.4-.5-3-1.5M12 6.5V8M12 16v1.5"/>',
    contention: '<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
    denials: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    forecast: '<path d="M3 17l5-5 4 3 4-5"/><path d="M16 10l5-4" stroke-dasharray="2 2.5"/><path d="M3 21h18"/>',
    renewal: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/><path d="M12 8v4l2.5 1.5"/>',
    sessions: '<path d="M4 6h16M4 12h16M4 18h10"/>',
    collections: '<path d="M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z"/><path d="M4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    admin: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
    other: '<circle cx="12" cy="12" r="9"/>',
  };
  /* Filtres : seuls ceux que la vue prend en compte sont affichés (les autres seraient sans effet). */
  const FILTERS_BASE = ["period", "server", "product"];
  const FILTER_SCOPE = {
    live: ["server", "product"], timeseries: FILTERS_BASE, heatmap: FILTERS_BASE, contention: FILTERS_BASE,
    efficiency: FILTERS_BASE, optimization: FILTERS_BASE, unused: FILTERS_BASE, reservations: FILTERS_BASE,
    forecast: FILTERS_BASE, renewal: FILTERS_BASE, denials: [...FILTERS_BASE, "user", "department"],
    named: [...FILTERS_BASE, "user", "department"], collections: ["period", "server"],
  };
  const FILTER_NOTE = { forecast: "Part de la fin de la période choisie ; lit les 24 derniers mois.", renewal: "Part de la fin de la période choisie ; usage des 90 derniers jours.", live: "Dernier sondage : pas de période." };
  /* Colonnes essentielles des tableaux larges ; « Afficher plus de colonnes » donne le reste (les exports contiennent tout). */
  const COMPACT = {
    summary: ["product", "issued", "peak", "business_utilization", "saturated_hours", "denials"],
    optimization: ["product", "advice", "savings", "annual_cost", "issued", "recommended"],
    forecast: ["product", "advice", "needed_12", "gap", "impact", "issued"],
    renewal: ["product", "expires_on", "advice", "delta", "impact"],
    sessions: ["product", "username", "hostname", "checkout_time", "checkin_time", "hours_in_period", "server"],
  };
  /* Glossaire : définition affichée au survol (ou au focus) du « ? » des en-têtes de colonnes. */
  const GLOSSARY = {
    issued: "Nombre de jetons (licences utilisables par une personne à la fois) que le serveur met à disposition.",
    peak: "Plus grand nombre de jetons utilisés en même temps sur la période.",
    avg: "Nombre moyen de jetons utilisés en même temps.",
    avg_utilization: "Part des jetons utilisés en moyenne, jour et nuit.",
    peak_utilization: "Part des jetons utilisés au moment du pic.",
    business_utilization: "Part des jetons utilisés en moyenne du lundi au vendredi, aux heures ouvrées de l'entreprise.",
    saturated_hours: "Heures pendant lesquelles tous les jetons étaient pris : un utilisateur de plus aurait été refusé.",
    denials: "Demandes de licence refusées parce qu'aucun jeton n'était libre.",
    recommended: "Nombre de jetons qui couvre 95 % du temps ouvré : au-delà, les pics restent ponctuels.",
    surplus: "Jetons émis au-delà du nombre recommandé : économie possible.",
    unused: "Jetons qui n'ont jamais servi sur la période.",
    half_share: "Part du temps où au moins la moitié des jetons est utilisée.",
    saturated_share: "Part du temps où tous les jetons sont pris.",
    savings: "Économie annuelle si l'excédent n'est pas renouvelé (jetons en trop × coût annuel d'un jeton).",
    annual_cost: "Coût annuel de ces jetons : jetons émis × coût annuel d'un jeton.",
    cost_per_hour: "Coût de la période divisé par les heures d'utilisation.",
    trend: "Jetons gagnés (ou perdus) par mois sur le pic hebdomadaire, tendance linéaire.",
    range_12: "Plage dans laquelle le pic réel a 9 chances sur 10 de rester dans 12 mois.",
    needed_3: "Jetons qui couvrent le plus haut pic prévu sur les 3 prochains mois.",
    needed_6: "Jetons qui couvrent le plus haut pic prévu sur les 6 prochains mois.",
    needed_12: "Jetons qui couvrent le plus haut pic prévu sur les 12 prochains mois.",
    margin_12: "Haut de la fourchette à 90 % : jetons à prévoir pour ne presque jamais saturer.",
    gap: "Jetons nécessaires à 12 mois moins jetons émis : positif, il en manquera.",
    impact: "Budget annuel supplémentaire (+) ou économie (−) correspondant à l'écart.",
    forecast: "Besoin prévu sur l'année qui suit l'échéance.",
    proposal: "Nombre de jetons à renouveler : recommandé ou besoin prévu, jamais à la baisse en cas de refus.",
    delta: "Jetons à renouveler moins jetons actuels.",
    days_left: "Jours restant avant l'échéance.",
    source: "D'où vient la date d'échéance : fichier de licence, contrat déclaré ou abonnement Autodesk.",
  };
  const EMPTY_HINT = {
    renewal: ["Aucune échéance dans les 12 mois.", "Les dates viennent du fichier de licence ou de la configuration (renewal d'une règle products, ou fin d'abonnement Autodesk saisie dans l'Administration)."],
    forecast: ["Historique insuffisant pour une prévision.", "Il faut au moins 4 semaines de données collectées ; la tendance apparaît dès 8 semaines et la saisonnalité dès 12 mois."],
    unused: ["Aucune licence inutilisée sur la période : tous les produits ont servi.", ""],
    named: ["Aucune licence Autodesk nommée connue.", "Importez un export du portail Autodesk ou connectez l'API Autodesk."],
    reservations: ["Aucune réservation détectée.", "Elles viennent des lignes RESERVE du fichier d'options des serveurs FlexLM."],
    denials: ["Aucun refus sur la période.", "Les refus ne sont connus que des serveurs dont le journal est collecté."],
  };
  const allColumns = {};
  let siteAvailable = false;
  const TODO_LABEL = { critical: "Urgent", serious: "Important", warning: "À préparer", info: "Économie" };
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
    server: [], product: [], user: [], department: [], site: [],
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
      // Panneau aligné à droite de la pastille s'il déborderait de la fenêtre.
      const box = panel.getBoundingClientRect();
      if (box.right > window.innerWidth - 8) { panel.style.left = "auto"; panel.style.right = "0"; }
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
    if (save) { try { localStorage.setItem("heptagoneslm_theme", choice); } catch (e) { /* stockage indisponible */ } }
  }

  /* -- Menu mobile ------------------------------------------------------------ */
  function closeMenu() { $("sidebar").classList.remove("open"); $("menu").setAttribute("aria-expanded", "false"); }

  /* -- Visite guidée (première connexion) -------------------------------------------- */
  let tour = null;   // { step, steps }
  function tourSteps() {
    const role = meta.admin ? "admin" : (meta.access && !meta.access.global ? "manager" : "reader");
    const intro = {
      reader: ["Bienvenue dans HeptagonesLM", "Vous voyez l'usage de toutes les licences, en lecture seule. Cette visite dure une minute."],
      manager: ["Bienvenue dans HeptagonesLM", `Vous ne voyez que vos services (${(meta.access.departments || []).join(", ") || "périmètre de votre rôle"}), exports compris. Cette visite dure une minute.`],
      admin: ["Bienvenue, administrateur", "Vous voyez tout et vous pouvez configurer l'application. Cette visite dure une minute, puis une checklist vous guide."],
    }[role];
    const last = role === "admin"
      ? { title: "Terminez la configuration", text: "L'Administration contient une checklist de démarrage : elle montre ce qui manque (coûts, échéances, projets) et évite les pages vides.", type: "admin", target: "#checklist" }
      : { title: "Exporter et demander de l'aide", text: "Chaque vue s'exporte en Excel, CSV ou JSON. Passez la souris sur un « ? » d'un en-tête de colonne pour lire sa définition.", type: "summary", target: ".exports" };
    return [
      { title: intro[0], text: intro[1], type: "dashboard", target: null },
      { title: "Ce qui demande votre attention", text: "Le tableau de bord liste ce qu'il y a à traiter : refus, économies, échéances. Chaque carte ouvre la bonne vue.", type: "dashboard", target: "#todo" },
      { title: "Un menu en six entrées", text: "Les vues sont regroupées en onglets : Analyse, Optimisation, Achats, Données.", type: "dashboard", target: "#nav", menu: true },
      { title: "Des filtres qui s'adaptent", text: "Seuls les filtres utiles à la vue s'affichent. Les filtres actifs restent en surbrillance et le bouton Réinitialiser les efface.", type: "summary", target: ".filterbar" },
      last,
    ];
  }
  function postPrefs(action) {
    return fetch("api/prefs", { method: "POST", credentials: "same-origin", headers: { "X-HeptagonesLM": "1", "Content-Type": "application/json" }, body: JSON.stringify({ onboarding: action }) }).catch(() => null);
  }
  function startTour() {
    tour = { step: 0, steps: tourSteps() };
    showTourStep();
  }
  function endTour(done = true) {
    tour = null;
    document.querySelectorAll(".tour-spot").forEach(e => e.classList.remove("tour-spot"));
    const layer = $("tour-layer"); if (layer) layer.remove();
    closeMenu();
    if (done) postPrefs("done");
  }
  async function showTourStep() {
    if (!tour) return;
    const st = tour.steps[tour.step];
    if (state.type !== st.type) { go({ type: st.type }); await waitForView(); }
    if (!tour) return;
    document.querySelectorAll(".tour-spot").forEach(e => e.classList.remove("tour-spot"));
    if (st.menu && window.innerWidth <= 960) $("sidebar").classList.add("open"); else closeMenu();
    let layer = $("tour-layer");
    if (!layer) { layer = document.createElement("div"); layer.id = "tour-layer"; document.body.appendChild(layer); }
    const n = tour.steps.length;
    layer.innerHTML = `<div class="tour-scrim"></div><div class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title">` +
      `<h2 id="tour-title">${esc(st.title)}</h2><p>${esc(st.text)}</p><div class="tour-row"><span class="tour-dots" aria-label="Étape ${tour.step + 1} sur ${n}">` +
      tour.steps.map((_, i) => `<i class="${i <= tour.step ? "on" : ""}"></i>`).join("") + `</span>` +
      (tour.step > 0 ? `<button type="button" class="btn ghost" id="tour-prev">Précédent</button>` : `<button type="button" class="btn ghost" id="tour-skip">Passer</button>`) +
      `<button type="button" class="btn primary" id="tour-next">${tour.step === n - 1 ? "Terminer" : "Suivant"}</button></div></div>`;
    const target = st.target && document.querySelector(st.target);
    if (target && !target.hidden) { target.classList.add("tour-spot"); target.scrollIntoView({ block: "nearest" }); }
    $("tour-next").addEventListener("click", () => { if (tour.step === n - 1) endTour(); else { tour.step++; showTourStep(); } });
    const prev = $("tour-prev"); if (prev) prev.addEventListener("click", () => { tour.step--; showTourStep(); });
    const skip = $("tour-skip"); if (skip) skip.addEventListener("click", () => endTour());
    $("tour-next").focus();
  }
  function waitForView() {
    return new Promise(resolve => {
      const started = Date.now();
      const tick = () => { if (!$("view").classList.contains("loading") && shownType === state.type) resolve(); else if (Date.now() - started > 8000) resolve(); else setTimeout(tick, 80); };
      setTimeout(tick, 60);
    });
  }
  let deepLink = false;     // la page a été ouverte sur un rapport précis
  async function maybeStartTour() {
    if (meta.demo) return;                       // pas de visite sur la démonstration
    if (deepLink) return;                        // lien profond (rapport partagé, retour de connexion) : on ne détourne pas
    try {
      const prefs = await fetchJSON("api/prefs");
      if (!prefs.onboarding_done) startTour();
    } catch (e) { /* préférences indisponibles : pas de visite */ }
  }
  document.addEventListener("keydown", ev => { if (ev.key === "Escape" && tour) endTour(); });

  /* -- Bulles d'aide ("?") ----------------------------------------------------------- */
  function bindHelp() {
    const show = el => { const b = el.getBoundingClientRect(); C.showTip(`<div class="t-row"><span>${esc(el.dataset.help)}</span></div>`, b.left, b.bottom); };
    document.addEventListener("mouseover", ev => { const h = ev.target.closest && ev.target.closest(".help"); if (h) show(h); });
    document.addEventListener("mouseout", ev => { if (ev.target.closest && ev.target.closest(".help")) C.hideTip(); });
    document.addEventListener("focusin", ev => { if (ev.target.classList && ev.target.classList.contains("help")) show(ev.target); });
    document.addEventListener("focusout", ev => { if (ev.target.classList && ev.target.classList.contains("help")) C.hideTip(); });
  }

  /* -- Initialisation ------------------------------------------------------------ */
  async function init() {
    let theme = "auto";
    try { theme = localStorage.getItem("heptagoneslm_theme") || "auto"; } catch (e) { /* stockage indisponible */ }
    setTheme(theme, false);
    document.querySelectorAll("[data-theme-choice]").forEach(b => b.addEventListener("click", () => setTheme(b.dataset.themeChoice)));
    $("menu").addEventListener("click", ev => {
      ev.stopPropagation();
      const open = $("sidebar").classList.toggle("open");
      $("menu").setAttribute("aria-expanded", String(open));
    });
    document.addEventListener("click", ev => { if (!$("sidebar").contains(ev.target)) closeMenu(); });

    bindHelp();
    deepLink = /type=/.test(location.hash);
    loadHash();
    try {
      meta = await fetchJSON("api/meta");
    } catch (e) {
      showError("Impossible de charger les métadonnées : " + e.message);
      return;
    }
    if (meta.admin) meta.reports.admin = "Administration";
    if (!meta.reports[state.type]) state.type = "dashboard";
    $("version").textContent = meta.version || "";
    if (meta.demo) $("demo-banner").hidden = false;
    fetchJSON("api/me").then(me => {
      if (me.auth === "none") return;
      const name = me.display_name || me.user;
      $("user-badge").hidden = false;
      $("user-name").textContent = name;
      $("user-name").title = (me.department ? `${me.user} — ${me.department}` : me.user) +
        (me.access ? `\n${me.access.label}` : "");
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
    // Rôle limité à des services : bandeau et classeur complet masqué (refusé par le serveur)
    if (meta.access && !meta.access.global) {
      $("coverage").textContent += ` · Vue limitée — ${meta.access.label}`;
      const full = document.querySelector('[data-export="full"]');
      if (full) full.hidden = true;
    }
    renderNav();
    renderHealth();

    // Filtres multiples
    multis.server = createMulti($("ms-server"), "Serveurs", meta.servers.map(s => ({ value: s, label: s })), "server");
    multis.product = createMulti($("ms-product"), "Produits", meta.products.map(p => ({ value: p.name, label: p.name, hint: p.servers.join(", ") })), "product");
    multis.user = createMulti($("ms-user"), "Utilisateurs", meta.users.map(u => ({ value: u.username, label: u.display_name ? `${u.display_name} (${u.username})` : u.username, hint: u.department || "" })), "user");
    const deps = meta.departments.map(d => ({ value: d, label: d }));
    deps.push({ value: "(non renseigné)", label: "(non renseigné)" });
    multis.department = createMulti($("ms-department"), "Services", deps, "department");
    // Sites : proposés dès que des règles « sites » classent les postes
    siteAvailable = (meta.sites || []).length > 0;
    if (siteAvailable) {
      const sites = meta.sites.map(x => ({ value: x, label: x }));
      sites.push({ value: "(non renseigné)", label: "(non renseigné)" });
      multis.site = createMulti($("ms-site"), "Sites", sites, "site");
    } else $("ms-site").hidden = true;

    // Période
    document.querySelectorAll("#presets [data-preset]").forEach(b => b.addEventListener("click", () => {
      state.preset = b.dataset.preset; applyPreset(); changed();
    }));
    for (const id of ["start", "end"]) $(id).addEventListener("change", ev => {
      if (!ev.target.value) return;
      state[id] = ev.target.value; state.preset = "custom"; changed();
    });
    $("reset").addEventListener("click", () => {
      Object.assign(state, { server: [], product: [], user: [], department: [], site: [] });
      Object.values(multis).forEach(m => m.refreshLabel()); changed();
    });
    document.querySelectorAll("[data-export]").forEach(b => b.addEventListener("click", () => doExport(b.dataset.export)));
    $("health").addEventListener("click", () => go({ type: "collections" }));

    // Liens internes des pages (délégation) : data-go="type" [+ data-*]
    $("view").addEventListener("click", ev => {
      const tab = ev.target.closest("[data-tab-type]");
      if (tab) { go({ type: tab.dataset.tabType }); return; }
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
    $("replay-tour").addEventListener("click", () => { closeMenu(); startTour(); });
    maybeStartTour();
  }

  function visibleGroups() {
    const placed = new Set(GROUPS.flatMap(g => g.types));
    const groups = GROUPS.map(g => ({ ...g, types: g.types.filter(k => meta.reports[k]) }));
    const others = Object.keys(meta.reports).filter(k => !placed.has(k) && k !== "admin");
    if (others.length) groups[2].types.push(...others);     // vue inconnue : rangée dans Analyse
    if (meta.admin) groups.push(ADMIN_GROUP);
    return groups.filter(g => g.types.length);
  }
  function renderNav() {
    const nav = $("nav");
    nav.innerHTML = visibleGroups().map(g =>
      (g.id === "admin" ? '<div class="nav-sep"></div>' : "") +
      `<button type="button" class="nav-item" data-group="${esc(g.id)}" data-type="${esc(g.types[0])}">${icon(g.icon)}<span class="nav-text"><span class="nav-label">${esc(g.label)}</span><small>${esc(g.desc)}</small></span></button>`
    ).join("");
    nav.querySelectorAll(".nav-item").forEach(b => b.addEventListener("click", () => {
      closeMenu();
      const g = visibleGroups().find(x => x.id === b.dataset.group);
      go({ type: (lastTab[g.id] && g.types.includes(lastTab[g.id])) ? lastTab[g.id] : g.types[0] });
    }));
  }
  function tabsHtml(type) {
    const g = visibleGroups().find(x => x.types.includes(type));
    if (!g || g.types.length < 2) return "";
    return `<div class="tabs" id="tabs" role="tablist" aria-label="${esc(g.label)}">` + g.types.map(k =>
      `<button type="button" role="tab" data-tab-type="${esc(k)}" aria-selected="${k === type}">${esc(meta.reports[k])}</button>`).join("") + "</div>";
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
    const item = document.querySelector('.nav-item[data-group="data"]');
    if (item && failing.length && !item.querySelector(".badge")) item.insertAdjacentHTML("beforeend", `<span class="badge sev sev-critical">${failing.length}</span>`);
  }

  /* -- Chargement ---------------------------------------------------------------- */
  function syncControls() {
    document.querySelectorAll("#presets [data-preset]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.preset === state.preset)));
    $("start").value = state.start; $("end").value = state.end;
    const grp = groupOf(state.type);
    lastTab[grp.id] = state.type;
    document.querySelectorAll(".nav-item").forEach(t => {
      if (t.dataset.group === grp.id) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
    });
    document.querySelectorAll("#tabs [data-tab-type]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tabType === state.type)));
    $("reset").hidden = !LIST_KEYS.some(k => state[k].length);
    const scope = FILTER_SCOPE[state.type] || ["period", "server", "product", "user", "department", "site"];
    const on = k => scope.includes(k);
    $("presets").hidden = !on("period"); document.querySelector(".filterbar .dates").hidden = !on("period");
    for (const k of ["server", "product", "user", "department"]) $("ms-" + k).hidden = !on(k);
    $("ms-site").hidden = !(on("site") && siteAvailable);
    const fn = $("filter-note"); fn.textContent = FILTER_NOTE[state.type] || ""; fn.hidden = !FILTER_NOTE[state.type];
    document.querySelector(".filterbar").hidden = state.type === "admin";   // pas de filtres sur l'administration
    const single = grp.types.length < 2;
    $("page-title").textContent = single ? ((meta && meta.reports[state.type]) || grp.label) : grp.label;
    document.title = `${meta && meta.demo ? "[Démonstration] " : ""}${(meta && meta.reports[state.type]) || grp.label} — HeptagonesLM`;
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
    try { sessionStorage.setItem("heptagoneslm_next_hash", location.hash); } catch (e) { /* stockage indisponible */ }
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
    if (state.type === "admin") { if (!opts.silent) renderAdmin(); return; }
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

  /* -- Administration (rôle administrateur) ------------------------------------------- */
  async function postAdmin(url, body, raw, headers) {
    const r = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "X-HeptagonesLM": "1", ...(raw ? headers : { "Content-Type": "application/json" }) },
      body: raw || JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
    return data;
  }
  const CHECK_TARGET = { servers: "servers-card", costs: "costs-card", renewals: "costs-card", projects: "projects-card", reports: "subs-card" };
  async function loadChecklist() {
    const box = $("checklist");
    if (!box) return;
    let c;
    try { c = await fetchJSON("api/admin/checklist"); } catch (e) { return; }
    if (!c || !c.items) return;
    const pct = c.total ? Math.round(100 * c.done / c.total) : 0;
    box.hidden = false;
    box.innerHTML = `<div class="card-head"><div><h2>Terminer la configuration</h2>
      <div class="sub">${c.done} étape${c.done > 1 ? "s" : ""} sur ${c.total} terminée${c.done > 1 ? "s" : ""}.</div></div></div>
      <div class="card-body"><div class="check-progress" aria-hidden="true"><i style="width:${pct}%"></i></div>
      <ul class="check-list">${c.items.map(i => `<li class="check-item"><span class="mark ${i.ok ? "ok" : ""}" aria-hidden="true">${i.ok ? "✓" : ""}</span>
        <span><b>${esc(i.label)}</b>${i.optional ? ' <span class="muted">(facultatif)</span>' : ""}${i.detail ? `<br><span class="muted">${esc(i.detail)}</span>` : ""}</span>
        ${!i.ok && CHECK_TARGET[i.action] ? `<button type="button" class="btn small ghost" data-goto="${CHECK_TARGET[i.action]}">Y aller</button>` : ""}</li>`).join("")}</ul></div>`;
    box.querySelectorAll("[data-goto]").forEach(b => b.addEventListener("click", () => {
      const t = $(b.dataset.goto); if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
    }));
  }
  let adminEdit = null;      // serveur en cours de modification : null (ajout) | nom d'origine
  async function renderAdmin() {
    shownType = "admin";
    const view = $("view");
    $("page-desc").textContent = "Serveurs de licences, imports (Autodesk, projets), coûts et échéances, rapports planifiés par e-mail.";
    let data;
    try { data = await fetchJSON("api/admin/servers"); showError(""); }
    catch (e) { showError(e.message); view.innerHTML = ""; return; }
    const ro = data.readonly;
    const status = s => !s.status ? '<span class="muted">pas encore collecté</span>'
      : `<span class="badge sev ${s.status === "OK" ? "sev-good" : "sev-critical"}">${esc(s.status)}</span> <span class="muted">${esc(fmt(s.last_run, "datetime"))}</span>`;
    view.innerHTML = `
      <section class="card" id="checklist" hidden style="margin-bottom:16px"></section>
      <section class="card" id="servers-card"><div class="card-head"><div><h2>Serveurs de licences</h2>
        <div class="sub">Pris en compte par la collecte suivante (toutes les 5 minutes). Les chemins de lmutil / rlmutil restent réglés par l'assistant d'installation.</div></div></div>
        <div class="card-body">
        ${ro ? `<div class="alert sev-warning"><span class="sev-icon" aria-hidden="true">!</span><div class="alert-body"><div class="alert-detail">${esc(ro)}</div></div></div>` : ""}
        <div class="table-wrap"><table><thead><tr><th>Nom</th><th>Adresse</th><th>Type</th><th>État</th><th>Dernière collecte</th><th></th></tr></thead><tbody>
        ${data.servers.map(s => `<tr><td><b>${esc(s.name)}</b></td><td><code>${esc(s.port_at_host)}</code></td><td>${s.license_type === "rlm" ? "RLM" : "FlexLM"}</td>
          <td>${s.enabled ? "Actif" : '<span class="muted">Désactivé</span>'}</td><td title="${esc(s.message || "")}">${status(s)}</td>
          <td style="white-space:nowrap">${ro ? "" : `<button type="button" class="btn small ghost" data-edit="${esc(s.name)}">Modifier</button><button type="button" class="btn small ghost" data-del="${esc(s.name)}">Supprimer</button>`}</td></tr>`).join("")}
        </tbody></table></div>
        ${ro ? "" : `<h3 id="srv-form-title" style="margin:18px 0 8px">Ajouter un serveur</h3>
        <div class="options" style="flex-wrap:wrap;gap:12px">
          <label class="field"><span>Nom</span><input type="text" id="srv-name" placeholder="SIEMENS" maxlength="40"></label>
          <label class="field"><span>Adresse port@serveur</span><input type="text" id="srv-target" placeholder="27000@licsrv01"></label>
          <label class="field"><span>Type</span><select id="srv-type"><option value="flexlm">FlexLM (lmstat)</option><option value="rlm">RLM (rlmstat)</option></select></label>
          <label class="field inline" style="align-self:flex-end"><input type="checkbox" id="srv-enabled" checked> <span>Actif</span></label>
        </div>
        <div class="options" style="gap:8px;margin-top:10px">
          <button type="button" class="btn" id="srv-test">Tester la connexion</button>
          <button type="button" class="btn primary" id="srv-save">Enregistrer</button>
          <button type="button" class="btn ghost" id="srv-cancel" hidden>Annuler</button>
        </div>
        <p id="srv-result" class="muted" style="margin-top:10px"></p>`}
        </div></section>
      <section class="card" style="margin-top:16px"><div class="card-head"><div><h2>Import d'un export Autodesk</h2>
        <div class="sub">Licences « utilisateur nommé » : rapport « Autodesk · utilisateurs nommés ».</div></div></div>
        <div class="card-body">
        <ol class="notes" style="margin-bottom:12px">
          <li>Sur <b>manage.autodesk.com</b> : <i>Rapports › Rapport d'utilisation</i>, puis <i>Exporter</i> (CSV ou Excel).</li>
          <li>Choisir le fichier ci-dessous puis « Importer ». Un même fichier importé deux fois n'est compté qu'une fois.</li>
          <li>Pour une récupération automatique chaque jour, sans export manuel : connecteur API Autodesk (abonnement Business Success Plan), réglé dans HeptagonesLM-Setup.exe › « Configurer les licences Autodesk ».</li>
        </ol>
        <div class="options" style="gap:8px"><input type="file" id="adsk-file" accept=".csv,.xlsx,.txt">
          <button type="button" class="btn primary" id="adsk-import">Importer</button></div>
        <p id="adsk-result" class="muted" style="margin-top:10px"></p>
        </div></section>
      <section class="card" style="margin-top:16px" id="costs-card"><div class="card-head"><div><h2>Coûts et échéances des licences Autodesk</h2>
        <div class="sub">Coût annuel d'une licence nommée par produit (« Optimisation &amp; coûts », refacturation) et date de fin d'abonnement (« Renouvellements »).</div></div></div>
        <div class="card-body" id="costs-body"><p class="muted">Chargement…</p></div></section>
      <section class="card" style="margin-top:16px" id="projects-card"><div class="card-head"><div><h2>Projets</h2>
        <div class="sub">Refacturation par projet : « Répartition » ou « Croisement », par Projet.</div></div></div>
        <div class="card-body">
        <ol class="notes" style="margin-bottom:12px">
          <li>Fichier CSV avec deux colonnes : <b>utilisateur</b> (identifiant Windows) et <b>projet</b>. Par exemple, export des membres d'un groupe AD :
            <code>Get-ADGroupMember "GG-Projet-A12" | Select SamAccountName, @{n="project";e={"A12"}} | Export-Csv projets.csv -NoTypeInformation -Encoding UTF8</code></li>
          <li>Un utilisateur absent du fichier est rangé par les règles <code>projects:</code> de la configuration (nom du poste, service), sinon sous « (non renseigné) ».</li>
        </ol>
        <div id="projects-list" class="muted">Chargement…</div>
        <div class="options" style="gap:8px;margin-top:10px"><input type="file" id="proj-file" accept=".csv,.txt">
          <label class="field inline"><input type="checkbox" id="proj-replace"> <span>Remplacer toutes les affectations</span></label>
          <button type="button" class="btn primary" id="proj-import">Importer</button></div>
        <p id="proj-result" class="muted" style="margin-top:10px"></p>
        </div></section>
      <section class="card" style="margin-top:16px" id="subs-card"><div class="card-head"><div><h2>Rapports planifiés</h2>
        <div class="sub">Envoyés par e-mail chaque jour, semaine, mois ou trimestre selon la période : vues choisies, filtres, classeur Excel joint (une feuille par vue).</div></div></div>
        <div class="card-body" id="subs-body"><p class="muted">Chargement…</p></div></section>`;
    loadChecklist();
    const form = () => ({ name: $("srv-name").value.trim(), port_at_host: $("srv-target").value.trim(),
      license_type: $("srv-type").value, enabled: $("srv-enabled").checked });
    const say = (id, msg, ok) => { const e = $(id); e.textContent = msg; e.style.color = ok === false ? "var(--critical)" : ok ? "var(--good)" : ""; };
    const setEdit = s => {
      adminEdit = s ? s.name : null;
      $("srv-form-title").textContent = s ? `Modifier ${s.name}` : "Ajouter un serveur";
      $("srv-name").value = s ? s.name : ""; $("srv-target").value = s ? s.port_at_host : "";
      $("srv-type").value = s ? s.license_type : "flexlm"; $("srv-enabled").checked = s ? s.enabled : true;
      $("srv-cancel").hidden = !s; say("srv-result", "");
    };
    view.querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click", () => setEdit(data.servers.find(s => s.name === b.dataset.edit))));
    view.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", async () => {
      if (!confirm(`Supprimer le serveur ${b.dataset.del} ? L'historique déjà collecté est conservé.`)) return;
      try { await postAdmin("api/admin/servers", { action: "delete", original: b.dataset.del }); renderAdmin(); }
      catch (e) { showError(e.message); }
    }));
    $("srv-cancel")?.addEventListener("click", () => setEdit(null));
    $("srv-test")?.addEventListener("click", async () => {
      say("srv-result", "Interrogation du serveur…");
      try { const r = await postAdmin("api/admin/servers", { action: "test", server: form() }); say("srv-result", r.message, r.ok); }
      catch (e) { say("srv-result", e.message, false); }
    });
    $("srv-save")?.addEventListener("click", async () => {
      try {
        await postAdmin("api/admin/servers", { action: "save", server: form(), original: adminEdit });
        adminEdit = null; await renderAdmin();
        say("srv-result", "Enregistré : pris en compte par la prochaine collecte.", true);
      } catch (e) { say("srv-result", e.message, false); }
    });
    try {
      const c = await fetchJSON("api/admin/costs");
      const body = $("costs-body");
      if (!c.costs.length) body.innerHTML = '<p class="muted">Aucun produit Autodesk connu : importer un export ou configurer la synchronisation par l\'API Autodesk.</p>';
      else {
        body.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Produit</th><th>Coût annuel / licence (${esc(meta.currency || "€")})</th><th>Fin d'abonnement</th></tr></thead><tbody>
          ${c.costs.map((x, i) => `<tr><td>${esc(x.product)}</td><td><input type="text" inputmode="decimal" data-cost="${i}" value="${x.cost ?? ""}" ${c.readonly ? "disabled" : ""} style="width:140px" aria-label="Coût annuel ${esc(x.product)}"></td>
            <td><input type="date" data-renewal="${i}" value="${esc(x.renewal || "")}" ${c.readonly ? "disabled" : ""} aria-label="Fin d'abonnement ${esc(x.product)}"></td></tr>`).join("")}
          </tbody></table></div>${c.readonly ? "" : `<div class="options" style="margin-top:10px"><button type="button" class="btn primary" id="costs-save">Enregistrer</button></div><p id="costs-result" class="muted"></p>`}`;
        $("costs-save")?.addEventListener("click", async () => {
          const costs = {}, renewals = {};
          body.querySelectorAll("[data-cost]").forEach(inp => { costs[c.costs[+inp.dataset.cost].product] = inp.value.trim(); });
          body.querySelectorAll("[data-renewal]").forEach(inp => { renewals[c.costs[+inp.dataset.renewal].product] = inp.value; });
          try { const r = await postAdmin("api/admin/costs", { costs, renewals }); say("costs-result", `Enregistré : ${r.count} produit(s) chiffré(s).`, true); }
          catch (e) { say("costs-result", e.message, false); }
        });
      }
    } catch (e) { $("costs-body").innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
    loadProjects();
    loadSubscriptions(say);
    $("proj-import").addEventListener("click", async () => {
      const f = $("proj-file").files[0];
      if (!f) { say("proj-result", "Choisir d'abord un fichier CSV utilisateur ; projet.", false); return; }
      try {
        const r = await postAdmin("api/admin/projects-import", null, await f.arrayBuffer(),
          { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(f.name), "X-Replace": $("proj-replace").checked ? "1" : "0" });
        say("proj-result", `Importé : ${r.users} utilisateur(s) répartis sur ${r.projects} projet(s).`, true);
        loadProjects();
      } catch (e) { say("proj-result", e.message, false); }
    });
    $("adsk-import").addEventListener("click", async () => {
      const f = $("adsk-file").files[0];
      if (!f) { say("adsk-result", "Choisir d'abord un fichier exporté du portail Autodesk.", false); return; }
      say("adsk-result", "Import en cours…");
      try {
        const r = await postAdmin("api/admin/autodesk-import", null, await f.arrayBuffer(),
          { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(f.name) });
        say("adsk-result", r.status === "importé" ? `Importé : ${r.rows} licence(s) nommée(s), rapport du ${fmt(r.report_date, "date")}.`
          : `Ce fichier a déjà été importé (${r.file}).`, true);
      } catch (e) { say("adsk-result", e.message, false); }
    });
  }

  async function loadProjects() {
    const box = $("projects-list");
    try {
      const d = await fetchJSON("api/admin/projects");
      box.classList.remove("muted");
      const rows = d.projects.map(p => `<tr><td>${esc(p.project)}</td><td>${fmt(p.users, "int")}</td><td>${esc(fmt(p.updated_at, "datetime"))}</td></tr>`).join("");
      box.innerHTML = (rows ? `<div class="table-wrap"><table><thead><tr><th>Projet (import)</th><th>Utilisateurs</th><th>Mis à jour</th></tr></thead><tbody>${rows}</tbody></table></div>`
        : "<p class=\"muted\">Aucune affectation importée.</p>") +
        (d.rules.length ? `<p class="muted">Règles de la configuration : ${d.rules.map(esc).join(", ")}.</p>` : "");
    } catch (e) { box.textContent = e.message; }
  }
  let subEdit = null;        // rapport planifié en cours de modification
  async function loadSubscriptions(say) {
    const body = $("subs-body");
    let d;
    try { d = await fetchJSON("api/admin/subscriptions"); } catch (e) { body.innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
    const viewLabel = Object.fromEntries(d.views.map(v => [v.key, v.label]));
    const periodLabel = Object.fromEntries(d.periods.map(v => [v.key, v.label]));
    const ro = d.readonly || (!d.smtp && "Serveur SMTP non configuré : renseigner l'envoi d'e-mails dans HeptagonesLM-Setup.exe pour planifier des rapports.");
    const list = d.subscriptions.length ? `<div class="table-wrap"><table><thead><tr><th>Rapport</th><th>Période</th><th>Vues</th><th>Destinataires</th><th></th></tr></thead><tbody>
      ${d.subscriptions.map(s => `<tr><td><b>${esc(s.name)}</b></td><td>${esc(periodLabel[s.period || "last-month"] || s.period)}</td>
        <td>${(s.views || []).length ? s.views.map(v => esc(viewLabel[v] || v)).join(", ") : '<span class="muted">Rapport standard</span>'}</td>
        <td>${esc([].concat(s.recipients || []).join(", "))}</td>
        <td style="white-space:nowrap">${ro ? "" : `<button type="button" class="btn small ghost" data-sedit="${esc(s.name)}">Modifier</button><button type="button" class="btn small ghost" data-sdel="${esc(s.name)}">Supprimer</button>`}</td></tr>`).join("")}
      </tbody></table></div>` : '<p class="muted">Aucun rapport planifié.</p>';
    const filters = LIST_KEYS.filter(k => state[k].length).map(k => `${k} : ${state[k].join(", ")}`).join(" · ");
    body.innerHTML = (ro ? `<div class="alert sev-warning"><span class="sev-icon" aria-hidden="true">!</span><div class="alert-body"><div class="alert-detail">${esc(ro)}</div></div></div>` : "") + list +
      (ro ? "" : `<h3 id="sub-form-title" style="margin:18px 0 8px">Nouveau rapport planifié</h3>
      <div class="options" style="flex-wrap:wrap;gap:12px">
        <label class="field"><span>Nom</span><input type="text" id="sub-name" maxlength="100" placeholder="Achats mensuel"></label>
        <label class="field" style="min-width:320px;flex:1"><span>Destinataires (séparés par des virgules)</span><input type="text" id="sub-to" placeholder="achats@entreprise.fr, dsi@entreprise.fr"></label>
        <label class="field"><span>Période</span><select id="sub-period">${d.periods.map(p => `<option value="${p.key}">${esc(p.label)}</option>`).join("")}</select></label>
        <label class="field"><span>Répartition par</span><select id="sub-dim">${d.dimensions.map(p => `<option value="${p.key}">${esc(p.label)}</option>`).join("")}</select></label>
      </div>
      <fieldset class="checks" style="border:0;padding:0;margin:12px 0 0"><legend class="muted" style="margin-bottom:6px">Vues du rapport (aucune : rapport standard par produit)</legend>
        <div style="display:flex;flex-wrap:wrap;gap:6px 16px">${d.views.map(v => `<label class="field inline"><input type="checkbox" data-view="${v.key}"> <span>${esc(v.label)}</span></label>`).join("")}</div></fieldset>
      <label class="field inline" style="margin-top:10px"><input type="checkbox" id="sub-filters"> <span>Reprendre les filtres actuels de l'interface${filters ? ` (${esc(filters)})` : " (aucun filtre actif)"}</span></label>
      <label class="field inline" style="margin-top:6px"><input type="checkbox" id="sub-excel" checked> <span>Joindre le classeur Excel</span></label>
      <div class="options" style="gap:8px;margin-top:12px;flex-wrap:wrap">
        <button type="button" class="btn primary" id="sub-save">Enregistrer</button>
        <button type="button" class="btn ghost" id="sub-cancel" hidden>Annuler</button>
        <label class="field inline"><span>Essai vers</span><input type="email" id="sub-test-to" placeholder="moi@entreprise.fr"></label>
        <button type="button" class="btn" id="sub-test">Envoyer un essai</button>
      </div>
      <p id="sub-result" class="muted" style="margin-top:10px"></p>`);
    if ($("sub-dim")) $("sub-dim").value = "department";
    let kept = {};           // filtres d'un rapport modifié (conservés tant que la case n'est pas cochée)
    const entry = () => {
      const e = { name: $("sub-name").value.trim(), recipients: $("sub-to").value, period: $("sub-period").value,
        dimension: $("sub-dim").value, attach_excel: $("sub-excel").checked,
        views: [...body.querySelectorAll("[data-view]")].filter(c => c.checked).map(c => c.dataset.view) };
      const src = $("sub-filters").checked ? { servers: state.server, products: state.product, users: state.user, departments: state.department, sites: state.site } : kept;
      return { ...e, ...src };
    };
    const setEdit = s => {
      subEdit = s ? s.name : null;
      kept = s ? { servers: s.servers || [], products: s.products || [], users: s.users || [], departments: s.departments || [], sites: s.sites || [] } : {};
      $("sub-form-title").textContent = s ? `Modifier « ${s.name} »` : "Nouveau rapport planifié";
      $("sub-name").value = s ? s.name : ""; $("sub-to").value = s ? [].concat(s.recipients || []).join(", ") : "";
      $("sub-period").value = s ? (s.period || "last-month") : "last-month"; $("sub-dim").value = s ? (s.dimension || "department") : "department";
      $("sub-excel").checked = s ? s.attach_excel !== false : true; $("sub-filters").checked = false;
      body.querySelectorAll("[data-view]").forEach(c => { c.checked = !!(s && (s.views || []).includes(c.dataset.view)); });
      $("sub-cancel").hidden = !s; say("sub-result", "");
    };
    body.querySelectorAll("[data-sedit]").forEach(b => b.addEventListener("click", () => setEdit(d.subscriptions.find(s => s.name === b.dataset.sedit))));
    body.querySelectorAll("[data-sdel]").forEach(b => b.addEventListener("click", async () => {
      if (!confirm(`Supprimer le rapport planifié « ${b.dataset.sdel} » ?`)) return;
      try { await postAdmin("api/admin/subscriptions", { action: "delete", original: b.dataset.sdel }); subEdit = null; loadSubscriptions(say); }
      catch (e) { showError(e.message); }
    }));
    $("sub-cancel")?.addEventListener("click", () => setEdit(null));
    $("sub-save")?.addEventListener("click", async () => {
      try {
        await postAdmin("api/admin/subscriptions", { action: "save", subscription: entry(), original: subEdit });
        subEdit = null; await loadSubscriptions(say);
        say("sub-result", "Enregistré : envoyé par la tâche « HeptagonesLM - Envoi des rapports » (chaque jour, une fois par période).", true);
      } catch (e) { say("sub-result", e.message, false); }
    });
    $("sub-test")?.addEventListener("click", async () => {
      say("sub-result", "Préparation et envoi…");
      try { const r = await postAdmin("api/admin/subscriptions", { action: "test", subscription: entry(), to: $("sub-test-to").value }); say("sub-result", r.ok ? `Essai envoyé : ${r.message}` : r.message, r.ok); }
      catch (e) { say("sub-result", e.message, false); }
    });
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
      ${tabsHtml(type)}
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
            <button id="cols-toggle" class="btn ghost small" type="button" hidden></button>
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
    $("cols-toggle").addEventListener("click", () => { allColumns[current.type] = !allColumns[current.type]; renderTable(); });
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
      `<section class="todo-grid" id="todo" aria-label="À traiter"></section>` +
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

    // À traiter : pénuries, économies possibles, échéances proches
    const todo = r.todo || [];
    $("todo").innerHTML = todo.length ? todo.map(x =>
      `<article class="todo-card"><span class="todo-pill sev-${esc(x.severity)}">${esc(TODO_LABEL[x.severity] || "À voir")}</span>` +
      `<h2>${esc(x.title)}</h2><p>${esc(x.detail || "")}</p>` +
      (meta.reports[x.target] ? `<button type="button" class="btn primary" data-go="${esc(x.target)}">${esc(x.label)}</button>` : "") + `</article>`).join("")
      : `<div class="alerts-ok"><span class="status-dot sev-good" aria-hidden="true">✓</span>Rien à traiter : pas de pénurie, pas d'économie évidente, pas d'échéance dans les 12 mois.</div>`;

    if (meta.admin) {
      fetchJSON("api/admin/checklist").then(c => {
        if (!c || !c.total || c.done >= c.total || shownType !== "dashboard" || !$("todo")) return;
        const missing = c.items.filter(i => !i.ok && !i.optional), rest = missing.length;
        if (!rest) return;
        $("todo").insertAdjacentHTML("afterbegin", `<article class="todo-card"><span class="todo-pill sev-info">Mise en route</span>` +
          `<h2>Terminer la configuration (${c.done}/${c.total})</h2><p>${rest} étape${rest > 1 ? "s" : ""} à finir : ${esc(missing.map(i => i.label.toLowerCase()).join(", "))}.</p>` +
          `<button type="button" class="btn primary" data-go="admin">Ouvrir la liste</button></article>`);
      }).catch(() => {});
    }

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
      box.innerHTML = empty("Aucun sondage des serveurs de licences disponible.");
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
      (live.updated_at ? `Dernier sondage ${esc(ago(live.updated_at))} (${fmt(live.updated_at, "datetime")}) · actualisation automatique chaque minute` : "Aucun sondage des serveurs de licences disponible") +
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
        `<div class="user">${avatar(x.user, x.username)}<div><div>${esc(x.user)}${x.borrowed ? ' <span class="tag" title="Licence empruntée : utilisation hors ligne">emprunt</span>' : ""}</div><small>${esc(x.host || "")}${x.site ? " · " + esc(x.site) : ""}${x.department ? " · " + esc(x.department) : ""}</small></div>` +
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

  function visibleColumns(r) {
    const btn = $("cols-toggle");
    const keys = COMPACT[r.type];
    const compact = keys ? keys.map(k => r.columns.find(c => c.key === k)).filter(Boolean) : [];
    if (compact.length < 3 || compact.length >= r.columns.length) { btn.hidden = true; return r.columns; }
    const all = !!allColumns[r.type];
    btn.hidden = false;
    btn.textContent = all ? "Masquer les colonnes détaillées" : `Afficher plus de colonnes (${r.columns.length - compact.length})`;
    return all ? r.columns : compact;
  }
  function renderTable() {
    const r = current;
    const t = $("table");
    if (!r || !t) return;
    if (!r.rows.length) {
      const hint = EMPTY_HINT[r.type];
      t.innerHTML = `<tbody><tr><td class="empty">` + (hint
        ? `<strong>${esc(hint[0])}</strong>${hint[1] ? `<br>${esc(hint[1])}` : ""}` + (meta.admin && (r.type === "renewal" || r.type === "named") ? `<br><button type="button" class="link" data-go="admin">Ouvrir l'Administration →</button>` : "")
        : "Aucune donnée pour ces filtres sur la période.") + `</td></tr></tbody>`;
      $("cols-toggle").hidden = true;
      $("row-count").textContent = "";
      $("more").hidden = true;
      return;
    }
    const rows = sortedRows();
    const heat = r.type === "matrix" || r.type === "heatmap";
    let max = 0;
    if (heat) for (const row of rows) for (const c of r.columns) if (NUMERIC.has(c.type) && c.key !== "total" && row[c.key] > max) max = row[c.key];

    const cols = visibleColumns(r);
    const head = "<thead><tr>" + cols.map(c => {
      const sort = table.sortKey === c.key ? (table.sortDir > 0 ? "ascending" : "descending") : "none";
      const help = GLOSSARY[c.key] ? ` <span class="help" tabindex="0" role="note" aria-label="${esc(GLOSSARY[c.key])}" data-help="${esc(GLOSSARY[c.key])}">?</span>` : "";
      return `<th class="${NUMERIC.has(c.type) ? "num" : ""}" data-key="${esc(c.key)}" aria-sort="${sort}" scope="col">${esc(c.label)}${help}</th>`;
    }).join("") + "</tr></thead>";
    const body = rows.slice(0, table.shown).map(row => "<tr>" + cols.map(c => {
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
    t.querySelectorAll("th").forEach(th => th.addEventListener("click", ev => {
      if (ev.target.closest(".help")) return;
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
      a.download = m ? m[1] : `HeptagonesLM_export.${format === "full" ? "xlsx" : format}`;
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
