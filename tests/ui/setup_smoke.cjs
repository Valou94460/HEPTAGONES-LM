/*
 * Parcours complet de l'assistant d'installation (mode simulation) dans un vrai navigateur.
 *   heptagoneslm setup --simulate --no-open --port 8790 --token test-token &
 *   node tests/ui/setup_smoke.cjs http://127.0.0.1:8790 test-token captures/
 * Échoue (code 1) si une étape bloque, si la vérification finale n'est pas verte,
 * ou en cas d'erreur JavaScript.
 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("fs");

(async () => {
  const base = (process.argv[2] || "http://127.0.0.1:8790").replace(/\/$/, "");
  const token = process.argv[3] || "test-token";
  const out = process.argv[4] || "ui-captures";
  fs.mkdirSync(out, { recursive: true });
  const failures = [];
  const check = (cond, msg) => { if (!cond) failures.push(msg); };
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

  // 8. Récapitulatif
  await next("Récapitulatif");
  await page.waitForSelector("pre.yaml", { state: "attached", timeout: 15000 });
  await page.waitForFunction(() => !document.getElementById("next").disabled, null, { timeout: 15000 })
    .catch(() => failures.push("bouton Installer désactivé au récapitulatif"));
  check((await page.textContent("#next")).includes("Installer"), "bouton Installer absent");
  await page.click("details summary");
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
  await shot("11_verification");
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
  if (failures.length) { console.error("ÉCHECS :\n - " + failures.join("\n - ")); process.exit(1); }
  console.log("Assistant d'installation OK : parcours complet, vérification verte, aucune erreur JavaScript.");
})().catch(e => { console.error(e); process.exit(1); });
