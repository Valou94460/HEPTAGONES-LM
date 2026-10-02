/*
 * Parcours complet de l'assistant d'installation (mode simulation) dans un vrai navigateur.
 *   heptagoneslm setup --simulate --no-open --port 8790 --token test-token &
 *   node tests/ui/setup_smoke.cjs http://127.0.0.1:8790 test-token captures/
 * Échoue (code 1) si une étape bloque, si la vérification finale n'est pas verte,
 * ou en cas d'erreur JavaScript.
 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

(async () => {
  const base = (process.argv[2] || "http://127.0.0.1:8790").replace(/\/$/, "");
  const token = process.argv[3] || "test-token";
  const out = process.argv[4] || "ui-captures";
  fs.mkdirSync(out, { recursive: true });
  const failures = [];
  const check = (cond, msg) => { if (!cond) failures.push(msg); };
  // Faux ServiceNow HTTPS (tests/fake_servicenow.py) pour le bouton « Ouvrir un ticket de test »
  const snowPort = process.env.ITSM_PORT || "8443";
  const snowUrl = process.env.ITSM_URL || `https://127.0.0.1:${snowPort}`;   // captures : nom DNS d'entreprise
  const snow = spawn(process.env.PYTHON || (process.platform === "win32" ? "python" : "python3"),
                     ["-m", "tests.fake_servicenow", snowPort], { cwd: path.resolve(__dirname, "../.."), stdio: "inherit" });
  process.on("exit", () => snow.kill());
  // ITSM_CA vide : AC approuvée par le système (comme une AC d'entreprise déployée par GPO)
  const caFile = "ITSM_CA" in process.env ? process.env.ITSM_CA : path.resolve(__dirname, "../fixtures/itsm_test_ca.pem");
  const browser = await chromium.launch();
  // SETUP_DOC_SHOTS=1 : captures de la fenêtre seule (documentation) plutôt que de la page entière
  const docShots = !!process.env.SETUP_DOC_SHOTS;
  const page = await browser.newPage({ viewport: docShots ? { width: 1280, height: 860 } : { width: 1440, height: 1000 },
                                       locale: "fr-FR", deviceScaleFactor: docShots ? 1.25 : 1 });
  page.on("pageerror", e => failures.push(`erreur JS : ${e.message}`));
  const shot = async name => { await page.waitForTimeout(250); await page.screenshot({ path: `${out}/setup_${name}.png`, fullPage: !docShots }); };
  const title = () => page.textContent("#step-title");
  const next = async expected => {
    await page.click("#next");
    await page.waitForFunction(t => document.getElementById("step-title").textContent === t, expected, { timeout: 15000 })
      .catch(async () => failures.push(`« ${expected} » non atteint (reste sur « ${await title()} » : ${await page.textContent("#error")})`));
  };
  const test = async (key, expect = "ok") => {
    await page.click(`[data-test="${key}"]`);
    await page.waitForSelector(`#res-${key} .status-dot`, { timeout: 15000 });
    const cls = await page.getAttribute(`#res-${key} .status-dot`, "class");
    const text = await page.textContent(`#res-${key}`);
    console.log(`test ${key} ->`, text.trim().replace(/\s+/g, " ").slice(0, 100));
    const want = { ok: "sev-good", warning: "sev-warning", error: "sev-critical" }[expect];
    check(cls.includes(want), `test ${key} : ${expect} attendu, obtenu ${cls} (${text.trim()})`);
  };

  // Sans jeton : accès refusé
  const denied = await page.goto(`${base}/`);
  check(denied.status() === 403, `accès sans jeton : ${denied.status()} au lieu de 403`);
  // (le 403 volontaire ci-dessus est journalisé par le navigateur : surveillance de la console après)
  page.on("console", m => { if (m.type() === "error") failures.push(`console : ${m.text()}`); });

  // 1. Bienvenue
  await page.goto(`${base}/?token=${token}`);
  await page.waitForFunction(() => document.getElementById("step-title").textContent === "Bienvenue");
  check(!(await page.isHidden("#sim-banner")), "bandeau de simulation absent");
  check(!page.url().includes("token"), "le jeton reste visible dans l'adresse");
  await shot("01_bienvenue");

  // 2. Prérequis : IIS absent -> corrections automatiques
  await next("Vérification du serveur");
  await page.waitForSelector(".check-row");
  const errorsBefore = await page.$$eval(".check-row .sev-critical", e => e.length);
  console.log("prérequis en erreur avant correction :", errorsBefore);
  check(errorsBefore > 0, "la simulation devrait signaler IIS absent");
  await shot("02_prerequis");
  for (let i = 0; i < 4; i++) {
    const fix = await page.$("[data-fix]");
    if (!fix) break;
    await fix.click();
    await page.waitForFunction(() => document.querySelector("#fix-result .status-dot") && !document.querySelector("#fix-result .spinner"), null, { timeout: 60000 });
  }
  const errorsAfter = await page.$$eval(".check-row .sev-critical", e => e.length);
  console.log("prérequis en erreur après correction :", errorsAfter);
  check(errorsAfter === 0, `${errorsAfter} prérequis encore en erreur après correction`);
  await shot("03_prerequis_corriges");

  // 3. Emplacement : validation puis compte de service
  await next("Emplacement et compte de service");
  await page.click("#next");
  await page.waitForSelector(".f.invalid");
  check(await page.$('[data-f="service_password"].invalid'), "mot de passe manquant non signalé");
  await page.fill('[data-bind="service_password"]', "Secret-1");
  await test("account");
  await shot("04_emplacement");

  // 4. Base de données
  await next("Base de données");
  await test("database");
  await shot("05_base");

  // 5. Serveurs de licences (pré-remplis en simulation) : un test par serveur
  await next("Serveurs de licences");
  await test("lm0"); await test("lm1");
  await page.click("#add-srv");
  const rows = await page.$$("table.servers tbody tr");
  await page.fill(`[data-srv="${rows.length - 1}"][data-key="name"]`, "ANCIEN");
  await page.fill(`[data-srv="${rows.length - 1}"][data-key="port_at_host"]`, "27000@serveur-inconnu");
  await test(`lm${rows.length - 1}`, "error");
  await page.click(`[data-del="${rows.length - 1}"]`);
  // Serveur RLM : type, chemin de rlmutil, test en direct (rlmstat)
  await page.click("#add-srv");
  const r = (await page.$$("table.servers tbody tr")).length - 1;
  await page.selectOption(`[data-srv="${r}"][data-key="license_type"]`, "rlm");
  await page.waitForSelector('[data-bind="rlmutil_path"]');
  check(!(await page.$(`[data-srv="${r}"][data-key="log"]`)), "RLM : l'option log debug ne doit pas s'afficher");
  await page.fill(`[data-srv="${r}"][data-key="name"]`, "FOUNDRY");
  await page.fill(`[data-srv="${r}"][data-key="port_at_host"]`, "5053@licsrv-rlm");
  // Bouton « Rechercher l'outil » : rlmutil trouvé sur le serveur RLM, copié sur ce serveur
  await page.click("#find-tools");
  await page.waitForSelector("table.tools-found", { timeout: 15000 });
  const scan = await page.textContent("#tools-found");
  check(scan.includes("licsrv-rlm") && scan.includes("rlmutil.exe"), `recherche de l'outil : ${scan}`);
  await page.click('#tools-found [data-use*="rlmutil"]');
  await page.waitForFunction(() => document.querySelector('[data-bind="rlmutil_path"]').value === "C:\\HeptagonesLM\\bin\\rlmutil.exe", null, { timeout: 15000 })
    .catch(async () => failures.push(`rlmutil non retenu : ${await page.textContent("#tools-found")}`));
  check((await page.textContent("#tools-found")).includes("Copié depuis licsrv-rlm"), "copie de rlmutil non signalée");
  await shot("06c_recherche_outil");
  await test(`lm${r}`);
  await shot("06b_licences_rlm");
  await page.click(`[data-del="${r}"]`);
  await shot("06_licences");

  // 6. Interface web : IIS (recommandé)
  await next("Interface web et IIS");
  await test("certificate"); await test("group");
  await shot("07_interface_iis");
  await page.click('[data-choice="web_mode"][data-value="https"]');
  await page.waitForSelector('[data-bind="tls_cert"]');
  await shot("07b_interface_https");
  await page.click('[data-choice="web_mode"][data-value="iis"]');
  await page.waitForSelector('[data-bind="iis_site"]');

  // 7. E-mail
  await next("Rapport par e-mail");
  await page.check('[data-bind="email_enabled"]');
  await page.waitForSelector('[data-bind="smtp_host"]');
  await test("smtp");
  await shot("08_email");

  // 8. Tickets ITSM : désactivés par défaut, puis ServiceNow avec ticket de test réel
  await next("Tickets ITSM");
  check(!(await page.isChecked('[data-bind="itsm_enabled"]')), "tickets ITSM activés par défaut");
  check(!(await page.$('[data-bind="itsm_url"]')), "champs ITSM visibles alors que désactivés");
  await shot("08b_tickets_itsm_desactives");
  await page.check('[data-bind="itsm_enabled"]');
  await page.waitForSelector('[data-choice="itsm_type"]');
  for (const [kind, field] of [["jira", "itsm_project"], ["glpi", "itsm_app_token"], ["webhook", "itsm_format"], ["servicenow", "itsm_user"]]) {
    await page.click(`[data-choice="itsm_type"][data-value="${kind}"]`);
    await page.waitForSelector(`[data-bind="${field}"]`, { timeout: 5000 }).catch(() => failures.push(`champ ${field} absent pour ${kind}`));
  }
  check((await page.getAttribute('[data-bind="itsm_password"]', "type")) === "password", "mot de passe ITSM affiché en clair");
  // Champs vides : « Suivant » bloqué
  await page.click("#next");
  await page.waitForSelector('[data-f="itsm_url"].invalid', { timeout: 5000 }).catch(() => failures.push("adresse ITSM vide acceptée"));
  await page.fill('[data-bind="itsm_url"]', snowUrl);
  await page.fill('[data-bind="itsm_user"]', "svc_heptagoneslm");
  await page.fill('[data-bind="itsm_assignment_group"]', "Support licences");
  if (caFile) await page.fill('[data-bind="itsm_ca_file"]', caFile);
  for (let i = 0; i < 40; i++) {           // attente du faux ServiceNow
    try { await page.request.get(snowUrl, { ignoreHTTPSErrors: true }); break; } catch (e) { await page.waitForTimeout(250); }
  }
  await page.fill('[data-bind="itsm_password"]', "mauvais-mot-de-passe");
  check(!(await page.$$eval(".ferr", e => e.some(x => x.textContent.trim()))), "messages d'erreur restés affichés après correction");
  await test("itsm", "error");
  check((await page.textContent("#res-itsm")).includes("401"), "mauvais mot de passe ServiceNow non signalé (401)");
  await shot("08c_tickets_itsm_refus");
  await page.fill('[data-bind="itsm_password"]', "Snow-Test-2026");
  await test("itsm", "ok");
  const ticket = await page.textContent("#res-itsm");
  check(/INC\d{7}/.test(ticket) && ticket.includes("résolution : OK"), `ticket de test incomplet : ${ticket}`);
  await shot("08d_tickets_itsm_test");
  // Le ticket existe bien dans l'outil (page des incidents du faux ServiceNow)
  const tool = await browser.newPage({ viewport: { width: 1280, height: 860 }, ignoreHTTPSErrors: true });
  await tool.goto(snowUrl);
  const toolText = await tool.textContent("body");
  check(toolText.includes("Test de connexion HeptagonesLM") && toolText.includes("Résolu"), "ticket absent ou non résolu dans l'outil ITSM");
  await tool.screenshot({ path: `${out}/setup_08e_servicenow_incident.png`, fullPage: true });
  await tool.close();

  // 9. Récapitulatif
  await next("Récapitulatif");
  await page.waitForSelector("pre.yaml", { state: "attached", timeout: 15000 });
  await page.waitForFunction(() => !document.getElementById("next").disabled, null, { timeout: 15000 })
    .catch(() => failures.push("bouton Installer désactivé au récapitulatif"));
  check((await page.textContent("#next")).includes("Installer"), "bouton Installer absent");
  await page.click("details summary");
  const recap = await page.textContent("#step-body");
  check(recap.includes("Chiffrés par Windows (DPAPI)") && recap.includes("réussi"), "récapitulatif ITSM incomplet");
  const yamlText = await page.textContent("pre.yaml");
  check(yamlText.includes("dpapi:C:/HeptagonesLM/secrets/itsm_password.dpapi"), "référence au secret chiffré absente du YAML");
  check(!yamlText.includes("Snow-Test-2026") && !(await page.content()).includes("Snow-Test-2026"), "mot de passe ITSM en clair dans le récapitulatif");
  await shot("09_recapitulatif");

  // 9. Installation : barre de progression jusqu'à 100 %
  await page.click("#next");
  await page.waitForFunction(() => document.getElementById("step-title").textContent === "Installation");
  await page.waitForFunction(() => { const p = document.getElementById("pct"); return p && parseInt(p.textContent) >= 25; }, null, { timeout: 30000 });
  await page.click("#log-details summary");
  await shot("10_installation");
  await page.waitForFunction(() => document.getElementById("step-title").textContent === "Vérification de l'installation", null, { timeout: 90000 })
    .catch(async () => failures.push(`installation non terminée : ${await page.textContent("#step-body")}`));

  // 10. Vérification finale
  await page.waitForSelector(".hero-result", { timeout: 30000 });
  const heading = await page.textContent(".hero-result h2");
  console.log("vérification ->", heading);
  check(heading.includes("opérationnel"), `vérification finale : ${heading}`);
  const verrors = await page.$$eval(".check-row .sev-critical", e => e.length);
  check(verrors === 0, `${verrors} contrôle(s) en erreur à la vérification`);
  // Authentification unique : test reçu d'un poste, SPN proposé puis déclaré
  const vtext = await page.textContent("#step-body");
  check(vtext.includes("Test depuis un poste utilisateur") && vtext.includes("PC-ETU-037"), "test d'accès depuis un poste absent de la vérification");
  check(vtext.includes("heptagoneslm.entreprise.local/acces"), "adresse du test d'accès absente");
  check(vtext.includes("Zone Intranet local"), "contrôle de la zone Intranet absent");
  check(vtext.includes("Secret chiffré itsm_password.dpapi") && vtext.includes("déchiffrable sur ce serveur"), "contrôle du secret ITSM absent");
  await shot("11_verification");
  const spnFix = await page.$('[data-vfix="register_spn"]');
  check(!!spnFix, "déclaration du SPN non proposée");
  if (spnFix) {
    await spnFix.click();
    await page.waitForFunction(() => { const r = document.getElementById("fix-result"); return r && /déclaré sur le compte/.test(r.textContent); },
      null, { timeout: 20000 }).catch(() => failures.push("déclaration du SPN sans résultat"));
    await page.waitForSelector(".hero-result");
    check(!(await page.$('[data-vfix="register_spn"]')), "SPN toujours signalé après sa déclaration");
    console.log("SPN ->", await page.textContent("#fix-result"));
  }
  const [dl] = await Promise.all([page.waitForEvent("download"), page.click('a[href="/api/report"]')]);
  const report = `${out}/${dl.suggestedFilename()}`;
  await dl.saveAs(report);
  const text = fs.readFileSync(report, "utf8");
  check(text.includes("Statut : done") && text.includes("Vérification"), "rapport d'installation incomplet");
  check(!/Secret-1/.test(text), "le mot de passe apparaît dans le rapport");

  // Mode sombre et affichage étroit
  await page.emulateMedia({ colorScheme: "dark" });
  await shot("12_verification_sombre");
  await page.setViewportSize({ width: 390, height: 900 });
  await page.waitForTimeout(200);
  check(!(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)), "débordement horizontal sur mobile");

  await browser.close();
  snow.kill();
  if (failures.length) { console.error("ÉCHECS :\n - " + failures.join("\n - ")); process.exit(1); }
  console.log("Assistant d'installation OK : parcours complet, vérification verte, aucune erreur JavaScript.");
})().catch(e => { console.error(e); process.exit(1); });
