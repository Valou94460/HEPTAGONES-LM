/* HeptagonesLM — assistant d'installation : navigation, formulaires, tests, installation, vérification. */
"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SECRETS = ["service_password", "db_password", "smtp_password", "itsm_password", "itsm_app_token", "autodesk_client_secret"];
  const BASE = {
    install_dir: "C:\\HeptagonesLM", service_account: "", service_password: "", collect_interval: 5,
    db_mode: "sqlserver", db_server: "", db_name: "HeptagonesLM", db_driver: "ODBC Driver 18 for SQL Server", db_auth: "windows",
    db_user: "", db_password: "", db_encrypt: true, db_trust_cert: false, db_create: true,
    lmutil_path: "", rlmutil_path: "", servers: [],
    web_mode: "iis", web_port: 8765, public_host: "", access_group: "", iis_site: "HeptagonesLM", iis_port: 443,
    iis_cert_thumbprint: "", tls_cert: "", tls_key: "", ldap_servers: [], base_dn: "", upn_suffix: "", open_firewall: true,
    email_enabled: false, smtp_host: "", smtp_port: 25, smtp_security: "starttls", smtp_user: "", smtp_password: "",
    sender: "", recipients: [], email_alerts: true,
    itsm_enabled: false, itsm_type: "servicenow", itsm_url: "", itsm_user: "", itsm_password: "", itsm_app_token: "",
    itsm_project: "", itsm_issue_type: "Incident", itsm_assignment_group: "", itsm_format: "teams", itsm_min_severity: "serious", itsm_ca_file: "",
    autodesk_enabled: false, autodesk_import_dir: "", autodesk_inactive_days: 30, autodesk_api_enabled: false,
    autodesk_client_id: "", autodesk_client_secret: "", autodesk_context_id: "", autodesk_callback_port: 8770,
  };
  let info = null;
  let A = {};
  let step = 0, maxStep = 0;
  let checks = null, blockingCount = 0, checksBusy = false, fixResult = null;
  let checksProgress = null;            // vérification en cours : {elapsed, steps: [{label, state, seconds}]}
  let preview = null, verification = null;
  let pollTimer = null, logCount = 0, install = { status: "idle" };
  const results = {};
  let toolScan = null;                 // recherche de lmutil / rlmutil : {busy} | {found, hosts} | {error}

  /* -- Utilitaires ------------------------------------------------------------------------ */
  async function api(path, body) {
    const opts = body === undefined ? { credentials: "same-origin" } : {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-HeptagonesLM-Setup": "1" }, body: JSON.stringify(body),
    };
    const r = await fetch(path, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(data.error || `HTTP ${r.status}`); e.data = data; throw e; }
    return data;
  }
  function showError(msg) { const e = $("error"); e.textContent = msg || ""; e.hidden = !msg; }
  function save() {
    try {
      const safe = { ...A }; SECRETS.forEach(k => delete safe[k]);
      sessionStorage.setItem("heptagoneslm_setup", JSON.stringify({ A: safe, step: Math.min(step, SUMMARY), maxStep: Math.min(maxStep, SUMMARY) }));
    } catch (e) { /* stockage indisponible */ }
  }
  function restore() {
    try { return JSON.parse(sessionStorage.getItem("heptagoneslm_setup") || "null"); } catch (e) { return null; }
  }
  const SEV = { ok: ["good", "✓"], warning: ["warning", "!"], error: ["critical", "✕"], info: ["info", "i"] };
  const dot = s => `<span class="status-dot sev-${(SEV[s] || SEV.info)[0]}" aria-hidden="true">${(SEV[s] || SEV.info)[1]}</span>`;
  const ICON = {
    check: '<path d="M4 12l5 5L20 6"/>', search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>', db: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
    shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>', monitor: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    ticket: '<path d="M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3a2 2 0 0 0 0 4v3a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-3a2 2 0 0 0 0-4z"/><path d="M14 5v14" stroke-dasharray="2 2"/>',
    chat: '<path d="M4 5h16v11H9l-5 4z"/>',
  };
  const icon = k => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[k]}</svg>`;

  /* -- Composants de formulaire -------------------------------------------------------------- */
  function input(name, label, o = {}) {
    const v = Array.isArray(A[name]) ? A[name].join(", ") : (A[name] ?? "");
    return `<label class="f ${o.full ? "full" : ""}" data-f="${name}"><span>${esc(label)}</span>
      <input type="${o.type || "text"}" data-bind="${name}" ${o.list ? `data-list="1"` : ""} value="${esc(v)}"
        placeholder="${esc(o.placeholder || "")}" ${o.type === "password" ? 'autocomplete="new-password"' : 'autocomplete="off" spellcheck="false"'}
        ${o.min !== undefined ? `min="${o.min}" max="${o.max}"` : ""} ${o.rerender ? 'data-rerender="1"' : ""}>
      ${o.hint ? `<span class="hint">${o.hint}</span>` : ""}<span class="ferr"></span></label>`;
  }
  function select(name, label, options, o = {}) {
    return `<label class="f ${o.full ? "full" : ""}" data-f="${name}"><span>${esc(label)}</span>
      <select data-bind="${name}" ${o.rerender ? 'data-rerender="1"' : ""}>${options.map(([v, l]) =>
        `<option value="${esc(v)}" ${String(A[name]) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>
      ${o.hint ? `<span class="hint">${o.hint}</span>` : ""}<span class="ferr"></span></label>`;
  }
  function toggle(name, label, sub, o = {}) {
    return `<label class="toggle ${o.full ? "full" : ""}" data-f="${name}"><input type="checkbox" data-bind="${name}" ${A[name] ? "checked" : ""}
      ${o.rerender ? 'data-rerender="1"' : ""}><span>${esc(label)}${sub ? `<small>${sub}</small>` : ""}</span></label>`;
  }
  function choices(name, items) {
    return `<div class="choices" role="radiogroup" data-f="${name}">${items.map(it => `
      <button type="button" class="choice" role="radio" aria-checked="${A[name] === it.value}" data-choice="${name}" data-value="${it.value}">
        ${it.tag ? `<span class="tag">${esc(it.tag)}</span>` : ""}<span class="ico">${icon(it.icon)}</span>
        <b>${esc(it.title)}</b><span>${esc(it.text)}</span></button>`).join("")}</div>`;
  }
  function testBlock(key, kind, label, extra = "") {
    return `<div class="test-row">${extra}<button type="button" class="btn" data-test="${key}" data-kind="${kind}">${esc(label)}</button>
      <div class="result" id="res-${key}">${renderResult(results[key])}</div></div>`;
  }
  function renderResult(r) {
    if (!r) return "";
    if (r.busy) return `<span class="spinner"></span><span>${esc(r.busy)}</span>`;
    return `${dot(r.status)}<div><b>${esc(r.message)}</b>${r.details && r.details.length ? `<ul>${r.details.map(d => `<li>${esc(d)}</li>`).join("")}</ul>` : ""}</div>`;
  }
  async function runTest(key, kind, extra = {}) {
    results[key] = { busy: "Test en cours…" };
    const el = $(`res-${key}`); if (el) el.innerHTML = renderResult(results[key]);
    try { results[key] = await api("/api/test", { kind, answers: A, ...extra }); }
    catch (e) { results[key] = { status: "error", message: e.message }; }
    const el2 = $(`res-${key}`); if (el2) el2.innerHTML = renderResult(results[key]);
  }

  function bind(root) {
    root.querySelectorAll("[data-bind]").forEach(el => {
      const name = el.dataset.bind;
      const handler = () => {
        if (el.type === "checkbox") A[name] = el.checked;
        else if (el.type === "number") A[name] = el.value === "" ? "" : Number(el.value);
        else if (el.dataset.list) A[name] = el.value.split(/[,;\s]+/).map(s => s.trim()).filter(Boolean);
        else A[name] = el.value;
        const f = el.closest(".f");
        if (f) { f.classList.remove("invalid"); const fe = f.querySelector(".ferr"); if (fe) fe.textContent = ""; }
        save();
        if (el.dataset.rerender) render();
      };
      el.addEventListener(el.tagName === "SELECT" || el.type === "checkbox" ? "change" : "input", handler);
    });
    root.querySelectorAll("[data-choice]").forEach(b => b.addEventListener("click", () => {
      A[b.dataset.choice] = b.dataset.value; save(); render();
    }));
    root.querySelectorAll("[data-test]").forEach(b => b.addEventListener("click", () => {
      const extra = {};
      if (b.dataset.kind === "lmstat") {
        const srv = A.servers[+b.dataset.index] || {};
        extra.port_at_host = srv.port_at_host || ""; extra.license_type = srv.license_type || "flexlm";
      }
      if (b.dataset.kind === "ldap") { extra.username = $("ldap-user")?.value || ""; extra.password = $("ldap-pass")?.value || ""; }
      if (b.dataset.kind === "smtp") extra.to = (A.recipients || [])[0];
      runTest(b.dataset.test, b.dataset.kind, extra);
    }));
  }
  function markErrors(errors) {
    document.querySelectorAll(".f.invalid").forEach(f => f.classList.remove("invalid"));
    document.querySelectorAll(".ferr").forEach(f => { f.textContent = ""; });
    for (const e of errors) {
      const f = document.querySelector(`[data-f="${CSS.escape(e.field)}"]`);
      if (f) { f.classList.add("invalid"); const fe = f.querySelector(".ferr"); if (fe) fe.textContent = e.message; }
    }
    const first = document.querySelector(".f.invalid input, .f.invalid select");
    if (first) first.focus();
  }

  /* -- Étapes -------------------------------------------------------------------------------- */
  const STEPS = [
    { id: "welcome", title: "Bienvenue", desc: "Cet assistant installe et configure HeptagonesLM sur ce serveur, puis vérifie que chaque composant fonctionne.", render: renderWelcome },
    { id: "checks", title: "Vérification du serveur", desc: "Contrôle des prérequis. Les éléments manquants peuvent être installés automatiquement.", render: renderChecks, enter: () => runChecks() },
    { id: "location", title: "Emplacement et compte de service", desc: "Dossier d'installation et compte Windows dédié qui exécutera les tâches de HeptagonesLM.", render: renderLocation, validate: "location" },
    { id: "database", title: "Base de données", desc: "L'historique est conservé dans SQL Server. L'assistant peut créer la base et les droits du compte de service.", render: renderDatabase, validate: "database" },
    { id: "licenses", title: "Serveurs de licences", desc: "Serveurs FlexLM ou RLM à surveiller. Testez chacun : l'assistant interroge lmstat ou rlmstat comme le fera la collecte.", render: renderLicenses, validate: "licenses" },
    { id: "web", title: "Interface web et IIS", desc: "Mode d'accès des utilisateurs à l'interface.", render: renderWeb, validate: "web" },
    { id: "email", title: "Rapport par e-mail", desc: "Envoi automatique du rapport mensuel (optionnel, modifiable plus tard).", render: renderEmail, validate: "email" },
    { id: "itsm", title: "Tickets ITSM", desc: "Ouverture automatique d'un ticket pour chaque alerte (optionnel, modifiable plus tard).", render: renderItsm, validate: "itsm" },
    { id: "autodesk", title: "Licences Autodesk nommées", desc: "Abonnements Autodesk nominatifs (sans serveur de licences) : exports du portail et / ou API Autodesk (optionnel).", render: renderAutodesk, validate: "autodesk" },
    { id: "summary", title: "Récapitulatif", desc: "Vérifiez la configuration avant de lancer l'installation.", render: renderSummary, enter: () => loadSummary(), next: "Installer" },
    { id: "install", title: "Installation", desc: "Ne fermez pas cette page pendant l'installation.", render: renderInstall },
    { id: "verify", title: "Vérification de l'installation", desc: "Contrôle de bout en bout de chaque composant installé.", render: renderVerify, enter: () => { fixResult = null; runVerify(); } },
    { id: "recette", title: "Recette", desc: "HeptagonesLM comparé aux serveurs de licences réels et contrôlé de bout en bout, puis PV de recette à signer.", render: renderRecette, enter: () => loadRecette() },
  ];
  const at = id => STEPS.findIndex(s => s.id === id);
  const SUMMARY = at("summary"), INSTALL = at("install"), VERIFY = at("verify"), RECETTE = at("recette");
  let recetteMode = false;            // recette d'une installation existante, ouverte depuis la page d'accueil

  function renderStepper() {
    const locked = install.status === "running";
    $("stepper").innerHTML = STEPS.map((s, i) => {
      const cls = i === step ? "current" : (i < maxStep || (i < step) ? "done" : "");
      let reachable = !locked && i <= maxStep && i !== step && (i < INSTALL || install.status === "done") && !(step >= INSTALL && i < INSTALL && install.status === "done");
      if (recetteMode) reachable = !locked && i !== step && (i === 0 || i === RECETTE);
      return `<li class="${cls}"><button type="button" data-step="${i}" ${reachable ? "" : "disabled"}>
        <span class="num">${cls === "done" ? "✓" : i + 1}</span><span class="txt">${esc(s.title)}</span></button></li>`;
    }).join("");
    $("stepper").querySelectorAll("button[data-step]").forEach(b => b.addEventListener("click", () => go(+b.dataset.step)));
  }

  function render() {
    const s = STEPS[step];
    $("step-eyebrow").textContent = `Étape ${step + 1} sur ${STEPS.length}`;
    $("step-title").textContent = s.title;
    $("step-desc").textContent = s.desc;
    const body = $("step-body");
    body.innerHTML = s.render();
    bind(body);
    if (s.after) s.after();
    renderStepper();
    const prev = $("prev"), next = $("next");
    prev.hidden = step === 0 || step >= INSTALL;
    next.hidden = step >= INSTALL;
    next.textContent = s.next || "Suivant";
    next.disabled = step === SUMMARY && (!preview || preview.errors.length > 0 || blockingCount > 0 || checksBusy);
    $("foot-msg").textContent = step === SUMMARY && blockingCount > 0 ? `${blockingCount} prérequis bloquant(s) à corriger` : "";
    // dernières étapes : boutons propres à la page, barre du bas inutile (elle masquait la fin de la page)
    document.querySelector(".setup-foot").hidden = prev.hidden && next.hidden;
    document.title = `${s.title} — Installation de HeptagonesLM`;
  }

  async function go(target) {
    showError("");
    const s = STEPS[step];
    if (target > step && s.validate) {
      try {
        const res = await api("/api/validate", { answers: A, step: s.validate });
        if (res.errors.length) { markErrors(res.errors); showError("Corrigez les champs signalés pour continuer."); return; }
      } catch (e) { showError(e.message); return; }
    }
    step = target; maxStep = Math.max(maxStep, step);
    save();
    render();
    window.scrollTo({ top: 0 });
    if (STEPS[step].enter) STEPS[step].enter();
  }

  /* 1. Bienvenue */
  function renderWelcome() {
    const os = info.os || {};
    const feats = [
      ["shield", "Contrôle des prérequis", "Windows, droits, pilote ODBC, lmutil, IIS et ses modules ; installation automatique de ce qui manque."],
      ["db", "Base SQL Server", "Test de connexion, création de la base, de la connexion Windows et des droits du compte de service."],
      ["server", "Serveurs de licences", "Déclaration et test en direct de chaque serveur FlexLM (lmstat) ou RLM (rlmstat)."],
      ["globe", "IIS et connexion Windows", "Site HTTPS, authentification unique, restriction à un groupe AD, pare-feu."],
      ["clock", "Tâches planifiées", "Collecte toutes les 5 minutes, rapport mensuel, e-mails, purge, interface permanente."],
      ["check", "Vérification finale", "Chaque composant est testé de bout en bout, avec un rapport d'installation."],
    ];
    const inst = info.installed;
    return `
      ${inst ? `<div class="panel installed-panel"><div class="panel-head"><div><h2>${dot("ok")} HeptagonesLM est déjà installé sur ce serveur</h2>
        <p class="muted">Configuration <code>${esc(inst)}</code>. Faites la recette de cette installation (comparaison avec les serveurs de licences, contrôles de bout en bout, PV), ou poursuivez pour la mettre à jour.</p></div></div>
        <div class="actions"><button type="button" class="btn primary" id="go-recette">Faire la recette de cette installation</button>
          <button type="button" class="btn" id="go-install">Mettre à jour ou réinstaller</button></div></div>` : ""}
      <div class="welcome-grid">${feats.map(([i, t, d]) => `<div class="feature"><span class="ico">${icon(i)}</span><b>${t}</b><span>${d}</span></div>`).join("")}</div>
      <div class="recap">
        <div class="panel"><h2>Ce serveur</h2><dl class="kv">
          <dt>Système</dt><dd>${esc(os.caption || "—")}</dd>
          <dt>Nom</dt><dd>${esc(os.fqdn || os.hostname || "—")}</dd>
          <dt>Domaine</dt><dd>${esc((info.domain || {}).domain || "hors domaine")}</dd>
          <dt>Droits</dt><dd>${info.admin ? `${dot("ok")} Administrateur` : `${dot("error")} Non administrateur : relancer en tant qu'administrateur`}</dd>
          <dt>Mode</dt><dd>${info.simulated ? "Simulation (aucune modification)" : "Installation réelle"}</dd>
        </dl></div>
        <div class="panel"><h2>À préparer avant de commencer</h2><ul class="prep">
          <li>Un <b>compte de service</b> dédié (gMSA recommandé, sinon compte + mot de passe).</li>
          <li>L'<b>instance SQL Server</b> et un compte autorisé à créer la base (ou une base créée par le DBA).</li>
          <li>Un <b>certificat HTTPS</b> importé dans le magasin « Ordinateur local › Personnel » et son <b>nom DNS</b>.</li>
          <li>Les adresses <b>port@serveur</b> des serveurs de licences et l'ouverture des flux réseau.</li>
          <li>Le <b>groupe AD</b> des personnes autorisées à consulter HeptagonesLM.</li>
        </ul></div>
      </div>`;
  }

  STEPS[0].after = () => {
    $("go-recette")?.addEventListener("click", () => { recetteMode = true; step = RECETTE; render(); window.scrollTo({ top: 0 }); loadRecette(); });
    $("go-install")?.addEventListener("click", () => { recetteMode = false; go(1); });
  };

  /* Commande à copier (SPN, zone Intranet, adresse de test…) */
  function cmdHtml(c) {
    return c.command ? `<div class="cmd"><code>${esc(c.command)}</code><button type="button" class="btn small ghost" data-copy="${esc(c.command)}">Copier</button></div>` : "";
  }
  function bindCopy(root = document) {
    root.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copié"; }
      catch (e) { b.textContent = "Sélectionner et copier"; }
      setTimeout(() => { b.textContent = "Copier"; }, 1800);
    }));
  }
  function checkRow(c) {
    return `<div class="check-row">${dot(c.status)}<div><div class="lbl">${esc(c.label)}</div><div class="det">${esc(c.detail)}
      ${c.help ? ` · <a href="${esc(c.help)}" target="_blank" rel="noopener">documentation</a>` : ""}</div>${cmdHtml(c)}</div><span></span></div>`;
  }

  /* 2. Vérification du serveur */
  function progressHtml(p) {
    const steps = (p && p.steps) || [];
    const mark = st => st.state === "running" ? '<span class="spinner"></span>' : dot(st.state === "error" ? "warning" : "ok");
    return `<div class="panel" id="checks-progress"><div class="panel-head"><div><h2><span class="spinner"></span> Vérification en cours</h2>
      <p class="muted">Lectures du serveur menées en parallèle${p && p.elapsed != null ? ` · ${Math.round(p.elapsed)} s` : ""}.</p></div></div>
      <ul class="probe-list">${steps.map(st => `<li class="${esc(st.state)}">${mark(st)}<span>${esc(st.label)}</span>
        <small>${st.seconds != null ? `${String(st.seconds).replace(".", ",")} s` : "en cours…"}</small></li>`).join("")}</ul></div>`;
  }
  function updateProgress() {
    const el = $("checks-progress");
    if (el && checksProgress) el.outerHTML = progressHtml(checksProgress);
  }
  function checksHtml(list) {
    if (!list) return progressHtml(checksProgress);
    const groups = {};
    list.forEach(c => (groups[c.category] = groups[c.category] || []).push(c));
    const count = k => list.filter(c => c.status === k).length;
    const fixes = [...new Map(list.filter(c => c.fix).map(c => [c.fix, c])).values()];
    return `
      <div class="summary-bar">
        <span class="badge sev sev-good">${dot("ok")} ${count("ok")} conforme(s)</span>
        ${count("warning") ? `<span class="badge sev sev-warning">${dot("warning")} ${count("warning")} avertissement(s)</span>` : ""}
        ${count("error") ? `<span class="badge sev sev-critical">${dot("error")} ${count("error")} à corriger</span>` : ""}
        <span class="spacer"></span>
        <button type="button" class="btn small" id="recheck" ${checksBusy ? "disabled" : ""}>Relancer la vérification</button>
      </div>
      ${fixes.length ? `<div class="panel"><div class="panel-head"><div><h2>Corrections automatiques</h2>
        <p class="muted">L'assistant peut installer les éléments manquants (quelques minutes, Internet requis pour les modules Microsoft sauf fichiers déposés dans <code>prerequis\\</code>).</p></div></div>
        <div class="actions">${fixes.map(c => `<button type="button" class="btn primary" data-fix="${c.fix}" ${checksBusy ? "disabled" : ""}>${esc(c.fix_label || "Corriger")}${c.fix === "install_iis" ? "" : ` : ${esc(c.label)}`}</button>`).join("")}</div></div>` : ""}
      <div class="result" id="fix-result">${renderResult(fixResult)}</div>
      <div class="panel">${Object.entries(groups).map(([g, items]) => `<div class="check-group"><h3>${esc(g)}</h3><div class="check-list">
        ${items.map(checkRow).join("")}
      </div></div>`).join("")}</div>`;
  }
  function renderChecks() { return (checksBusy && checks ? progressHtml(checksProgress) : "") + checksHtml(checks); }
  function bindChecks(root = document) {
    bindCopy(root);
    root.querySelector("#recheck")?.addEventListener("click", () => runChecks());
    root.querySelectorAll("[data-fix]").forEach(b => b.addEventListener("click", () => runFix(b.dataset.fix)));
  }
  STEPS[1].after = () => bindChecks();
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  async function runChecks() {
    checksBusy = true;
    checksProgress = { steps: [], elapsed: 0 };
    if (step === 1) render();
    try {
      let st = await api("/api/checks/start", { answers: A, final: step >= SUMMARY });
      while (st.status === "running") {
        checksProgress = st; updateProgress();
        await sleep(400);
        st = await api("/api/checks/state");
      }
      if (st.status === "error") throw new Error(st.error || "Vérification impossible");
      checks = st.checks; blockingCount = st.blocking;
    } catch (e) { showError(e.message); }
    checksBusy = false; checksProgress = null;
    if (step === 1 || step === SUMMARY) render();
  }
  async function runFix(fix) {
    checksBusy = true;
    fixResult = { busy: "Installation en cours… cette opération peut prendre plusieurs minutes." };
    render();
    try {
      const res = await api("/api/fix", { fix, answers: A, final: step >= SUMMARY });
      checks = res.checks; blockingCount = res.blocking;
      fixResult = { status: "ok", message: res.message };
    } catch (e) {
      fixResult = { status: "error", message: e.message };
    }
    checksBusy = false;
    render();
  }

  /* 3. Emplacement et compte */
  function renderLocation() {
    const gmsa = (A.service_account || "").endsWith("$");
    return `
      <div class="panel"><h2>Dossier d'installation</h2><p class="muted">Exécutable, configuration, journaux et rapports.</p>
        <div class="form-grid">${input("install_dir", "Dossier", { placeholder: "C:\\HeptagonesLM", hint: "Créé s'il n'existe pas ; une configuration existante est sauvegardée." })}
        ${input("collect_interval", "Intervalle de collecte (minutes)", { type: "number", min: 1, max: 60, hint: "5 minutes recommandées" })}</div></div>
      <div class="panel"><h2>Compte de service</h2><p class="muted">Compte dédié, non administrateur, qui exécutera les tâches planifiées (SYSTEM est refusé). Il reçoit les droits nécessaires sur le dossier et la base.</p>
        <div class="form-grid">${input("service_account", "Compte (DOMAINE\\compte, ou DOMAINE\\gmsa$)", { rerender: false, placeholder: "ENTREPRISE\\svc_heptagoneslm" })}
        ${gmsa ? `<div class="f"><span>Mot de passe</span><span class="hint">Compte de service géré (gMSA) : aucun mot de passe à saisir.</span></div>`
               : input("service_password", "Mot de passe du compte", { type: "password", hint: "Utilisé uniquement pour enregistrer les tâches ; jamais écrit sur le disque." })}</div>
        ${testBlock("account", "account", "Tester le compte")}</div>`;
  }

  /* 4. Base de données */
  function renderDatabase() {
    const sql = A.db_mode === "sqlserver";
    const drivers = (info.odbc_drivers.length ? info.odbc_drivers : ["ODBC Driver 18 for SQL Server"]).map(d => [d, d]);
    return `
      ${choices("db_mode", [
        { value: "sqlserver", title: "SQL Server", text: "Production : historique illimité, sauvegardes, accès Excel / Power BI.", tag: "Recommandé", icon: "db" },
        { value: "sqlite", title: "SQLite local", text: "Évaluation ou très petit parc : fichier dans le dossier d'installation.", icon: "server" },
      ])}
      ${sql ? `<div class="panel"><h2>Connexion</h2><div class="form-grid">
        ${input("db_server", "Instance SQL Server", { placeholder: "SQLSRV01 ou SQLSRV01\\INSTANCE" })}
        ${input("db_name", "Nom de la base", { placeholder: "HeptagonesLM" })}
        ${select("db_driver", "Pilote ODBC", drivers)}
        ${select("db_auth", "Authentification", [["windows", "Windows (compte de service)"], ["sql", "Identifiant SQL"]], { rerender: true })}
        ${A.db_auth === "sql" ? input("db_user", "Identifiant SQL") + input("db_password", "Mot de passe SQL", { type: "password", hint: "Stocké dans la variable d'environnement machine HEPTAGONESLM_DB_PASSWORD." }) : ""}
        <div class="full" style="display:flex;flex-direction:column;gap:12px">
          ${toggle("db_create", "Créer la base et les droits si nécessaire", "Base en récupération SIMPLE, connexion Windows du compte de service, rôles lecture / écriture. Nécessite un compte autorisé (sysadmin ou dbcreator).")}
          ${toggle("db_encrypt", "Chiffrer la connexion (Encrypt=yes)", "Recommandé : le certificat de l'instance SQL doit être reconnu par ce serveur.")}
          ${toggle("db_trust_cert", "Faire confiance au certificat du serveur SQL sans le vérifier", "À réserver aux essais : déconseillé en production.")}
        </div></div>
        ${testBlock("database", "database", "Tester la connexion")}</div>`
      : `<div class="panel"><h2>Base SQLite</h2><p class="muted">Fichier <code>${esc(A.install_dir)}\\data\\heptagoneslm.db</code>, créé à l'installation. Migration vers SQL Server possible plus tard (nouvelle installation + reprise des logs).</p></div>`}`;
  }

  /* 5. Serveurs de licences */
  function renderLicenses() {
    if (!A.servers.length) A.servers.push({ name: "", port_at_host: "", collectors: ["lmstat"], log_path: "", license_type: "flexlm" });
    const found = info.lmutil_found || [];
    const types = new Set(A.servers.map(s => s.license_type || "flexlm"));
    const rows = A.servers.map((s, i) => {
      const rlm = s.license_type === "rlm";
      return `
      <tr>
        <td><div class="f" data-f="servers.${i}.name"><input type="text" data-srv="${i}" data-key="name" value="${esc(s.name)}" placeholder="${rlm ? "FOUNDRY" : "AUTODESK"}" aria-label="Nom du serveur"><span class="ferr"></span></div></td>
        <td><div class="f" data-f="servers.${i}.license_type"><select data-srv="${i}" data-key="license_type" aria-label="Gestionnaire de licences">
          <option value="flexlm" ${rlm ? "" : "selected"}>FlexLM</option><option value="rlm" ${rlm ? "selected" : ""}>RLM</option></select><span class="ferr"></span></div></td>
        <td><div class="f" data-f="servers.${i}.port_at_host"><input type="text" data-srv="${i}" data-key="port_at_host" value="${esc(s.port_at_host)}" placeholder="${rlm ? "5053@licsrv3" : "27000@licsrv1"}" aria-label="Adresse port@serveur"><span class="ferr"></span></div></td>
        <td>${rlm ? `<span class="muted">rlmstat</span>` : `<label class="toggle"><input type="checkbox" data-srv="${i}" data-key="log" ${s.collectors.includes("log") ? "checked" : ""}><span>Log debug</span></label>
          ${s.collectors.includes("log") ? `<div class="f" data-f="servers.${i}.log_path" style="margin-top:6px"><input type="text" data-srv="${i}" data-key="log_path" value="${esc(s.log_path || "")}" placeholder="\\\\licsrv1\\logs$\\license.log"><span class="ferr"></span></div>` : ""}`}</td>
        <td style="width:240px"><button type="button" class="btn small" data-test="lm${i}" data-kind="lmstat" data-index="${i}">Tester</button>
          <div class="result" id="res-lm${i}">${renderResult(results["lm" + i])}</div></td>
        <td><button type="button" class="icon-x" data-del="${i}" title="Retirer" aria-label="Retirer ce serveur">×</button></td>
      </tr>`; }).join("");
    return `
      <div class="panel"><div class="panel-head"><div><h2>Serveurs</h2><p class="muted">Triade redondante : <code>27000@srv1:27000@srv2:27000@srv3</code>. Le log debug ajoute les refus de licence et des sessions à la seconde.</p></div>
        <button type="button" class="btn small" id="add-srv">+ Ajouter un serveur</button></div>
        <table class="servers"><thead><tr><th>Nom</th><th>Type</th><th>Adresse port@serveur</th><th>Collecte</th><th>Test</th><th></th></tr></thead><tbody>${rows}</tbody></table>
        <div class="f" data-f="servers"><span class="ferr"></span></div></div>
      <div class="panel"><h2>Outils des gestionnaires de licences</h2><div class="form-grid">
        ${types.has("flexlm") ? input("lmutil_path", "Chemin de lmutil.exe (FlexLM)", { full: true, hint: found.length ? `Détecté : ${esc(found.join(" · "))}` : "Fourni avec Autodesk Network License Manager ou tout serveur FlexLM (à copier par exemple dans C:\\HeptagonesLM\\bin)." }) : ""}
        ${types.has("rlm") ? input("rlmutil_path", "Chemin de rlmutil.exe (RLM)", { full: true, placeholder: "C:\\HeptagonesLM\\bin\\rlmutil.exe", hint: "Fourni avec le serveur de licences RLM de l'éditeur (Foundry, Maxon, Chaos…)." }) : ""}
      </div>
        <div class="test-row"><button type="button" class="btn" id="find-tools">${icon("search")} Rechercher l'outil</button>
          <span class="muted" style="flex:1;min-width:240px">Cherche sur ce serveur et sur les serveurs de licences déclarés ci-dessus : démons en cours d'exécution, services, dossiers d'installation. Un outil trouvé sur un autre serveur est copié dans <code>${esc(A.install_dir)}\\bin</code>.</span></div>
        <div id="tools-found">${renderTools(types)}</div></div>`;
  }
  function renderTools(types) {
    const t = toolScan;
    if (!t) return "";
    if (t.busy) return `<div class="result"><span class="spinner"></span><span>Recherche en cours sur ${esc(t.busy)}… (jusqu'à quelques minutes pour un serveur distant)</span></div>`;
    if (t.error) return `<div class="result">${dot("error")}<div><b>${esc(t.error)}</b></div></div>`;
    const wanted = [...types].map(x => x === "rlm" ? "rlmutil" : "lmutil");
    const rows = (t.found || []).filter(r => wanted.includes(r.tool));
    const note = (t.used ? `<div class="result">${dot("ok")}<div><b>${esc(t.used)}</b></div></div>` : "")
      + ((t.problems || []).length ? `<div class="result">${dot("warning")}<div><b>Serveurs non examinés</b><ul>${t.problems.map(p => `<li>${esc(p.host)} : ${esc(p.problem)}</li>`).join("")}</ul></div></div>` : "");
    if (!rows.length) return `${note}<div class="result">${dot("warning")}<div><b>Aucun outil trouvé</b><ul>
      <li>Serveurs examinés : ce serveur${t.hosts.length ? ", " + esc(t.hosts.join(", ")) : " (aucun serveur de licences déclaré)"}.</li>
      <li>Pour un serveur distant, le compte qui lance l'assistant doit être administrateur de ce serveur (partage C$ et WMI).</li>
      <li>Sinon, copier ${wanted.map(w => `<code>${w}.exe</code>`).join(" et ")} depuis le dossier du serveur de licences vers <code>${esc(A.install_dir)}\\bin</code> et indiquer ce chemin (ou le dossier).</li></ul></div></div>`;
    return `${note}<table class="tools-found"><thead><tr><th>Outil</th><th>Emplacement</th><th>Trouvé par</th><th></th></tr></thead><tbody>${rows.map(r => {
      const current = (A[r.tool + "_path"] || "").toLowerCase() === r.path.toLowerCase();
      return `<tr><td><b>${r.tool}.exe</b></td><td><code>${esc(r.path)}</code><div class="muted">${r.local ? "Ce serveur" : esc(r.host)}</div></td>
        <td class="muted">${esc(r.how)}</td>
        <td>${current ? `<span class="muted">Retenu</span>` : `<button type="button" class="btn small" data-use="${esc(r.path)}">${r.local ? "Utiliser" : "Copier ici et utiliser"}</button>`}</td></tr>`;
    }).join("")}</tbody></table>`;
  }
  function bindTools() {
    const root = $("tools-found"); if (!root) return;
    root.querySelectorAll("[data-use]").forEach(b => b.addEventListener("click", async () => {
      b.disabled = true; b.textContent = "…";
      try {
        const r = await api("/api/tools/use", { answers: A, path: b.dataset.use });
        A[r.field] = r.path;
        toolScan.used = r.copied ? `Copié depuis ${r.host} : ${r.path}` : `Chemin retenu : ${r.path}`;
        save(); render();
      } catch (e) { toolScan.used = null; toolScan.error = e.message; save(); render(); }
    }));
  }
  async function findTools() {
    const hosts = [...new Set(A.servers.flatMap(s => (s.port_at_host || "").split(":").map(p => p.split("@")[1]).filter(Boolean)))];
    toolScan = { busy: ["ce serveur", ...hosts].join(", ") };
    $("tools-found").innerHTML = renderTools(new Set());
    $("find-tools").disabled = true;
    try {
      toolScan = await api("/api/tools/find", { answers: A });
      // champ vide : le meilleur outil de ce serveur est retenu d'office
      for (const tool of ["lmutil", "rlmutil"]) {
        const best = toolScan.found.find(r => r.tool === tool && r.local);
        if (best && !A[tool + "_path"]) { A[tool + "_path"] = best.path; toolScan.used = `Chemin retenu : ${best.path}`; }
      }
    } catch (e) { toolScan = { error: e.message }; }
    save(); render();
  }
  STEPS[4].after = () => {
    const root = $("step-body");
    $("find-tools")?.addEventListener("click", findTools);
    bindTools();
    root.querySelectorAll("[data-srv]").forEach(el => {
      const i = +el.dataset.srv, key = el.dataset.key;
      el.addEventListener(el.type === "checkbox" || el.tagName === "SELECT" ? "change" : "input", () => {
        const s = A.servers[i];
        if (key === "log") { s.collectors = el.checked ? ["lmstat", "log"] : ["lmstat"]; save(); render(); return; }
        if (key === "license_type") {
          s.license_type = el.value; s.collectors = el.value === "rlm" ? ["rlmstat"] : ["lmstat"];
          delete results["lm" + i]; save(); render(); return;
        }
        s[key] = el.value; save();
      });
    });
    root.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", () => {
      A.servers.splice(+b.dataset.del, 1); Object.keys(results).filter(k => k.startsWith("lm")).forEach(k => delete results[k]); save(); render();
    }));
    $("add-srv").addEventListener("click", () => { A.servers.push({ name: "", port_at_host: "", collectors: ["lmstat"], log_path: "", license_type: "flexlm" }); save(); render(); });
  };

  /* 6. Interface web */
  function renderWeb() {
    const certs = info.certificates || [];
    const certOptions = certs.length ? certs.map(c => [c.thumbprint, `${c.friendly || c.subject} — ${c.dns || c.subject} (jusqu'au ${c.not_after})`])
                                     : [["", "Aucun certificat avec clé privée dans LocalMachine\\My"]];
    const mode = A.web_mode;
    let detail = "";
    if (mode === "iis") {
      detail = `<div class="panel"><h2>Site IIS</h2><p class="muted">IIS authentifie les utilisateurs avec leur session Windows (aucune saisie), vérifie le groupe autorisé et relaie vers HeptagonesLM, qui n'écoute que sur ce serveur.</p>
        <div class="form-grid">
          ${input("public_host", "Nom DNS de l'interface", { placeholder: "heptagoneslm.entreprise.local", hint: "Nom saisi dans le navigateur ; enregistrement DNS vers ce serveur." })}
          ${input("iis_site", "Nom du site IIS")}
          ${select("iis_cert_thumbprint", "Certificat HTTPS", certOptions, { full: true })}
          ${input("iis_port", "Port HTTPS", { type: "number", min: 1, max: 65535 })}
          ${input("web_port", "Port interne de HeptagonesLM", { type: "number", min: 1024, max: 65535, hint: "Écoute locale uniquement (127.0.0.1)." })}
          ${input("access_group", "Groupe AD autorisé", { placeholder: "ENTREPRISE\\GG_HeptagonesLM_Lecteurs", hint: "Vide : tout utilisateur du domaine." })}
        </div>
        ${testBlock("certificate", "certificate", "Vérifier le certificat")}${testBlock("group", "group", "Vérifier le groupe")}</div>`;
    } else if (mode === "https") {
      detail = `<div class="panel"><h2>HTTPS intégré et connexion AD</h2><p class="muted">HeptagonesLM sert lui-même l'interface en HTTPS ; les utilisateurs saisissent leurs identifiants Windows, vérifiés par l'Active Directory (LDAPS).</p>
        <div class="form-grid">
          ${input("public_host", "Nom DNS de l'interface", { placeholder: "heptagoneslm.entreprise.local" })}
          ${input("web_port", "Port HTTPS", { type: "number", min: 1024, max: 65535 })}
          ${input("tls_cert", "Certificat (PEM)", { placeholder: "C:\\HeptagonesLM\\certs\\heptagoneslm.pem" })}
          ${input("tls_key", "Clé privée (PEM)", { placeholder: "C:\\HeptagonesLM\\certs\\heptagoneslm.key" })}
          ${input("ldap_servers", "Contrôleurs de domaine", { list: true, full: true, placeholder: "ldaps://dc01.entreprise.local, ldaps://dc02.entreprise.local" })}
          ${input("base_dn", "Base de recherche", { placeholder: "DC=entreprise,DC=local" })}
          ${input("upn_suffix", "Suffixe UPN", { placeholder: "entreprise.local" })}
          ${input("access_group", "Groupe AD autorisé", { placeholder: "ENTREPRISE\\GG_HeptagonesLM_Lecteurs", hint: "Vide : tout utilisateur du domaine." })}
        </div>
        ${testBlock("ldap", "ldap", "Tester une connexion",
          `<input type="text" id="ldap-user" placeholder="identifiant de test" autocomplete="off" style="height:34px;width:180px">
           <input type="password" id="ldap-pass" placeholder="mot de passe" autocomplete="new-password" style="height:34px;width:160px">`)}</div>`;
    } else {
      detail = `<div class="panel"><h2>Poste local uniquement</h2><p class="muted">Interface accessible seulement depuis ce serveur (<code>http://localhost:${esc(A.web_port)}</code>), sans authentification. Pour un essai ou une consultation par l'administrateur.</p>
        <div class="form-grid">${input("web_port", "Port", { type: "number", min: 1024, max: 65535 })}</div></div>`;
    }
    return `
      ${choices("web_mode", [
        { value: "iis", title: "IIS · connexion Windows automatique", text: "Authentification unique, HTTPS géré par IIS, restriction par groupe AD.", tag: "Recommandé", icon: "globe" },
        { value: "https", title: "HTTPS intégré · identifiants AD", text: "Sans IIS : page de connexion, certificat au format PEM.", icon: "lock" },
        { value: "local", title: "Poste local", text: "Consultation depuis ce serveur uniquement.", icon: "monitor" },
      ])}
      ${detail}
      ${mode !== "local" ? `<div class="panel">${toggle("open_firewall", "Ouvrir le port dans le pare-feu Windows", "Règle entrante TCP, profils Domaine et Privé.")}</div>` : ""}`;
  }

  /* 7. E-mail */
  function renderEmail() {
    return `
      <div class="panel">${toggle("email_enabled", "Envoyer le rapport mensuel par e-mail", "Résumé HTML et classeur Excel joint, une fois par mois (tâche « Envoi des rapports »).", { rerender: true })}</div>
      ${A.email_enabled ? `<div class="panel"><h2>Messagerie</h2><div class="form-grid">
        ${input("smtp_host", "Serveur SMTP", { placeholder: "smtp.entreprise.local" })}
        ${input("smtp_port", "Port", { type: "number", min: 1, max: 65535, hint: "25 (relais interne), 587 avec STARTTLS, 465 avec SSL" })}
        ${select("smtp_security", "Sécurité", [["starttls", "STARTTLS"], ["ssl", "SSL / TLS"], ["none", "Aucune (relais interne)"]])}
        ${input("sender", "Expéditeur", { placeholder: "HeptagonesLM <heptagoneslm@entreprise.local>" })}
        ${input("smtp_user", "Identifiant (optionnel)")}
        ${input("smtp_password", "Mot de passe (optionnel)", { type: "password", hint: "Stocké dans la variable d'environnement machine HEPTAGONESLM_SMTP_PASSWORD." })}
        ${input("recipients", "Destinataires", { list: true, full: true, placeholder: "dsi@entreprise.local, achats@entreprise.local" })}
        ${toggle("email_alerts", "Alertes immédiates", "Collecte en échec ou arrêtée, refus de licence, plus aucun jeton disponible, licence qui expire : un e-mail dès la collecte suivante, rappel au plus une fois par jour.", { full: true })}
      </div>${testBlock("smtp", "smtp", "Envoyer un message de test au premier destinataire")}</div>` : ""}`;
  }

  /* 8. Tickets ITSM */
  const ITSM = {
    servicenow: { label: "ServiceNow", url: "https://entreprise.service-now.com", urlHint: "Adresse de l'instance. Incident créé par l'API Table (rôle itil requis)." },
    jira: { label: "Jira", url: "https://entreprise.atlassian.net", urlHint: "Jira Cloud ou Data Center : ticket créé dans le projet choisi." },
    glpi: { label: "GLPI", url: "https://glpi.entreprise.local/apirest.php", urlHint: "Adresse de l'API REST (Configuration › Générale › API), terminée par /apirest.php." },
    webhook: { label: "Teams / Slack", url: "https://entreprise.webhook.office.com/…", urlHint: "Adresse du webhook entrant du canal (Workflows Teams, application Slack)." },
  };
  const SECRET_HINT = "Chiffré par Windows (DPAPI) à l'installation dans le dossier secrets, lisible seulement par le compte de service et les administrateurs ; jamais écrit dans heptagoneslm.yaml.";
  function renderItsm() {
    const t = A.itsm_type, d = ITSM[t] || ITSM.servicenow;
    let fields = "";
    if (t === "servicenow") {
      fields = `${input("itsm_user", "Compte technique", { placeholder: "svc_heptagoneslm" })}
        ${input("itsm_password", "Mot de passe du compte", { type: "password", hint: SECRET_HINT })}
        ${input("itsm_assignment_group", "Groupe d'affectation (optionnel)", { placeholder: "Support licences", full: true })}`;
    } else if (t === "jira") {
      fields = `${input("itsm_user", "E-mail du compte technique (Jira Cloud)", { placeholder: "svc-heptagoneslm@entreprise.fr", hint: "Vide pour Jira Data Center (jeton personnel seul)." })}
        ${input("itsm_password", "Jeton d'API (Cloud) ou jeton personnel (Data Center)", { type: "password", hint: SECRET_HINT })}
        ${input("itsm_project", "Clé du projet", { placeholder: "LIC" })}
        ${input("itsm_issue_type", "Type de ticket", { placeholder: "Incident" })}`;
    } else if (t === "glpi") {
      fields = `${input("itsm_app_token", "Jeton d'application", { type: "password", hint: "Configuration › Générale › API › client de l'API." })}
        ${input("itsm_password", "Jeton d'API de l'utilisateur technique", { type: "password", hint: SECRET_HINT })}`;
    } else {
      fields = `${select("itsm_format", "Format", [["teams", "Microsoft Teams"], ["slack", "Slack"], ["json", "JSON (autre outil)"]])}`;
    }
    return `
      <div class="panel">${toggle("itsm_enabled", "Ouvrir automatiquement des tickets", "Chaque alerte ouvre un ticket, le complète à chaque rappel et le résout quand la condition disparaît, sans doublon.", { rerender: true })}</div>
      ${A.itsm_enabled ? `
      ${choices("itsm_type", [
        { value: "servicenow", title: "ServiceNow", text: "Incident, groupe d'affectation, résolution automatique.", icon: "ticket" },
        { value: "jira", title: "Jira", text: "Ticket dans un projet, transition de résolution.", icon: "ticket" },
        { value: "glpi", title: "GLPI", text: "Ticket d'incident, solution ajoutée à la résolution.", icon: "ticket" },
        { value: "webhook", title: "Teams / Slack", text: "Message dans un canal à l'ouverture et à la résolution.", icon: "chat" },
      ])}
      <div class="panel"><h2>Connexion à ${esc(d.label)}</h2><div class="form-grid">
        ${input("itsm_url", "Adresse", { placeholder: d.url, hint: d.urlHint, full: true })}
        ${fields}
        ${select("itsm_min_severity", "Alertes qui ouvrent un ticket", [["serious", "Graves : pannes de collecte, refus, échéance à moins de 7 jours (recommandé)"], ["warning", "Graves et avertissements : aussi saturation, échéance à 30 jours"], ["critical", "Critiques uniquement : collecte en échec, licence expirée ou à moins de 7 jours"]], { full: true })}
        ${input("itsm_ca_file", "Autorité de certification interne (optionnel)", { placeholder: "C:\\HeptagonesLM\\certs\\ac-interne.pem", hint: "Uniquement si le certificat de l'outil n'est pas reconnu par Windows.", full: true })}
      </div>
      ${testBlock("itsm", "itsm", "Ouvrir un ticket de test")}
      <p class="muted" style="margin-top:10px">Le test ouvre un vrai ticket « Test de connexion HeptagonesLM », le commente puis le résout, avec les identifiants saisis.</p></div>` : ""}`;
  }

  /* 9. Licences Autodesk nommées */
  let adesk = null;                    // connexion Autodesk : {busy} | {status, teams, error}
  function renderAutodesk() {
    const dir = A.autodesk_import_dir || ((A.install_dir || "C:\\HeptagonesLM").replace(/\\$/, "") + "\\autodesk");
    const callback = `http://localhost:${A.autodesk_callback_port || 8770}/autodesk/callback`;
    const teams = (adesk && adesk.teams) || [];
    let login = "";
    if (adesk && adesk.busy) login = `<span class="spinner"></span><span>${esc(adesk.busy)}</span>`;
    else if (adesk && adesk.error) login = `${dot("error")}<div><b>${esc(adesk.error)}</b></div>`;
    else if (adesk && adesk.status === "done") login = `${dot("ok")}<div><b>Connecté à Autodesk : ${teams.length} équipe(s)</b><ul><li>Jeton de renouvellement chiffré par Windows (DPAPI) dans le dossier data, renouvelé à chaque synchronisation.</li></ul></div>`;
    return `
      <div class="panel">${toggle("autodesk_enabled", "Suivre les licences Autodesk nommées", "Licences affectées à un utilisateur (Autodesk Account) : actives, inactives ou jamais utilisées, et coût récupérable, dans le rapport « Autodesk · utilisateurs nommés ».", { rerender: true })}</div>
      ${A.autodesk_enabled ? `
      <div class="panel"><h2>Exports du portail Autodesk Account</h2><div class="form-grid">
        ${input("autodesk_import_dir", "Dossier surveillé", { placeholder: dir, hint: "Chaque export « Usage report » (manage.autodesk.com › Reporting › Usage report › Export, CSV ou Excel) déposé ici est importé à la collecte suivante.", full: true })}
        ${input("autodesk_inactive_days", "Licence inactive après (jours)", { type: "number", min: 1, max: 365 })}
      </div></div>
      <div class="panel">${toggle("autodesk_api_enabled", "Lire l'usage par l'API Autodesk", "Abonnement Business Success Plan avec l'API de reporting : l'usage est lu chaque jour chez Autodesk, sans export manuel.", { rerender: true })}
      ${A.autodesk_api_enabled ? `<div class="form-grid" style="margin-top:12px">
        ${input("autodesk_client_id", "Client ID de l'application Autodesk", { placeholder: "aps.autodesk.com › Applications › Client ID", full: true })}
        ${input("autodesk_client_secret", "Client Secret (optionnel)", { type: "password", hint: "Uniquement pour une application « Traditional Web App » ; vide pour une application « Desktop, Mobile, Single-Page App » (PKCE). " + SECRET_HINT })}
        ${input("autodesk_callback_port", "Port de retour de connexion", { type: "number", min: 1024, max: 65535, rerender: true, hint: `URL de rappel à déclarer dans l'application : <code>${esc(callback)}</code>` })}
        ${teams.length ? select("autodesk_context_id", "Équipe Autodesk", [["", "— choisir —"], ...teams.map(t => [t.id, t.name + (t.reporting ? "" : " (sans API de reporting)")])], { full: true }) : ""}
      </div>
      <div class="test-row"><button type="button" class="btn" id="autodesk-login" ${adesk && adesk.busy ? "disabled" : ""}>Se connecter à Autodesk</button>
        <div class="result" id="res-autodesk">${login}</div></div>
      <p class="muted" style="margin-top:10px">Un administrateur de l'équipe Autodesk se connecte dans l'onglet qui s'ouvre. Synchronisation ensuite chaque jour par la collecte ; à défaut d'usage pendant 15 jours, relancer « HeptagonesLM.exe autodesk-login ».</p>` : ""}</div>` : ""}`;
  }
  async function autodeskLogin() {
    const w = window.open("about:blank", "_blank");       // ouvert au clic : non bloqué par le navigateur
    adesk = { busy: "Connexion à Autodesk en cours : se connecter dans l'onglet ouvert…" }; render();
    try {
      let st = await api("/api/autodesk/login", { answers: A });
      let opened = false;
      const open = () => { if (st.url && w && !opened) { w.location = st.url; opened = true; } };
      open();
      while (st.status === "running") {
        await sleep(1200);
        st = await api("/api/autodesk/state");
        open();
      }
      if (!opened && w) w.close();
      if (st.status === "error") adesk = { error: st.error };
      else {
        adesk = { status: "done", teams: st.teams || [] };
        const usable = adesk.teams.filter(t => t.reporting);
        if (!A.autodesk_context_id && usable.length === 1) { A.autodesk_context_id = usable[0].id; save(); }
      }
    } catch (e) { adesk = { error: e.message }; if (w) w.close(); }
    render();
  }

  STEPS[at("autodesk")].after = () => $("autodesk-login")?.addEventListener("click", autodeskLogin);

  /* 10. Récapitulatif */
  async function loadSummary() {
    preview = null; render();
    try { preview = await api("/api/preview", { answers: A }); } catch (e) { showError(e.message); }
    await runChecks();
    render();
  }
  function renderSummary() {
    if (!preview) return `<div class="panel"><span class="spinner"></span> Préparation du récapitulatif…</div>`;
    if (preview.errors.length) {
      return `<div class="panel"><h2>Configuration incomplète</h2><ul>${preview.errors.map(e => `<li>${esc(e.field)} : ${esc(e.message)}</li>`).join("")}</ul></div>`;
    }
    const a = A;
    const web = { iis: `IIS · https://${a.public_host}${+a.iis_port === 443 ? "" : ":" + a.iis_port}`, https: `HTTPS intégré · https://${a.public_host}:${a.web_port}`, local: `Poste local · http://localhost:${a.web_port}` }[a.web_mode];
    const block = (title, target, rows) => `<div class="panel"><h2>${title}<button type="button" class="btn small ghost" data-goto="${target}">Modifier</button></h2>
      <dl class="kv">${rows.filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl></div>`;
    const blockers = (checks || []).filter(c => c.status === "error");
    return `
      ${blockers.length ? `<div class="panel"><div class="panel-head"><div><h2>${dot("error")} Prérequis à corriger avant l'installation</h2>
        <p class="muted">${blockers.map(c => esc(c.label)).join(" · ")}</p></div></div>
        <div class="actions">${[...new Map(blockers.filter(c => c.fix).map(c => [c.fix, c])).values()].map(c =>
          `<button type="button" class="btn primary" data-fix="${c.fix}" ${checksBusy ? "disabled" : ""}>${esc(c.fix_label || "Corriger")}${c.fix === "install_iis" ? "" : ` : ${esc(c.label)}`}</button>`).join("")}</div>
        <div class="result" id="fix-result">${renderResult(fixResult)}</div></div>`
        : `<div class="panel">${dot("ok")} <b>Tous les prérequis sont satisfaits.</b> <span class="muted">${(checks || []).filter(c => c.status === "warning").length} avertissement(s) non bloquant(s).</span></div>`}
      <div class="recap">
        ${block("Emplacement", 2, [["Dossier", esc(a.install_dir)], ["Compte de service", esc(a.service_account)], ["Collecte", `toutes les ${esc(a.collect_interval)} min`]])}
        ${block("Base de données", 3, a.db_mode === "sqlite" ? [["Mode", "SQLite local"]] : [["Instance", esc(a.db_server)], ["Base", esc(a.db_name) + (a.db_create ? " (créée si absente)" : "")], ["Authentification", a.db_auth === "sql" ? "SQL : " + esc(a.db_user) : "Windows (compte de service)"], ["Chiffrement", a.db_encrypt ? "Oui" : "Non"]])}
        ${block("Serveurs de licences", 4, [...(a.servers.some(s => s.license_type !== "rlm") ? [["lmutil", esc(a.lmutil_path)]] : []), ...(a.servers.some(s => s.license_type === "rlm") ? [["rlmutil", esc(a.rlmutil_path)]] : []), ...a.servers.map(s => [s.name, (s.license_type === "rlm" ? "RLM · " : "") + esc(s.port_at_host) + (s.collectors.includes("log") ? " + log debug" : "")])])}
        ${block("Interface web", 5, [["Accès", esc(web)], a.access_group && a.web_mode !== "local" ? ["Groupe autorisé", esc(a.access_group)] : null, a.web_mode === "iis" ? ["Site IIS", esc(a.iis_site)] : null, a.web_mode !== "local" ? ["Pare-feu", a.open_firewall ? "Port ouvert" : "Inchangé"] : null])}
        ${block("Tickets ITSM", at("itsm"), a.itsm_enabled ? [["Outil", esc((ITSM[a.itsm_type] || {}).label || a.itsm_type)], ["Adresse", esc(a.itsm_url)], a.itsm_user ? ["Compte", esc(a.itsm_user)] : null, a.itsm_type === "jira" ? ["Projet", esc(a.itsm_project)] : null, ["Secrets", a.itsm_type === "webhook" ? "Aucun" : "Chiffrés par Windows (DPAPI)"], ["Ticket de test", results.itsm && results.itsm.status === "ok" ? `${dot("ok")} réussi` : "non effectué"]] : [["Tickets automatiques", "Désactivés"]])}
        ${block("Licences Autodesk nommées", at("autodesk"), a.autodesk_enabled ? [["Exports du portail", esc(a.autodesk_import_dir || (a.install_dir + "\\autodesk"))], ["Inactive après", `${esc(a.autodesk_inactive_days)} jours`], ["API Autodesk", a.autodesk_api_enabled ? (a.autodesk_context_id ? `équipe ${esc(a.autodesk_context_id)}` : "connexion à faire") : "Non"]] : [["Suivi", "Désactivé"]])}
        ${block("E-mail", 6, a.email_enabled ? [["Relais", esc(a.smtp_host) + ":" + esc(a.smtp_port)], ["Destinataires", esc((a.recipients || []).join(", "))], ["Alertes immédiates", a.email_alerts ? "Activées" : "Désactivées"]] : [["Rapport par e-mail", "Désactivé"]])}
        <div class="panel"><h2>Tâches planifiées</h2><dl class="kv">${preview.plan.tasks.map(t => `<dt>${esc(t.name.replace("HeptagonesLM - ", ""))}</dt><dd>${esc(t.schedule)}</dd>`).join("")}</dl></div>
      </div>
      <div class="panel"><h2>Étapes de l'installation</h2><ol class="plan">${preview.plan.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol></div>
      <div class="panel"><details><summary>Configuration générée (heptagoneslm.yaml)</summary><pre class="yaml">${esc(preview.yaml)}</pre></details></div>`;
  }
  STEPS[SUMMARY].after = () => {
    bindChecks();
    document.querySelectorAll("[data-goto]").forEach(b => b.addEventListener("click", () => go(+b.dataset.goto)));
  };

  /* 10. Installation */
  function renderInstall() {
    const st = install;
    const pct = Math.round(st.percent || 0);
    const cls = st.status === "done" ? "done" : st.status === "failed" ? "failed" : "";
    const icons = { done: "✓", failed: "✕", running: "", pending: "", skipped: "–" };
    return `
      <div class="panel progress-card">
        <div class="progress-top"><span class="pct" id="pct">${pct} %</span>
          <span class="cur" id="cur">${st.status === "done" ? "Installation terminée" : st.status === "failed" ? "Installation interrompue" : esc(st.current || "Démarrage…")}</span></div>
        <div class="bar ${cls}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><i style="width:${pct}%"></i></div>
        <ul class="steps-list" id="steps">${(st.steps || []).map(s => `<li class="${s.status}"><span class="ico">${s.status === "running" ? '<span class="spinner"></span>' : icons[s.status]}</span>
          <span>${esc(s.label)}${s.status === "failed" ? `<br><small style="color:var(--critical)">${esc(s.message)}</small>` : ""}</span>
          <small>${s.duration != null ? s.duration + " s" : ""}</small></li>`).join("")}</ul>
      </div>
      ${st.status === "failed" ? `<div class="panel"><div class="hero-result sev-critical"><span class="big">!</span><div><h2>L'installation s'est arrêtée</h2>
        <p>${esc(st.error || "")}</p></div></div>
        <div class="actions"><button type="button" class="btn primary" id="retry">Réessayer à partir de cette étape</button>
          <button type="button" class="btn" id="back-summary">Modifier la configuration</button>
          <a class="btn ghost" href="/api/report">Télécharger le journal</a></div></div>` : ""}
      <div class="panel"><details ${st.status === "failed" ? "open" : ""} id="log-details"><summary>Journal détaillé</summary>
        <div class="console" id="console" style="margin-top:12px"></div></details></div>`;
  }
  let consoleLines = [];
  function appendLogs(lines) {
    consoleLines = consoleLines.concat(lines);
    const c = $("console");
    if (!c) return;
    const atBottom = c.scrollTop + c.clientHeight >= c.scrollHeight - 30;
    c.insertAdjacentHTML("beforeend", lines.map(l => `<div class="${esc(l.level)}"><span class="t">${esc(l.t)}</span>${esc(l.msg)}</div>`).join(""));
    if (atBottom) c.scrollTop = c.scrollHeight;
  }
  STEPS[INSTALL].after = () => {
    const c = $("console");
    if (c && consoleLines.length) { c.innerHTML = consoleLines.map(l => `<div class="${esc(l.level)}"><span class="t">${esc(l.t)}</span>${esc(l.msg)}</div>`).join(""); c.scrollTop = c.scrollHeight; }
    $("retry")?.addEventListener("click", () => startInstall(true));
    $("back-summary")?.addEventListener("click", () => { install = { status: "idle" }; go(SUMMARY); });
  };
  async function startInstall(resume = false) {
    showError("");
    try { await api("/api/install", { answers: A, resume }); }
    catch (e) { showError(e.message); return; }
    if (!resume) { logCount = 0; consoleLines = []; }
    install = { ...install, status: "running" };
    if (step !== INSTALL) { step = INSTALL; maxStep = Math.max(maxStep, INSTALL); }
    render();
    poll();
  }
  async function poll() {
    clearTimeout(pollTimer);
    try {
      const st = await api(`/api/progress?since=${logCount}`);
      logCount = st.log_count;
      const prevStatus = install.status;
      install = st;
      if (step === INSTALL) {
        const stepsChanged = JSON.stringify(st.steps.map(s => s.status)) !== JSON.stringify((poll.last || []));
        poll.last = st.steps.map(s => s.status);
        if (stepsChanged || st.status !== prevStatus) render();
        else {
          const pct = Math.round(st.percent || 0);
          $("pct").textContent = `${pct} %`;
          document.querySelector(".bar > i").style.width = `${pct}%`;
          document.querySelector(".bar").setAttribute("aria-valuenow", pct);
          if (st.current) $("cur").textContent = st.current;
        }
        appendLogs(st.logs);
      }
      if (st.status === "running") pollTimer = setTimeout(poll, 700);
      else if (st.status === "done") { maxStep = VERIFY; setTimeout(() => go(VERIFY), 1200); }
      else render();
    } catch (e) { showError(e.message); pollTimer = setTimeout(poll, 2000); }
  }

  /* 11. Vérification */
  async function runVerify() {
    verification = null; render();
    try { verification = await api("/api/verify", {}); } catch (e) { showError(e.message); }
    render();
  }
  function renderVerify() {
    if (!verification) return `<div class="panel"><span class="spinner"></span> Vérification de chaque composant…</div>`;
    const v = verification;
    const ok = v.success;
    const groups = {};
    v.checks.forEach(c => (groups[c.category] = groups[c.category] || []).push(c));
    const fixes = [...new Map(v.checks.filter(c => c.fix).map(c => [c.fix, c])).values()];
    return `
      <div class="panel"><div class="hero-result ${ok ? "sev-good" : "sev-critical"}"><span class="big">${ok ? "✓" : "!"}</span><div>
        <h2>${ok ? "HeptagonesLM est installé et opérationnel" : "Installation terminée avec des points à corriger"}</h2>
        <p>${v.counts.ok} contrôle(s) conforme(s)${v.counts.warning ? `, ${v.counts.warning} avertissement(s)` : ""}${v.counts.error ? `, ${v.counts.error} erreur(s)` : ""}.
          ${v.url ? `Adresse de l'interface : <b>${esc(v.url)}</b>` : ""}</p></div></div>
        <div class="actions">
          ${v.url ? `<a class="btn primary" href="${esc(v.url)}" target="_blank" rel="noopener">Ouvrir HeptagonesLM</a>` : ""}
          <a class="btn" href="/api/report">Télécharger le rapport d'installation</a>
          <button type="button" class="btn" id="reverify">Relancer la vérification</button>
          <button type="button" class="btn primary" id="to-recette">Passer à la recette</button>
          <button type="button" class="btn ghost" id="close">Fermer l'assistant</button>
        </div></div>
      ${v.access_url ? `<div class="panel"><h2>Test depuis un poste utilisateur</h2>
        <p class="muted">Depuis le poste d'un utilisateur membre du groupe autorisé, ouvrir l'adresse ci-dessous : la page indique sous quel nom il est reconnu (Kerberos ou NTLM) et lui demande s'il a dû saisir un mot de passe. Le résultat apparaît ici après « Relancer la vérification ».</p>
        ${cmdHtml({ command: v.access_url })}</div>` : ""}
      ${fixes.length || fixResult ? `<div class="panel">${fixes.length ? `<div class="actions">${fixes.map(c => `<button type="button" class="btn primary" data-vfix="${c.fix}">${esc(c.fix_label || "Corriger")}</button>`).join("")}</div>` : ""}
        <div class="result" id="fix-result">${renderResult(fixResult)}</div></div>` : ""}
      <div class="panel">${Object.entries(groups).map(([g, items]) => `<div class="check-group"><h3>${esc(g)}</h3><div class="check-list">
        ${items.map(checkRow).join("")}
      </div></div>`).join("")}</div>`;
  }
  STEPS[VERIFY].after = () => {
    bindCopy();
    $("reverify")?.addEventListener("click", () => runVerify());
    $("to-recette")?.addEventListener("click", () => { maxStep = Math.max(maxStep, RECETTE); go(RECETTE); });
    document.querySelectorAll("[data-vfix]").forEach(b => b.addEventListener("click", async () => {
      fixResult = { busy: "Correction en cours…" }; render();
      try {
        const res = await api("/api/fix", { fix: b.dataset.vfix, answers: A, final: true });
        fixResult = { status: "ok", message: res.message };
      } catch (e) {
        fixResult = { status: "error", message: e.message };
      }
      await runVerify();
    }));
    $("close")?.addEventListener("click", async () => {
      try { await api("/api/shutdown", {}); } catch (e) { /* déjà arrêté */ }
      try { sessionStorage.removeItem("heptagoneslm_setup"); } catch (e) { /* stockage indisponible */ }
      $("step-body").innerHTML = `<div class="panel"><h2>Assistant fermé</h2><p class="muted">Vous pouvez fermer cet onglet. Pour modifier la configuration plus tard, relancez HeptagonesLM-Setup.exe.</p></div>`;
    });
  };

  /* 12. Recette */
  let rctx = null, rjob = null, rpv = null;
  const ropt = { hours: 24, anonymize: true, email_to: "", itsm: true, outsider_user: "", outsider_password: "", member_user: "", member_password: "" };
  const pvMeta = { client: "", redacteur: "", valideur: "", comment: "" };
  async function loadRecette() {
    rctx = null; render();
    try {
      rctx = await api("/api/recette/context", {});
      const st = await api("/api/recette/state");
      if (st.status !== "idle") rjob = st;
      if (rjob && rjob.status === "running") pollRecette();
    } catch (e) { rctx = { installed: null, error: e.message }; }
    render();
  }
  async function startRecette() {
    showError(""); rpv = null;
    try { rjob = await api("/api/recette/start", { options: ropt }); } catch (e) { showError(e.message); return; }
    render(); pollRecette();
  }
  async function pollRecette() {
    while (rjob && rjob.status === "running") {
      await sleep(600);
      try { rjob = await api("/api/recette/state"); } catch (e) { showError(e.message); break; }
      const el = $("recette-progress");
      if (el && rjob.status === "running") el.outerHTML = recetteProgress(rjob); else render();
    }
  }
  function recetteProgress(j) {
    const mark = st => st.state === "running" ? '<span class="spinner"></span>' : dot(st.state === "error" ? "error" : "ok");
    return `<div class="panel" id="recette-progress"><div class="panel-head"><div><h2><span class="spinner"></span> Recette en cours</h2>
      <p class="muted">La collecte planifiée est déclenchée puis chaque serveur de licences est interrogé en direct${j.elapsed != null ? ` · ${Math.round(j.elapsed)} s` : ""}.</p></div></div>
      <ul class="probe-list">${(j.steps || []).map(st => `<li class="${esc(st.state)}">${mark(st)}<span>${esc(st.label)}</span>
        <small>${st.seconds != null ? `${String(st.seconds).replace(".", ",")} s` : "en cours…"}</small></li>`).join("")}</ul></div>`;
  }
  const SEVTXT = { ok: "Conforme", warning: "Réserve", error: "Non conforme", info: "Information" };
  function rtable(t) {
    const verdictCol = t.columns[t.columns.length - 1] === "Résultat" ? t.columns.length - 1 : -1;
    const cls = (v, i) => i !== verdictCol ? "" : v === "identique" ? "same" : "diff";
    return `<div class="table-wrap"><table class="rtable"><thead><tr>${t.columns.map(c => `<th>${esc(c)}</th>`).join("")}</tr></thead>
      <tbody>${t.rows.map(r => `<tr>${r.map((v, i) => `<td class="${cls(v, i)}">${esc(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }
  function itemHtml(it) {
    const conf = it.confirm && (it.auto_status === "ok" || it.auto_status === "warning") ? `<div class="confirm-row"><span>${esc(it.confirm)}</span>
      <button type="button" class="btn small ${it.confirmed === true ? "primary" : ""}" data-rconfirm="${esc(it.id)}" data-val="1">Oui</button>
      <button type="button" class="btn small ${it.confirmed === false ? "danger" : ""}" data-rconfirm="${esc(it.id)}" data-val="0">Non</button></div>` : "";
    const files = (it.files || []).filter(f => (rjob.recette.files || []).includes(f));
    return `<div class="ritem">${dot(it.status)}<div class="rbody"><div class="lbl">${esc(it.label)} <span class="tag sev-${({ ok: "good", warning: "warning", error: "critical", info: "info" })[it.status]}">${SEVTXT[it.status] || it.status}</span></div>
      <div class="det">${esc(it.detail)}</div>
      ${it.details && it.details.length ? `<ul>${it.details.map(d => `<li>${esc(d)}</li>`).join("")}</ul>` : ""}
      ${it.table ? (it.id.startsWith("srv:") ? rtable(it.table) : `<details><summary>Détail (${it.table.rows.length} lignes)</summary>${rtable(it.table)}</details>`) : ""}
      ${files.length ? `<div class="files">${files.map(f => `<a href="/api/recette/file?name=${encodeURIComponent(f)}">${esc(f)}</a>`).join(" · ")}</div>` : ""}
      ${conf}</div></div>`;
  }
  function renderRecette() {
    if (!rctx) return `<div class="panel"><span class="spinner"></span> Lecture de l'installation…</div>`;
    if (!rctx.installed) return `<div class="panel"><h2>Aucune installation trouvée</h2><p class="muted">${esc(rctx.error || "La recette porte sur une installation existante (heptagoneslm.yaml dans le dossier d'installation). Installez d'abord HeptagonesLM.")}</p></div>`;
    const I = rctx.installed, web = I.web_mode !== "local";
    const running = rjob && rjob.status === "running", res = rjob && rjob.status === "done" ? rjob.recette : null;
    let html = `<div class="panel"><h2>Installation contrôlée</h2><dl class="kv">
      <dt>Configuration</dt><dd>${esc(I.config_path)}</dd>
      <dt>Serveurs de licences</dt><dd>${I.servers.map(s => esc(`${s.name} (${s.port_at_host})`)).join(", ") || "—"}</dd>
      <dt>Interface</dt><dd>${esc(I.public_url || "poste local")}${I.access_group ? ` · groupe ${esc(I.access_group)}` : ""}</dd>
      <dt>E-mail</dt><dd>${I.email.configured ? "configuré" : "non configuré"}</dd>
      <dt>Tickets ITSM</dt><dd>${I.itsm.length ? I.itsm.map(c => esc(c.name)).join(", ") : "aucun"}</dd></dl></div>`;
    if (!running && !res) {
      html += `<div class="panel"><h2>Paramètres de la recette</h2><div class="form-grid">
        <label class="f"><span>Collecte planifiée à contrôler</span><select data-ropt="hours">${[[1, "1 heure"], [4, "4 heures"], [24, "24 heures (recommandé)"], [72, "3 jours"], [168, "7 jours"]].map(([v, l]) => `<option value="${v}" ${+ropt.hours === v ? "selected" : ""}>${l}</option>`).join("")}</select>
          <span class="hint">Collectes réussies et interruptions sur la période ; faire la recette après au moins une journée de fonctionnement.</span></label>
        ${I.email.configured ? `<label class="f"><span>Destinataire de l'e-mail de test</span><input type="text" data-ropt="email_to" value="${esc(ropt.email_to)}" placeholder="${esc((I.email.recipients || [])[0] || "dsi@entreprise.local")}"></label>` : ""}
        <div class="full" style="display:flex;flex-direction:column;gap:12px">
          <label class="toggle"><input type="checkbox" data-ropt="anonymize" ${ropt.anonymize ? "checked" : ""}><span>Anonymiser les sorties enregistrées<small>Noms d'utilisateurs et de postes remplacés (utilisateur001, poste001…) dans les sorties lmstat conservées comme preuves et transmissibles pour les tests de non-régression.</small></span></label>
          ${I.itsm.length ? `<label class="toggle"><input type="checkbox" data-ropt="itsm" ${ropt.itsm ? "checked" : ""}><span>Ouvrir un ticket de test dans ${esc(I.itsm.map(c => c.name).join(", "))}<small>Ticket ouvert, commenté puis résolu automatiquement.</small></span></label>` : ""}
        </div>
        ${web ? `<div class="full"><h3 class="sub">Contrôle de la restriction d'accès (facultatif)</h3><p class="muted">Un compte authentifié mais <b>hors</b> du groupe ${esc(I.access_group || "autorisé")} doit être refusé. Le compte membre permet d'écarter un faux positif. Sans comptes, le refus sera à confirmer manuellement.</p></div>
          <label class="f"><span>Compte hors du groupe</span><input type="text" data-ropt="outsider_user" value="${esc(ropt.outsider_user)}" placeholder="ENTREPRISE\\compte" autocomplete="off"></label>
          <label class="f"><span>Mot de passe</span><input type="password" data-ropt="outsider_password" value="${esc(ropt.outsider_password)}" autocomplete="new-password"></label>
          <label class="f"><span>Compte membre du groupe</span><input type="text" data-ropt="member_user" value="${esc(ropt.member_user)}" placeholder="ENTREPRISE\\utilisateur" autocomplete="off"></label>
          <label class="f"><span>Mot de passe</span><input type="password" data-ropt="member_password" value="${esc(ropt.member_password)}" autocomplete="new-password"></label>` : ""}
        </div><div class="actions" style="margin-top:14px"><button type="button" class="btn primary" id="start-recette">Lancer la recette</button></div></div>`;
    }
    if (running) html += recetteProgress(rjob);
    if (rjob && rjob.status === "error") html += `<div class="panel"><div class="hero-result sev-critical"><span class="big">!</span><div><h2>Recette interrompue</h2><p>${esc(rjob.error)}</p></div></div>
      <div class="actions"><button type="button" class="btn primary" id="start-recette">Relancer la recette</button></div></div>`;
    if (res) {
      const v = res.verdict, cls = { ok: "sev-good", warning: "sev-warning", error: "sev-critical" }[v.level];
      const groups = {};
      res.items.forEach(it => (groups[it.category] = groups[it.category] || []).push(it));
      html += `<div class="panel"><div class="hero-result ${cls}"><span class="big">${v.level === "ok" ? "✓" : "!"}</span><div><h2>${esc(v.label)}</h2>
        <p>${v.counts.ok} conforme(s), ${v.counts.warning} réserve(s), ${v.counts.error} non-conformité(s)${v.pending ? ` · <b>${v.pending} point(s) à confirmer ci-dessous</b>` : ""} · recette du ${esc(new Date(res.started).toLocaleString("fr-FR"))}</p>
        <p class="muted">Preuves : <code>${esc(res.folder)}</code></p></div></div></div>
        ${Object.entries(groups).map(([g, items]) => `<div class="panel"><h2>${esc(g)}</h2><div class="ritems">${items.map(itemHtml).join("")}</div></div>`).join("")}
        <div class="panel"><h2>PV de recette</h2><p class="muted">Document Word reprenant chaque point, les comparaisons et un cadre de signature (rédacteur et client).</p>
          <div class="form-grid">
            <label class="f"><span>Client / site</span><input type="text" data-pv="client" value="${esc(pvMeta.client)}" placeholder="Entreprise — site"></label>
            <label class="f"><span>Rédigé par (intégrateur)</span><input type="text" data-pv="redacteur" value="${esc(pvMeta.redacteur)}"></label>
            <label class="f"><span>Validé par (client)</span><input type="text" data-pv="valideur" value="${esc(pvMeta.valideur)}"></label>
            <label class="f full"><span>Commentaire (facultatif)</span><input type="text" data-pv="comment" value="${esc(pvMeta.comment)}"></label>
          </div>
          <div class="actions" style="margin-top:14px"><button type="button" class="btn primary" id="make-pv">Télécharger le PV de recette (Word)</button>
            <a class="btn" href="/api/recette/preuves.zip">Télécharger les preuves (.zip)</a>
            <button type="button" class="btn ghost" id="start-recette">Relancer la recette</button></div>
          <div class="result" id="pv-result">${rpv ? `${dot("ok")}<div><b>PV généré : <a href="${esc(rpv.url)}">${esc(rpv.file)}</a></b></div>` : ""}</div></div>`;
    }
    return html;
  }
  STEPS[RECETTE].after = () => {
    const root = $("step-body");
    root.querySelectorAll("[data-ropt]").forEach(el => el.addEventListener(el.type === "checkbox" || el.tagName === "SELECT" ? "change" : "input", () => {
      ropt[el.dataset.ropt] = el.type === "checkbox" ? el.checked : el.tagName === "SELECT" ? +el.value : el.value;
    }));
    root.querySelectorAll("[data-pv]").forEach(el => el.addEventListener("input", () => { pvMeta[el.dataset.pv] = el.value; }));
    root.querySelectorAll("#start-recette").forEach(b => b.addEventListener("click", startRecette));
    root.querySelectorAll("[data-rconfirm]").forEach(b => b.addEventListener("click", async () => {
      try {
        const r = await api("/api/recette/confirm", { id: b.dataset.rconfirm, confirmed: b.dataset.val === "1" });
        const items = rjob.recette.items;
        items[items.findIndex(i => i.id === r.item.id)] = r.item;
        rjob.recette.verdict = r.verdict;
        render();
      } catch (e) { showError(e.message); }
    }));
    $("make-pv")?.addEventListener("click", async () => {
      try {
        rpv = await api("/api/recette/pv", pvMeta);
        rjob.recette.files = [...new Set([...(rjob.recette.files || []), rpv.file])];
        render();
        window.location.href = rpv.url;
      } catch (e) { showError(e.message); }
    });
  };

  /* -- Démarrage ------------------------------------------------------------------------------ */
  $("prev").addEventListener("click", () => go(Math.max(0, step - 1)));
  $("next").addEventListener("click", () => {
    if (step === SUMMARY) startInstall(false);
    else go(step + 1);
  });

  async function init() {
    try { info = await api("/api/info"); }
    catch (e) { showError("Assistant indisponible : " + e.message); return; }
    $("version").textContent = info.version;
    $("sim-banner").hidden = !info.simulated;
    $("sys-badge").textContent = info.simulated ? "Simulation" : (info.os && info.os.hostname) || "Windows";
    A = { ...BASE, ...info.defaults };
    A.servers = (info.defaults.servers || []).map(s => ({ log_path: "", license_type: "flexlm", ...s }));
    const saved = restore();
    if (saved && saved.A) { A = { ...A, ...saved.A }; step = saved.step || 0; maxStep = saved.maxStep || 0; }
    try {
      const st = await api("/api/progress?since=0");
      if (st.status && st.status !== "idle") {
        install = st; logCount = st.log_count; consoleLines = st.logs;
        step = st.status === "done" ? VERIFY : INSTALL; maxStep = step;
        if (st.status === "running") poll();
      }
    } catch (e) { /* pas d'installation en cours */ }
    render();
    if (STEPS[step].enter) STEPS[step].enter();
  }
  init();
})();
