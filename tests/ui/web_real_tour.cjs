/*
 * Visite enregistrée de l'interface installée (CI Windows) : https://<PUBLIC_HOST>/ servi par IIS,
 * connexion Windows (NTLM) d'un utilisateur du groupe autorisé, données collectées par la tâche
 * planifiée du compte de service.
 *   node tests/ui/web_real_tour.cjs <dossier>      (variables : PUBLIC_HOST, TOUR_USER, TOUR_PASSWORD ou SVC_PWD)
 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("fs");
const path = require("path");

(async () => {
  const out = process.argv[2] || "installation-reelle";
  fs.mkdirSync(out, { recursive: true });
  const base = `https://${process.env.PUBLIC_HOST}`;
  const failures = [];
  const browser = await chromium.launch({ slowMo: 150 });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 }, locale: "fr-FR", ignoreHTTPSErrors: true,   // certificat auto-signé de la CI
    httpCredentials: { username: process.env.TOUR_USER, password: process.env.TOUR_PASSWORD || process.env.SVC_PWD },
    recordVideo: { dir: out, size: { width: 1440, height: 900 } },
  });
  const page = await context.newPage();
  page.on("pageerror", e => failures.push(`erreur JS : ${e.message}`));
  const shot = name => page.screenshot({ path: path.join(out, `interface_${name}.png`), fullPage: true });
  try {
    const me = await (await page.goto(`${base}/api/me`)).json();
    console.log("Connecté via IIS :", JSON.stringify(me));
    const short = process.env.TOUR_USER.split("\\").pop().toLowerCase();       // DOMAINE\compte -> compte
    if (me.user !== short || me.auth !== "header") failures.push(`authentification unique : ${JSON.stringify(me)}`);
    await page.goto(`${base}/#preset=all`);
    await page.waitForSelector("#kpi-grid .kpi-card", { timeout: 60000 });
    await page.waitForTimeout(1500);
    await shot("tableau_de_bord");
    await page.click('.nav-item[data-type="live"]');
    await page.waitForSelector("#live-grid .live-card", { timeout: 30000 });
    await page.waitForTimeout(1500);
    await shot("en_direct");
    const live = await page.textContent("#live-grid");
    if (!live.trim()) failures.push("vue En direct vide après la collecte");
    await page.goto(`${base}/#type=sessions&preset=all`);
    await page.waitForFunction(() => document.getElementById("table-title").textContent !== "Chargement…", null, { timeout: 30000 });
    await page.waitForTimeout(1500);
    await shot("sessions");
  } catch (e) {
    failures.push(`visite interrompue : ${e.message}`);
    await shot("arret").catch(() => {});
  } finally {
    await context.close();
    await browser.close();
    const before = new Set(["installation_assistant.webm"]);
    const video = fs.readdirSync(out).find(f => f.endsWith(".webm") && !before.has(f));
    if (video) fs.renameSync(path.join(out, video), path.join(out, "interface_installee.webm"));
  }
  if (failures.length) { failures.forEach(f => console.error(" -", f)); process.exit(1); }
  console.log("Interface installée : OK (tableau de bord, En direct, sessions)");
})();
