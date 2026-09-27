/*
 * Test de bout en bout de l'interface web dans un vrai navigateur (Playwright).
 *   node tests/ui/ui_smoke.cjs http://localhost:8765 captures/
 * Échoue (code 1) si un onglet est en erreur ou vide, ou en cas d'erreur JavaScript.
 * Module Playwright : PLAYWRIGHT_MODULE (chemin) ou "playwright" installé localement.
 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("fs");

(async () => {
  const base = (process.argv[2] || "http://localhost:8765").replace(/\/$/, "");
  const out = process.argv[3] || "ui-captures";
  fs.mkdirSync(out, { recursive: true });
  const failures = [];
  const browser = await chromium.launch();
  for (const scheme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, colorScheme: scheme, locale: "fr-FR" });
    page.on("console", m => { if (m.type() === "error") failures.push(`[${scheme}] console: ${m.text()}`); });
    page.on("pageerror", e => failures.push(`[${scheme}] erreur JS : ${e.message}`));
    const tabs = scheme === "light"
      ? ["summary", "timeseries", "breakdown", "matrix", "heatmap", "contention", "denials", "sessions", "collections"]
      : ["summary", "timeseries", "heatmap"];
    for (const t of tabs) {
      const extra = { timeseries: "&metric=utilization&granularity=day", breakdown: "&dimension=department", matrix: "&dimension=department" }[t] || "";
      await page.goto(`${base}/#type=${t}&preset=all${extra}`);
      await page.waitForFunction(() => document.getElementById("table-title").textContent !== "Chargement…", null, { timeout: 30000 });
      await page.waitForTimeout(250);
      const info = await page.evaluate(() => ({
        title: document.getElementById("table-title").textContent,
        rows: document.querySelectorAll("#table tbody tr:not(:has(.empty))").length,
        chart: !document.getElementById("chart-card").hidden,
        error: document.getElementById("error").hidden ? "" : document.getElementById("error").textContent,
      }));
      console.log(scheme.padEnd(5), t.padEnd(12), JSON.stringify(info));
      if (info.error) failures.push(`${t} : ${info.error}`);
      if (!info.rows) failures.push(`${t} : tableau vide`);
      await page.screenshot({ path: `${out}/${scheme}_${t}.png`, fullPage: t !== "sessions" && t !== "collections" });
    }
    if (scheme === "light") {
      await page.goto(`${base}/#type=timeseries&preset=all`);
      await page.waitForSelector("#chart svg");
      await page.click("#ms-product .ms-button");
      const boxes = await page.$$(".ms-panel .ms-list input");
      for (const b of boxes.slice(0, 2)) await b.check();
      await page.keyboard.press("Escape");
      await page.waitForTimeout(800);
      const series = await page.$$eval("#legend span", s => s.length);
      console.log("filtre 2 produits ->", series, "séries");
      if (series !== 2) failures.push(`filtre produits : ${series} séries au lieu de 2`);
      const hit = await page.$("#chart svg rect[fill=transparent]");
      const bb = await hit.boundingBox();
      await page.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2);
      const tip = await page.$eval("#tooltip", t => !t.hidden);
      if (!tip) failures.push("infobulle absente au survol");
      const formats = process.env.UI_SKIP_XLSX ? ["csv", "json"] : ["csv", "json", "xlsx", "full"];
      for (const fmt of formats) {
        const [dl] = await Promise.all([page.waitForEvent("download"), page.click(`[data-export=${fmt}]`)]);
        const path = `${out}/${dl.suggestedFilename()}`;
        await dl.saveAs(path);
        const size = fs.statSync(path).size;
        console.log(`export ${fmt} -> ${dl.suggestedFilename()} (${size} octets)`);
        if (size < 100) failures.push(`export ${fmt} vide`);
      }
      await page.setViewportSize({ width: 390, height: 900 });
      await page.goto(`${base}/#type=summary`);
      await page.waitForSelector(".kpi");
      if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)) failures.push("débordement horizontal sur mobile");
      await page.screenshot({ path: `${out}/mobile_summary.png` });
    }
    await page.close();
  }
  await browser.close();
  if (failures.length) { console.error("ÉCHECS :\n - " + failures.join("\n - ")); process.exit(1); }
  console.log("Interface OK : aucun échec, aucune erreur JavaScript.");
})().catch(e => { console.error(e); process.exit(1); });
