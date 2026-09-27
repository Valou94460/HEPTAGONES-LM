/* FreeLM — assistant d'installation : navigation, formulaires, tests, installation, vérification. */
"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SECRETS = ["service_password", "db_password", "smtp_password"];
  const BASE = {
    install_dir: "C:\\FreeLM", service_account: "", service_password: "", collect_interval: 5,
    db_mode: "sqlserver", db_server: "", db_name: "FreeLM", db_driver: "ODBC Driver 18 for SQL Server", db_auth: "windows",
    db_user: "", db_password: "", db_encrypt: true, db_trust_cert: false, db_create: true,
    lmutil_path: "", servers: [],
    web_mode: "iis", web_port: 8765, public_host: "", access_group: "", iis_site: "FreeLM", iis_port: 443,
    iis_cert_thumbprint: "", tls_cert: "", tls_key: "", ldap_servers: [], base_dn: "", upn_suffix: "", open_firewall: true,
    email_enabled: false, smtp_host: "", smtp_port: 25, smtp_security: "starttls", smtp_user: "", smtp_password: "",
    sender: "", recipients: [],
  };
  let info = null;
  let A = {};
  let step = 0, maxStep = 0;
  let checks = null, blockingCount = 0, checksBusy = false, fixResult = null;
  let preview = null, verification = null;
  let pollTimer = null, logCount = 0, install = { status: "idle" };
  const results = {};

  /* -- Utilitaires ------------------------------------------------------------------------ */
  async function api(path, body) {
    const opts = body === undefined ? { credentials: "same-origin" } : {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-FreeLM-Setup": "1" }, body: JSON.stringify(body),
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
      sessionStorage.setItem("freelm_setup", JSON.stringify({ A: safe, step: Math.min(step, 7), maxStep: Math.min(maxStep, 7) }));
    } catch (e) { /* stockage indisponible */ }
  }
  function restore() {
    try { return JSON.parse(sessionStorage.getItem("freelm_setup") || "null"); } catch (e) { return null; }
  }
  const SEV = { ok: ["good", "✓"], warning: ["warning", "!"], error: ["critical", "✕"], info: ["info", "i"] };
  const dot = s => `<span class="status-dot sev-${(SEV[s] || SEV.info)[0]}" aria-hidden="true">${(SEV[s] || SEV.info)[1]}</span>`;
  const ICON = {
    check: '<path d="M4 12l5 5L20 6"/>', db: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
    shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>', monitor: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
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
        el.closest(".f")?.classList.remove("invalid");
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
      if (b.dataset.kind === "lmstat") extra.port_at_host = A.servers[+b.dataset.index]?.port_at_host || "";
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
    { id: "welcome", title: "Bienvenue", desc: "Cet assistant installe et configure FreeLM sur ce serveur, puis vérifie que chaque composant fonctionne.", render: renderWelcome },
    { id: "checks", title: "Vérification du serveur", desc: "Contrôle des prérequis. Les éléments manquants peuvent être installés automatiquement.", render: renderChecks, enter: () => runChecks() },
    { id: "location", title: "Emplacement et compte de service", desc: "Dossier d'installation et compte Windows dédié qui exécutera les tâches de FreeLM.", render: renderLocation, validate: "location" },
    { id: "database", title: "Base de données", desc: "L'historique est conservé dans SQL Server. L'assistant peut créer la base et les droits du compte de service.", render: renderDatabase, validate: "database" },
    { id: "licenses", title: "Serveurs de licences", desc: "Serveurs FlexLM à surveiller. Testez chacun : l'assistant interroge lmstat comme le fera la collecte.", render: renderLicenses, validate: "licenses" },
    { id: "web", title: "Interface web et IIS", desc: "Mode d'accès des utilisateurs à l'interface.", render: renderWeb, validate: "web" },
    { id: "email", title: "Rapport par e-mail", desc: "Envoi automatique du rapport mensuel (optionnel, modifiable plus tard).", render: renderEmail, validate: "email" },
    { id: "summary", title: "Récapitulatif", desc: "Vérifiez la configuration avant de lancer l'installation.", render: renderSummary, enter: () => loadSummary(), next: "Installer" },
    { id: "install", title: "Installation", desc: "Ne fermez pas cette page pendant l'installation.", render: renderInstall },
    { id: "verify", title: "Vérification de l'installation", desc: "Contrôle de bout en bout de chaque composant installé.", render: renderVerify, enter: () => runVerify() },
  ];

  function renderStepper() {
    const locked = install.status === "running";
    $("stepper").innerHTML = STEPS.map((s, i) => {
      const cls = i === step ? "current" : (i < maxStep || (i < step) ? "done" : "");
      const reachable = !locked && i <= maxStep && i !== step && (i < 8 || install.status === "done") && !(step >= 8 && i < 8 && install.status === "done");
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
    prev.hidden = step === 0 || step >= 8;
    next.hidden = step >= 8;
    next.textContent = s.next || "Suivant";
    next.disabled = step === 7 && (!preview || preview.errors.length > 0 || blockingCount > 0 || checksBusy);
    $("foot-msg").textContent = step === 7 && blockingCount > 0 ? `${blockingCount} prérequis bloquant(s) à corriger` : "";
    document.title = `${s.title} — Installation de FreeLM`;
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
      ["server", "Serveurs de licences", "Déclaration et test en direct de chaque serveur FlexLM (lmstat)."],
      ["globe", "IIS et connexion Windows", "Site HTTPS, authentification unique, restriction à un groupe AD, pare-feu."],
      ["clock", "Tâches planifiées", "Collecte toutes les 5 minutes, rapport mensuel, e-mails, purge, interface permanente."],
      ["check", "Vérification finale", "Chaque composant est testé de bout en bout, avec un rapport d'installation."],
    ];
    return `
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
          <li>Le <b>groupe AD</b> des personnes autorisées à consulter FreeLM.</li>
        </ul></div>
      </div>`;
  }

  /* 2. Vérification du serveur */
  function checksHtml(list) {
    if (!list) return `<div class="panel"><span class="spinner"></span> Vérification en cours…</div>`;
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
        ${items.map(c => `<div class="check-row">${dot(c.status)}<div><div class="lbl">${esc(c.label)}</div><div class="det">${esc(c.detail)}
          ${c.help ? ` · <a href="${esc(c.help)}" target="_blank" rel="noopener">documentation</a>` : ""}</div></div><span></span></div>`).join("")}
      </div></div>`).join("")}</div>`;
  }
  function renderChecks() { return checksHtml(checks); }
  function bindChecks(root = document) {
    root.querySelector("#recheck")?.addEventListener("click", () => runChecks());
    root.querySelectorAll("[data-fix]").forEach(b => b.addEventListener("click", () => runFix(b.dataset.fix)));
  }
  STEPS[1].after = () => bindChecks();
  async function runChecks() {
    checksBusy = true;
    try {
      const res = await api("/api/checks", { answers: A, final: step >= 7 });
      checks = res.checks; blockingCount = res.blocking;
    } catch (e) { showError(e.message); }
    checksBusy = false;
    if (step === 1 || step === 7) render();
  }
  async function runFix(fix) {
    checksBusy = true;
    fixResult = { busy: "Installation en cours… cette opération peut prendre plusieurs minutes." };
    render();
    try {
      const res = await api("/api/fix", { fix, answers: A, final: step >= 7 });
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
        <div class="form-grid">${input("install_dir", "Dossier", { placeholder: "C:\\FreeLM", hint: "Créé s'il n'existe pas ; une configuration existante est sauvegardée." })}
        ${input("collect_interval", "Intervalle de collecte (minutes)", { type: "number", min: 1, max: 60, hint: "5 minutes recommandées" })}</div></div>
      <div class="panel"><h2>Compte de service</h2><p class="muted">Compte dédié, non administrateur, qui exécutera les tâches planifiées (SYSTEM est refusé). Il reçoit les droits nécessaires sur le dossier et la base.</p>
        <div class="form-grid">${input("service_account", "Compte (DOMAINE\\compte, ou DOMAINE\\gmsa$)", { rerender: false, placeholder: "ENTREPRISE\\svc_freelm" })}
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
        ${input("db_name", "Nom de la base", { placeholder: "FreeLM" })}
        ${select("db_driver", "Pilote ODBC", drivers)}
        ${select("db_auth", "Authentification", [["windows", "Windows (compte de service)"], ["sql", "Identifiant SQL"]], { rerender: true })}
        ${A.db_auth === "sql" ? input("db_user", "Identifiant SQL") + input("db_password", "Mot de passe SQL", { type: "password", hint: "Stocké dans la variable d'environnement machine FREELM_DB_PASSWORD." }) : ""}
        <div class="full" style="display:flex;flex-direction:column;gap:12px">
          ${toggle("db_create", "Créer la base et les droits si nécessaire", "Base en récupération SIMPLE, connexion Windows du compte de service, rôles lecture / écriture. Nécessite un compte autorisé (sysadmin ou dbcreator).")}
          ${toggle("db_encrypt", "Chiffrer la connexion (Encrypt=yes)", "Recommandé : le certificat de l'instance SQL doit être reconnu par ce serveur.")}
          ${toggle("db_trust_cert", "Faire confiance au certificat du serveur SQL sans le vérifier", "À réserver aux essais : déconseillé en production.")}
        </div></div>
        ${testBlock("database", "database", "Tester la connexion")}</div>`
      : `<div class="panel"><h2>Base SQLite</h2><p class="muted">Fichier <code>${esc(A.install_dir)}\\data\\freelm.db</code>, créé à l'installation. Migration vers SQL Server possible plus tard (nouvelle installation + reprise des logs).</p></div>`}`;
  }

  /* 5. Serveurs de licences */
  function renderLicenses() {
    if (!A.servers.length) A.servers.push({ name: "", port_at_host: "", collectors: ["lmstat"], log_path: "" });
    const found = info.lmutil_found || [];
    const rows = A.servers.map((s, i) => `
      <tr>
        <td><div class="f" data-f="servers.${i}.name"><input type="text" data-srv="${i}" data-key="name" value="${esc(s.name)}" placeholder="AUTODESK" aria-label="Nom du serveur"><span class="ferr"></span></div></td>
        <td><div class="f" data-f="servers.${i}.port_at_host"><input type="text" data-srv="${i}" data-key="port_at_host" value="${esc(s.port_at_host)}" placeholder="27000@licsrv1" aria-label="Adresse port@serveur"><span class="ferr"></span></div></td>
        <td><label class="toggle"><input type="checkbox" data-srv="${i}" data-key="log" ${s.collectors.includes("log") ? "checked" : ""}><span>Log debug</span></label>
          ${s.collectors.includes("log") ? `<div class="f" data-f="servers.${i}.log_path" style="margin-top:6px"><input type="text" data-srv="${i}" data-key="log_path" value="${esc(s.log_path || "")}" placeholder="\\\\licsrv1\\logs$\\license.log"><span class="ferr"></span></div>` : ""}</td>
        <td style="width:240px"><button type="button" class="btn small" data-test="lm${i}" data-kind="lmstat" data-index="${i}">Tester</button>
          <div class="result" id="res-lm${i}">${renderResult(results["lm" + i])}</div></td>
        <td><button type="button" class="icon-x" data-del="${i}" title="Retirer" aria-label="Retirer ce serveur">×</button></td>
      </tr>`).join("");
    return `
      <div class="panel"><h2>Outil lmutil</h2><div class="form-grid">
        ${input("lmutil_path", "Chemin de lmutil.exe", { full: true, hint: found.length ? `Détecté : ${esc(found.join(" · "))}` : "Fourni avec Autodesk Network License Manager ou tout serveur FlexLM (à copier par exemple dans C:\\FreeLM\\bin)." })}
      </div></div>
      <div class="panel"><div class="panel-head"><div><h2>Serveurs</h2><p class="muted">Triade redondante : <code>27000@srv1:27000@srv2:27000@srv3</code>. Le log debug ajoute les refus de licence et des sessions à la seconde.</p></div>
        <button type="button" class="btn small" id="add-srv">+ Ajouter un serveur</button></div>
        <table class="servers"><thead><tr><th>Nom</th><th>Adresse port@serveur</th><th>Collecte</th><th>Test</th><th></th></tr></thead><tbody>${rows}</tbody></table>
        <div class="f" data-f="servers"><span class="ferr"></span></div></div>`;
  }
  STEPS[4].after = () => {
    const root = $("step-body");
    root.querySelectorAll("[data-srv]").forEach(el => {
      const i = +el.dataset.srv, key = el.dataset.key;
      el.addEventListener(el.type === "checkbox" ? "change" : "input", () => {
        const s = A.servers[i];
        if (key === "log") { s.collectors = el.checked ? ["lmstat", "log"] : ["lmstat"]; save(); render(); return; }
        s[key] = el.value; save();
      });
    });
    root.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", () => {
      A.servers.splice(+b.dataset.del, 1); Object.keys(results).filter(k => k.startsWith("lm")).forEach(k => delete results[k]); save(); render();
    }));
    $("add-srv").addEventListener("click", () => { A.servers.push({ name: "", port_at_host: "", collectors: ["lmstat"], log_path: "" }); save(); render(); });
  };

  /* 6. Interface web */
  function renderWeb() {
    const certs = info.certificates || [];
    const certOptions = certs.length ? certs.map(c => [c.thumbprint, `${c.friendly || c.subject} — ${c.dns || c.subject} (jusqu'au ${c.not_after})`])
                                     : [["", "Aucun certificat avec clé privée dans LocalMachine\\My"]];
    const mode = A.web_mode;
    let detail = "";
    if (mode === "iis") {
      detail = `<div class="panel"><h2>Site IIS</h2><p class="muted">IIS authentifie les utilisateurs avec leur session Windows (aucune saisie), vérifie le groupe autorisé et relaie vers FreeLM, qui n'écoute que sur ce serveur.</p>
        <div class="form-grid">
          ${input("public_host", "Nom DNS de l'interface", { placeholder: "freelm.entreprise.local", hint: "Nom saisi dans le navigateur ; enregistrement DNS vers ce serveur." })}
          ${input("iis_site", "Nom du site IIS")}
          ${select("iis_cert_thumbprint", "Certificat HTTPS", certOptions, { full: true })}
          ${input("iis_port", "Port HTTPS", { type: "number", min: 1, max: 65535 })}
          ${input("web_port", "Port interne de FreeLM", { type: "number", min: 1024, max: 65535, hint: "Écoute locale uniquement (127.0.0.1)." })}
          ${input("access_group", "Groupe AD autorisé", { placeholder: "ENTREPRISE\\GG_FreeLM_Lecteurs", hint: "Vide : tout utilisateur du domaine." })}
        </div>
        ${testBlock("certificate", "certificate", "Vérifier le certificat")}${testBlock("group", "group", "Vérifier le groupe")}</div>`;
    } else if (mode === "https") {
      detail = `<div class="panel"><h2>HTTPS intégré et connexion AD</h2><p class="muted">FreeLM sert lui-même l'interface en HTTPS ; les utilisateurs saisissent leurs identifiants Windows, vérifiés par l'Active Directory (LDAPS).</p>
        <div class="form-grid">
          ${input("public_host", "Nom DNS de l'interface", { placeholder: "freelm.entreprise.local" })}
          ${input("web_port", "Port HTTPS", { type: "number", min: 1024, max: 65535 })}
          ${input("tls_cert", "Certificat (PEM)", { placeholder: "C:\\FreeLM\\certs\\freelm.pem" })}
          ${input("tls_key", "Clé privée (PEM)", { placeholder: "C:\\FreeLM\\certs\\freelm.key" })}
          ${input("ldap_servers", "Contrôleurs de domaine", { list: true, full: true, placeholder: "ldaps://dc01.entreprise.local, ldaps://dc02.entreprise.local" })}
          ${input("base_dn", "Base de recherche", { placeholder: "DC=entreprise,DC=local" })}
          ${input("upn_suffix", "Suffixe UPN", { placeholder: "entreprise.local" })}
          ${input("access_group", "Groupe AD autorisé", { placeholder: "ENTREPRISE\\GG_FreeLM_Lecteurs", hint: "Vide : tout utilisateur du domaine." })}
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
        ${input("sender", "Expéditeur", { placeholder: "FreeLM <freelm@entreprise.local>" })}
        ${input("smtp_user", "Identifiant (optionnel)")}
        ${input("smtp_password", "Mot de passe (optionnel)", { type: "password", hint: "Stocké dans la variable d'environnement machine FREELM_SMTP_PASSWORD." })}
        ${input("recipients", "Destinataires", { list: true, full: true, placeholder: "dsi@entreprise.local, achats@entreprise.local" })}
      </div>${testBlock("smtp", "smtp", "Envoyer un message de test au premier destinataire")}</div>` : ""}`;
  }

  /* 8. Récapitulatif */
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
        ${block("Serveurs de licences", 4, [["lmutil", esc(a.lmutil_path)], ...a.servers.map(s => [s.name, esc(s.port_at_host) + (s.collectors.includes("log") ? " + log debug" : "")])])}
        ${block("Interface web", 5, [["Accès", esc(web)], a.access_group && a.web_mode !== "local" ? ["Groupe autorisé", esc(a.access_group)] : null, a.web_mode === "iis" ? ["Site IIS", esc(a.iis_site)] : null, a.web_mode !== "local" ? ["Pare-feu", a.open_firewall ? "Port ouvert" : "Inchangé"] : null])}
        ${block("E-mail", 6, a.email_enabled ? [["Relais", esc(a.smtp_host) + ":" + esc(a.smtp_port)], ["Destinataires", esc((a.recipients || []).join(", "))]] : [["Rapport par e-mail", "Désactivé"]])}
        <div class="panel"><h2>Tâches planifiées</h2><dl class="kv">${preview.plan.tasks.map(t => `<dt>${esc(t.name.replace("FreeLM - ", ""))}</dt><dd>${esc(t.schedule)}</dd>`).join("")}</dl></div>
      </div>
      <div class="panel"><h2>Étapes de l'installation</h2><ol class="plan">${preview.plan.steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol></div>
      <div class="panel"><details><summary>Configuration générée (freelm.yaml)</summary><pre class="yaml">${esc(preview.yaml)}</pre></details></div>`;
  }
  STEPS[7].after = () => {
    bindChecks();
    document.querySelectorAll("[data-goto]").forEach(b => b.addEventListener("click", () => go(+b.dataset.goto)));
  };

  /* 9. Installation */
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
  STEPS[8].after = () => {
    const c = $("console");
    if (c && consoleLines.length) { c.innerHTML = consoleLines.map(l => `<div class="${esc(l.level)}"><span class="t">${esc(l.t)}</span>${esc(l.msg)}</div>`).join(""); c.scrollTop = c.scrollHeight; }
    $("retry")?.addEventListener("click", () => startInstall(true));
    $("back-summary")?.addEventListener("click", () => { install = { status: "idle" }; go(7); });
  };
  async function startInstall(resume = false) {
    showError("");
    try { await api("/api/install", { answers: A, resume }); }
    catch (e) { showError(e.message); return; }
    if (!resume) { logCount = 0; consoleLines = []; }
    install = { ...install, status: "running" };
    if (step !== 8) { step = 8; maxStep = Math.max(maxStep, 8); }
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
      if (step === 8) {
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
      else if (st.status === "done") { maxStep = 9; setTimeout(() => go(9), 1200); }
      else render();
    } catch (e) { showError(e.message); pollTimer = setTimeout(poll, 2000); }
  }

  /* 10. Vérification */
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
    return `
      <div class="panel"><div class="hero-result ${ok ? "sev-good" : "sev-critical"}"><span class="big">${ok ? "✓" : "!"}</span><div>
        <h2>${ok ? "FreeLM est installé et opérationnel" : "Installation terminée avec des points à corriger"}</h2>
        <p>${v.counts.ok} contrôle(s) conforme(s)${v.counts.warning ? `, ${v.counts.warning} avertissement(s)` : ""}${v.counts.error ? `, ${v.counts.error} erreur(s)` : ""}.
          ${v.url ? `Adresse de l'interface : <b>${esc(v.url)}</b>` : ""}</p></div></div>
        <div class="actions">
          ${v.url ? `<a class="btn primary" href="${esc(v.url)}" target="_blank" rel="noopener">Ouvrir FreeLM</a>` : ""}
          <a class="btn" href="/api/report">Télécharger le rapport d'installation</a>
          <button type="button" class="btn" id="reverify">Relancer la vérification</button>
          <button type="button" class="btn ghost" id="close">Fermer l'assistant</button>
        </div></div>
      <div class="panel">${Object.entries(groups).map(([g, items]) => `<div class="check-group"><h3>${esc(g)}</h3><div class="check-list">
        ${items.map(c => `<div class="check-row">${dot(c.status)}<div><div class="lbl">${esc(c.label)}</div><div class="det">${esc(c.detail)}</div></div><span></span></div>`).join("")}
      </div></div>`).join("")}</div>`;
  }
  STEPS[9].after = () => {
    $("reverify")?.addEventListener("click", () => runVerify());
    $("close")?.addEventListener("click", async () => {
      try { await api("/api/shutdown", {}); } catch (e) { /* déjà arrêté */ }
      try { sessionStorage.removeItem("freelm_setup"); } catch (e) { /* stockage indisponible */ }
      $("step-body").innerHTML = `<div class="panel"><h2>Assistant fermé</h2><p class="muted">Vous pouvez fermer cet onglet. Pour modifier la configuration plus tard, relancez FreeLM-Setup.exe.</p></div>`;
    });
  };

  /* -- Démarrage ------------------------------------------------------------------------------ */
  $("prev").addEventListener("click", () => go(Math.max(0, step - 1)));
  $("next").addEventListener("click", () => {
    if (step === 7) startInstall(false);
    else go(step + 1);
  });

  async function init() {
    try { info = await api("/api/info"); }
    catch (e) { showError("Assistant indisponible : " + e.message); return; }
    $("version").textContent = info.version;
    $("sim-banner").hidden = !info.simulated;
    $("sys-badge").textContent = info.simulated ? "Simulation" : (info.os && info.os.hostname) || "Windows";
    A = { ...BASE, ...info.defaults };
    A.servers = (info.defaults.servers || []).map(s => ({ log_path: "", ...s }));
    const saved = restore();
    if (saved && saved.A) { A = { ...A, ...saved.A }; step = saved.step || 0; maxStep = saved.maxStep || 0; }
    try {
      const st = await api("/api/progress?since=0");
      if (st.status && st.status !== "idle") {
        install = st; logCount = st.log_count; consoleLines = st.logs;
        step = st.status === "done" ? 9 : 8; maxStep = step;
        if (st.status === "running") poll();
      }
    } catch (e) { /* pas d'installation en cours */ }
    render();
    if (STEPS[step].enter) STEPS[step].enter();
  }
  init();
})();
