/*
 * Mise à jour en place dans un vrai navigateur (assistant simulé, installation 26.3.0 existante) :
 *   python tests/ui/update_harness.py 8793 &  puis  node tests/ui/setup_update.cjs captures/
 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
(async () => {
  const out = process.argv[2]; const fails = [];
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: 1.25, locale: "fr-FR" });
  p.on("pageerror", e => fails.push("JS " + e.message));
  p.on("console", m => { if (m.type() === "error") fails.push("console " + m.text()); });
  await p.goto("http://127.0.0.1:8793/?token=upd");
  await p.waitForSelector("#go-update", { timeout: 20000 }).catch(async e => { await p.screenshot({ path: `${out}/maj_err.png` }); console.log("ERR", fails.join(" | "), await p.textContent("#step-title")); process.exit(1); });
  const txt = await p.textContent(".installed-panel");
  console.log("accueil :", txt.replace(/\s+/g, " ").trim().slice(0, 220));
  await p.screenshot({ path: `${out}/maj_01_accueil.png` });
  await p.click("#go-update");
  await p.waitForFunction(() => document.getElementById("step-title").textContent === "Mise à jour", null, { timeout: 10000 });
  await p.waitForTimeout(700);
  await p.screenshot({ path: `${out}/maj_02_progression.png` });
  await p.waitForFunction(() => document.getElementById("step-title").textContent.includes("Vérification"), null, { timeout: 60000 })
    .catch(() => fails.push("vérification non atteinte"));
  await p.waitForSelector(".hero-result", { timeout: 30000 }).catch(() => fails.push("résultat de vérification absent"));
  await p.screenshot({ path: `${out}/maj_03_verification.png` });
  console.log("vérification :", (await p.textContent("#step-body")).replace(/\s+/g, " ").slice(0, 160));
  const inf = await p.evaluate(async () => (await (await fetch("/api/info")).json()));
  console.log("version installée après :", inf.installed_version, "/ assistant :", inf.version);
  if (inf.installed_version !== inf.version) fails.push("version non enregistrée après la mise à jour");
  // 26.3.3 : licences Autodesk ajoutées à l'installation existante depuis l'accueil
  await p.click('#stepper button[data-step="0"]');
  await p.waitForSelector("#go-autodesk", { timeout: 10000 }).catch(() => fails.push("bouton « Configurer les licences Autodesk » absent"));
  await p.click("#go-autodesk");
  await p.waitForFunction(() => document.getElementById("step-title").textContent.includes("Autodesk"), null, { timeout: 10000 })
    .catch(() => fails.push("formulaire Autodesk non affiché"));
  if (!(await p.isChecked('[data-bind="autodesk_enabled"]'))) fails.push("suivi Autodesk non coché par défaut");
  if ((await p.textContent("#next")) !== "Enregistrer la configuration Autodesk") fails.push("bouton d'enregistrement Autodesk");
  await p.screenshot({ path: `${out}/autodesk_01_formulaire.png` });
  await p.click("#next");
  await p.waitForFunction(() => document.getElementById("step-title").textContent === "Configuration Autodesk", null, { timeout: 10000 })
    .catch(() => fails.push("progression de la configuration Autodesk absente"));
  await p.waitForFunction(() => document.getElementById("step-title").textContent.includes("Vérification"), null, { timeout: 60000 })
    .catch(() => fails.push("vérification non atteinte après la configuration Autodesk"));
  await p.waitForSelector(".hero-result", { timeout: 30000 }).catch(() => {});
  await p.screenshot({ path: `${out}/autodesk_02_verification.png` });
  const ctx = await p.evaluate(async () => (await (await fetch("/api/autodesk/context", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-HeptagonesLM-Setup": "1" }, body: "{}" })).json()));
  if (!ctx.answers || !ctx.answers.autodesk_enabled) fails.push("configuration Autodesk non enregistrée");
  if (!(await p.$("[data-quit]"))) fails.push("bouton « Quitter l'assistant » absent");
  console.log(fails.length ? "ÉCHECS : " + fails.join(" | ") : "Mise à jour et configuration Autodesk (navigateur) OK");
  await b.close(); process.exit(fails.length ? 1 : 0);
})();
