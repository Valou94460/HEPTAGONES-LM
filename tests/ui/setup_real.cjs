/*
 * Installation RÉELLE par l'assistant graphique, enregistrée en vidéo (CI Windows Server).
 *   HeptagonesLM-Setup.exe setup --no-open --port 8792 --token <jeton>     (sans --simulate)
 *   node tests/ui/setup_real.cjs http://127.0.0.1:8792 <jeton> <dossier>
 * Variables : SVC_ACCOUNT, SVC_PWD, CERT_THUMB, ACCESS_GROUP, PUBLIC_HOST ;
 *   facultatives : LICENSE_SERVERS (« NOM=port@serveur;… »), EXPECT_LMUTIL (chemin attendu de lmutil.exe),
 *   ITSM_URL / ITSM_USER / ITSM_PASSWORD (ServiceNow : étape « Tickets ITSM » et ticket de test réel).
 * Le navigateur remplit l'assistant comme un administrateur : corrections des prérequis (rôles IIS,
 * modules), compte de service, SQLite, recherche de lmutil, IIS, installation, vérification finale.
 * Produit : vidéo (.webm), une capture par étape et le déroulé horodaté (deroule.txt).
 * Échoue (code 1) si une étape bloque, si un test est en erreur ou si la vérification n'est pas verte.
 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("fs");
const path = require("path");

(async () => {
  const base = (process.argv[2] || "http://127.0.0.1:8792").replace(/\/$/, "");
  const token = process.argv[3];
  const out = process.argv[4] || "installation-reelle";
  fs.mkdirSync(out, { recursive: true });
  const env = k => { if (!process.env[k]) throw new Error(`variable ${k} manquante`); return process.env[k]; };
  const failures = [];
  const t0 = Date.now();
  const journal = [];
  const log = (...m) => {
    const line = `[${new Date().toISOString().slice(11, 19)} +${Math.round((Date.now() - t0) / 1000)}s] ${m.join(" ")}`;
    console.log(line); journal.push(line);
  };
  const check = (cond, msg) => { if (!cond) { failures.push(msg); log("ÉCHEC :", msg); } };

  const browser = await chromium.launch({ slowMo: 120 });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "fr-FR",
                                             recordVideo: { dir: out, size: { width: 1440, height: 900 } } });
  const page = await context.newPage();
  page.on("pageerror", e => check(false, `erreur JS : ${e.message}`));
  let n = 0;
  const shot = async name => {
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(out, `${String(++n).padStart(2, "0")}_${name}.png`), fullPage: true });
  };
  const title = () => page.textContent("#step-title");
  const next = async expected => {
    await page.click("#next");
    await page.waitForFunction(t => document.getElementById("step-title").textContent === t, expected, { timeout: 5 * 60000 })
      .catch(async () => check(false, `« ${expected} » non atteint (reste sur « ${await title()} » : ${await page.textContent("#error")})`));
    log("Étape :", expected);
  };
  // Test en direct : erreur = échec ; avertissement accepté mais consigné
  const test = async (key, timeout = 120000) => {
    await page.click(`[data-test="${key}"]`);
    await page.waitForSelector(`#res-${key} .status-dot`, { timeout });
    const cls = await page.getAttribute(`#res-${key} .status-dot`, "class");
    const text = (await page.textContent(`#res-${key}`)).trim().replace(/\s+/g, " ");
    log(`Test ${key} :`, cls.includes("sev-good") ? "OK" : cls.includes("sev-warning") ? "AVERTISSEMENT" : "ERREUR", "—", text.slice(0, 160));
    check(!cls.includes("sev-critical"), `test ${key} en erreur : ${text}`);
  };

  try {
    // 1. Bienvenue : mode réel (pas de bandeau de simulation)
    await page.goto(`${base}/?token=${token}`);
    await page.waitForFunction(() => document.getElementById("step-title").textContent === "Bienvenue");
    check(await page.isHidden("#sim-banner"), "bandeau « simulation » affiché : l'assistant n'est pas en mode réel");
    const info = await page.evaluate(() => fetch("/api/info").then(r => r.json()));
    log("Assistant", info.version, "· système :", info.system, "· simulation :", info.simulated, "· administrateur :", info.admin);
    log("Serveur :", info.os.caption || "?", info.os.version || "", "·", info.os.hostname || "");
    check(info.simulated === false && info.system === "Windows", "l'assistant ne tourne pas sur le système Windows réel");
    await shot("bienvenue");

    // 2. Prérequis réels ; corrections automatiques (installation des rôles et modules IIS manquants)
    await next("Vérification du serveur");
    await page.waitForSelector(".check-row", { timeout: 10 * 60000 });
    const chk = await page.evaluate(() => fetch("/api/checks/state").then(r => r.json()));
    log(`Vérification du serveur : ${chk.elapsed} s (lectures en parallèle :`,
        Object.entries(chk.timings || {}).map(([k, v]) => `${k} ${v} s`).join(", ") + ")");
    check(chk.elapsed < 60, `vérification du serveur trop lente : ${chk.elapsed} s (objectif : moins d'une minute)`);
    await shot("prerequis");
    for (let i = 0; i < 8; i++) {
      const fix = await page.$("[data-fix]");
      if (!fix) break;
      log("Correction :", (await fix.textContent()).trim());
      await fix.click();
      await page.waitForFunction(() => document.querySelector("#fix-result .status-dot") && !document.querySelector("#fix-result .spinner"),
        null, { timeout: 20 * 60000 });
      log("  ->", (await page.textContent("#fix-result")).trim().replace(/\s+/g, " ").slice(0, 200));
    }
    const rows = await page.$$eval(".check-row", rs => rs.map(r => ({
      sev: (r.querySelector(".status-dot")?.className || "").replace("status-dot", "").trim(),
      text: r.textContent.trim().replace(/\s+/g, " ").slice(0, 150) })));
    rows.forEach(r => log("  prérequis", r.sev, "—", r.text));
    check(!rows.some(r => r.sev.includes("sev-critical")), "prérequis encore en erreur après les corrections");
    await shot("prerequis_corriges");

    // 3. Emplacement et compte de service (identifiants vérifiés par Windows)
    await next("Emplacement et compte de service");
    await page.fill('[data-bind="install_dir"]', "C:\\HeptagonesLM");
    await page.fill('[data-bind="service_account"]', env("SVC_ACCOUNT"));
    await page.fill('[data-bind="service_password"]', env("SVC_PWD"));
    await test("account");
    await shot("compte_service");

    // 4. Base : SQLite locale
    await next("Base de données");
    await page.click('[data-choice="db_mode"][data-value="sqlite"]');
    await shot("base_sqlite");

    // 5. Serveurs de licences, puis bouton « Rechercher l'outil » (recherche réelle sur ce serveur)
    await next("Serveurs de licences");
    while ((await page.$$("table.servers tbody tr")).length > 1) {
      const rows = await page.$$("[data-del]");
      await rows[rows.length - 1].click();
    }
    // LICENSE_SERVERS : « NOM=port@serveur;NOM2=port@serveur2 » (serveurs de test par défaut)
    const servers = (process.env.LICENSE_SERVERS || "AUTODESK=27000@licsrv-adsk;ANSYS=1055@licsrv-ansys")
      .split(";").map(x => x.trim()).filter(Boolean).map(x => x.split("=").map(y => y.trim()));
    for (const [i, [name, addr]] of servers.entries()) {
      if (i > 0) await page.click("#add-srv");
      await page.fill(`[data-srv="${i}"][data-key="name"]`, name);
      await page.fill(`[data-srv="${i}"][data-key="port_at_host"]`, addr);
    }
    await page.fill('[data-bind="lmutil_path"]', "");
    await page.click("#find-tools");
    log(`Recherche de lmutil.exe (ce serveur, ${servers.map(s => s[1].split("@").pop()).join(", ")})…`);
    await page.waitForFunction(() => !document.querySelector("#tools-found .spinner"), null, { timeout: 10 * 60000 });
    log("  ->", (await page.textContent("#tools-found")).trim().replace(/\s+/g, " ").slice(0, 600));
    // Aucun outil sur ce serveur : copie depuis le premier serveur de licences où il a été trouvé
    if (!(await page.inputValue('[data-bind="lmutil_path"]'))) {
      const copy = await page.$('#tools-found [data-use*="lmutil"]');
      if (copy) {
        log("  copie depuis le serveur de licences :", await copy.getAttribute("data-use"));
        await copy.click();
        await page.waitForFunction(() => document.querySelector('[data-bind="lmutil_path"]').value, null, { timeout: 5 * 60000 })
          .catch(() => {});
      }
    }
    const lmutil = await page.inputValue('[data-bind="lmutil_path"]');
    log("  lmutil retenu :", lmutil || "(aucun)");
    const expected = process.env.EXPECT_LMUTIL;
    check(expected ? lmutil.toLowerCase() === expected.toLowerCase() : !!lmutil,
          `lmutil.exe non retenu par la recherche (${lmutil || "aucun"}${expected ? `, attendu ${expected}` : ""})`);
    await shot("recherche_lmutil");
    for (let i = 0; i < servers.length; i++) await test(`lm${i}`);
    await shot("serveurs_licences");

    // 6. Interface web : IIS, certificat HTTPS, groupe AD autorisé
    await next("Interface web et IIS");
    await page.click('[data-choice="web_mode"][data-value="iis"]');
    await page.fill('[data-bind="public_host"]', env("PUBLIC_HOST"));
    await page.selectOption('[data-bind="iis_cert_thumbprint"]', env("CERT_THUMB"));
    await page.fill('[data-bind="access_group"]', env("ACCESS_GROUP"));
    await test("certificate"); await test("group");
    await shot("interface_iis");

    // 7. E-mail : non configuré (pas de serveur SMTP dans la CI)
    await next("Rapport par e-mail");
    await shot("email");

    // 8. Tickets ITSM : ServiceNow, ticket de test réel, secret chiffré par DPAPI à l'installation
    await next("Tickets ITSM");
    if (process.env.ITSM_URL) {
      await page.check('[data-bind="itsm_enabled"]');
      await page.click('[data-choice="itsm_type"][data-value="servicenow"]');
      await page.fill('[data-bind="itsm_url"]', env("ITSM_URL"));
      await page.fill('[data-bind="itsm_user"]', env("ITSM_USER"));
      await page.fill('[data-bind="itsm_password"]', env("ITSM_PASSWORD"));
      await page.fill('[data-bind="itsm_assignment_group"]', "Support licences");
      await test("itsm");
    }
    await shot("tickets_itsm");

    // 9. Récapitulatif
    await next("Récapitulatif");
    await page.waitForSelector("pre.yaml", { state: "attached", timeout: 60000 });
    await page.waitForFunction(() => !document.getElementById("next").disabled, null, { timeout: 120000 })
      .catch(async () => check(false, `bouton Installer désactivé : ${await page.textContent("#step-body")}`));
    await page.click("details summary");
    if (process.env.ITSM_URL) {
      check(!(await page.content()).includes(env("ITSM_PASSWORD")), "mot de passe ITSM en clair dans le récapitulatif");
      check((await page.textContent("pre.yaml")).includes("dpapi:"), "secret ITSM non référencé en dpapi: dans le YAML");
    }
    await shot("recapitulatif");

    // 9. Installation réelle
    await page.click("#next");
    await page.waitForFunction(() => document.getElementById("step-title").textContent === "Installation");
    log("Installation lancée");
    await page.click("#log-details summary").catch(() => {});
    let last = -1;
    const deadline = Date.now() + 30 * 60000;
    while (Date.now() < deadline) {
      const st = await page.evaluate(() => fetch("/api/progress?since=0").then(r => r.json()));
      const pct = Math.floor(st.percent);
      if (pct !== last) { log(`  ${pct} % —`, st.current || st.status); last = pct; }
      if (st.status !== "running") {
        st.steps.forEach(s => log("  étape", s.status, "—", s.label, s.duration != null ? `${s.duration} s` : "", s.message ? `(${s.message})` : ""));
        fs.writeFileSync(path.join(out, "journal_installation.txt"), st.logs.map(l => `${l.t} [${l.level}] ${l.msg}`).join("\n") + "\n");
        check(st.status === "done", `installation terminée avec le statut ${st.status} : ${st.error || ""}`);
        break;
      }
      if (last >= 50 && n < 12) await shot("installation_en_cours");
      await page.waitForTimeout(3000);
    }
    await page.waitForFunction(() => document.getElementById("step-title").textContent === "Vérification de l'installation",
      null, { timeout: 5 * 60000 }).catch(async () => check(false, `vérification non atteinte : ${await page.textContent("#step-body")}`));

    // 10. Vérification finale (contrôles réels : tâches, IIS, HTTPS, SSO, base, collecte)
    await page.waitForSelector(".hero-result", { timeout: 5 * 60000 });
    const heading = (await page.textContent(".hero-result h2")).trim();
    log("Vérification :", heading);
    const checks = await page.$$eval(".check-row", rs => rs.map(r => ({
      sev: (r.querySelector(".status-dot")?.className || "").replace("status-dot", "").trim(),
      text: r.textContent.trim().replace(/\s+/g, " ").slice(0, 180) })));
    checks.forEach(c => log("  contrôle", c.sev, "—", c.text));
    check(!checks.some(c => c.sev.includes("sev-critical")), "contrôle(s) en erreur à la vérification finale");
    check(heading.includes("opérationnel"), `vérification finale : ${heading}`);
    await shot("verification_finale");
    const report = await page.evaluate(() => fetch("/api/report").then(r => r.text()));
    fs.writeFileSync(path.join(out, "rapport_installation.txt"), report);

    // 11. Recette : collecte déclenchée (compte de service), lmstat en direct comparé à la base, contrôles, PV
    await page.click("#to-recette");
    await page.waitForFunction(() => document.getElementById("step-title").textContent === "Recette", null, { timeout: 60000 });
    log("Étape : Recette");
    await page.waitForSelector("#start-recette", { timeout: 120000 });
    await page.selectOption('[data-ropt="hours"]', "1");
    await page.fill('[data-ropt="outsider_user"]', env("SVC_ACCOUNT"));        // authentifié, hors du groupe
    await page.fill('[data-ropt="outsider_password"]', env("SVC_PWD"));
    if (process.env.TOUR_USER) {
      await page.fill('[data-ropt="member_user"]', process.env.TOUR_USER);    // membre du groupe autorisé
      await page.fill('[data-ropt="member_password"]', env("SVC_PWD"));
    }
    await shot("recette_parametres");
    const r0 = Date.now();
    await page.click("#start-recette");
    await page.waitForSelector("#make-pv", { timeout: 15 * 60000 })
      .catch(async () => check(false, `recette non terminée : ${(await page.textContent("#step-body")).slice(0, 400)}`));
    let rs = await page.evaluate(() => fetch("/api/recette/state").then(r => r.json()));
    log(`Recette terminée en ${Math.round((Date.now() - r0) / 1000)} s :`, rs.recette?.verdict?.label);
    (rs.recette?.items || []).forEach(i => log("  recette", i.status, "—", i.label, "·", (i.detail || "").slice(0, 160)));
    const ri = Object.fromEntries((rs.recette?.items || []).map(i => [i.id, i]));
    check(!(rs.recette?.items || []).some(i => i.status === "error"), "point(s) de recette en erreur");
    check(ri.collecte?.status === "ok", "collecte déclenchée par la recette en échec");
    for (const s of (rs.recette?.items || []).filter(i => i.id.startsWith("srv:"))) {
      check(s.status !== "error", `comparaison ${s.label} : ${s.detail}`);
      (s.table?.rows || []).forEach(r => log("    ", r.join(" | ")));
    }
    check(ri.refus?.status === "ok", `refus hors du groupe : ${ri.refus?.detail}`);
    await shot("recette_resultats");
    // Confirmations vérifiées par le script : ticket présent dans l'outil, rapport Excel valide
    if (process.env.ITSM_URL && ri["itsm:servicenow"]?.confirm) {
      const inc = await context.request.get(`${process.env.ITSM_URL}/incidents.json`, { ignoreHTTPSErrors: true });
      const list = inc.ok() ? await inc.json() : [];
      log(`  incidents reçus par l'outil ITSM : ${list.length}`);
      if (list.some(x => x.state === "6")) await page.click('[data-rconfirm="itsm:servicenow"][data-val="1"]');
    }
    if (ri.rapport?.confirm) {
      const x = await context.request.get(`${base}/api/recette/file?name=rapport_recette.xlsx`);
      const body = x.ok() ? await x.body() : Buffer.alloc(0);
      fs.writeFileSync(path.join(out, "rapport_recette.xlsx"), body);
      log(`  rapport Excel : ${body.length} octets`);
      if (body.slice(0, 2).toString() === "PK") await page.click('[data-rconfirm="rapport"][data-val="1"]');
    }
    await page.waitForTimeout(500);
    await page.fill('[data-pv="client"]', "CI GitHub — Windows Server 2025");
    await page.fill('[data-pv="redacteur"]', "Parcours automatisé de la CI");
    const [pvdl] = await Promise.all([page.waitForEvent("download", { timeout: 60000 }), page.click("#make-pv")]);
    await pvdl.saveAs(path.join(out, pvdl.suggestedFilename()));
    log("  PV :", pvdl.suggestedFilename());
    const zip = await context.request.get(`${base}/api/recette/preuves.zip`);
    if (zip.ok()) fs.writeFileSync(path.join(out, "preuves_recette.zip"), await zip.body());
    rs = await page.evaluate(() => fetch("/api/recette/state").then(r => r.json()));
    log("Recette (après confirmations) :", rs.recette?.verdict?.label);
    await shot("recette_pv");
  } catch (e) {
    check(false, `arrêt du parcours : ${e.message}`);
    await shot("arret").catch(() => {});
  } finally {
    await context.close();                       // écrit la vidéo
    await browser.close();
    const videos = fs.readdirSync(out).filter(f => f.endsWith(".webm"));
    if (videos.length) fs.renameSync(path.join(out, videos[0]), path.join(out, "installation_assistant.webm"));
    log(failures.length ? `ÉCHEC (${failures.length})` : "Installation réelle par l'assistant : OK");
    fs.writeFileSync(path.join(out, "deroule.txt"), journal.join("\n") + "\n");
  }
  if (failures.length) { failures.forEach(f => console.error(" -", f)); process.exit(1); }
})();
