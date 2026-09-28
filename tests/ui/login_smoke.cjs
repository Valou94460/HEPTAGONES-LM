/*
 * Parcours de connexion dans un vrai navigateur (serveur tests/ui/fake_auth_server.py).
 *   node tests/ui/login_smoke.cjs http://localhost:8766 captures/
 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("fs");

(async () => {
  const base = (process.argv[2] || "http://localhost:8766").replace(/\/$/, "");
  const out = process.argv[3] || "ui-captures";
  fs.mkdirSync(out, { recursive: true });
  const failures = [];
  const check = (cond, msg) => { if (!cond) failures.push(msg); };
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, locale: "fr-FR" });
  page.on("pageerror", e => failures.push("erreur JS : " + e.message));

  // 1. Accès direct à un rapport -> redirection vers la connexion
  await page.goto(`${base}/#type=heatmap&preset=7d`);
  await page.waitForURL(/login\.html/);
  check(/login\.html(#.*)?$/.test(page.url()), "pas de redirection vers login.html");
  await page.screenshot({ path: `${out}/login.png` });

  // 2. Mauvais mot de passe -> message
  await page.fill("#username", "jdupont");
  await page.fill("#password", "mauvais");
  await page.click("#login-submit");
  await page.waitForSelector("#login-error:not([hidden])");
  const err = await page.textContent("#login-error");
  console.log("mauvais mot de passe ->", err);
  check(err.includes("incorrect"), "message d'erreur absent");
  await page.screenshot({ path: `${out}/login_erreur.png` });

  // 3. Bon mot de passe -> retour sur le rapport demandé, nom affiché
  await page.fill("#username", "DOMAINE\\jdupont");
  await page.fill("#password", "Secret-1");
  await page.click("#login-submit");
  await page.waitForFunction(() => document.getElementById("table-title") &&
    document.getElementById("table-title").textContent === "Profil horaire", null, { timeout: 15000 })
    .catch(() => failures.push("retour au rapport demandé (Profil horaire) non effectué"));
  const user = await page.textContent("#user-name").catch(() => "");
  console.log("connecté ->", user, "| onglet :", await page.textContent("#table-title").catch(() => "?"));
  check(user === "Jean Dupont", "nom de l'utilisateur non affiché");
  await page.screenshot({ path: `${out}/connecte.png` });

  // 4. Export autorisé une fois connecté
  const [dl] = await Promise.all([page.waitForEvent("download"), page.click("[data-export=csv]")]);
  console.log("export ->", dl.suggestedFilename());

  // 5. Test d'accès depuis ce poste (/acces) : utilisateur reconnu, résultat enregistré
  await page.goto(base + "/acces");
  await page.waitForSelector("#access-status.ok", { timeout: 10000 }).catch(() => failures.push("page /acces : accès non confirmé"));
  console.log("test d'accès ->", (await page.textContent("#access-status")).trim());
  check((await page.textContent("#access-details")).includes("jdupont"), "page /acces : identifiant absent");
  await page.check('input[name="prompted"][value="no"]');
  await page.click("#access-submit");
  await page.waitForSelector("#access-result.ok", { timeout: 10000 }).catch(() => failures.push("page /acces : résultat non enregistré"));
  await page.screenshot({ path: `${out}/test_acces.png` });
  await page.goto(base + "/");
  await page.waitForSelector("#logout:not([hidden])", { timeout: 10000 }).catch(() => {});

  // 6. Déconnexion -> retour à la connexion, API refusée
  await page.click("#logout");
  await page.waitForURL(/login\.html/);
  const status = await page.evaluate(async () => (await fetch("api/meta")).status);
  console.log("après déconnexion, /api/meta ->", status);
  check(status === 401, "API accessible après déconnexion");

  await browser.close();
  if (failures.length) { console.error("ÉCHECS :\n - " + failures.join("\n - ")); process.exit(1); }
  console.log("Connexion OK : redirection, erreur, connexion, retour au rapport, export, déconnexion.");
})().catch(e => { console.error(e); process.exit(1); });
