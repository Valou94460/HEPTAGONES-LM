/* FreeLM — graphiques SVG sans dépendance (courbes, barres, carte de chaleur). */
"use strict";

const FreeLMCharts = (() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const NF_INT = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
  const NF_1 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  const NF_2 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });

  function fmt(value, type) {
    if (value === null || value === undefined || value === "") return "—";
    switch (type) {
      case "pct": return NF_1.format(value * 100) + " %";
      case "int": return NF_INT.format(value);
      case "float": return NF_2.format(value);
      case "hours": return NF_1.format(value) + " h";
      case "date": return formatDate(value);
      case "datetime": return formatDateTime(value);
      default: return String(value);
    }
  }
  function formatDate(v) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v);
    return m ? `${m[3]}/${m[2]}/${m[1]}` : v;
  }
  function formatDateTime(v) {
    const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(v);
    return m ? `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}` : formatDate(v);
  }
  function formatPeriod(v, xType) {
    if (xType === "month") {
      const [y, m] = v.split("-");
      return new Date(+y, +m - 1, 1).toLocaleDateString("fr-FR", { month: "short", year: "numeric" });
    }
    if (xType === "hour") return formatDateTime(v);
    if (xType === "week") return "sem. du " + formatDate(v);
    return formatDate(v);
  }
  function shortPeriod(v, xType) {
    if (xType === "month") return formatPeriod(v, xType);
    if (xType === "hour") { const d = formatDateTime(v); return d.slice(0, 5) + " " + d.slice(11, 13) + "h"; }
    return formatDate(v).slice(0, 5);
  }

  /* Couleur attachée à l'entité (jamais au rang) : une série garde sa couleur
     tant qu'elle reste affichée, même si les filtres en retirent d'autres. */
  const slotOf = new Map();
  function assignColors(names) {
    const used = new Set();
    const result = {};
    for (const n of names) {
      const s = slotOf.get(n);
      if (s !== undefined && !used.has(s)) { result[n] = s; used.add(s); }
    }
    for (const n of names) {
      if (result[n] !== undefined) continue;
      let s = 0;
      while (used.has(s) && s < 7) s++;
      result[n] = s; used.add(s); slotOf.set(n, s);
    }
    return Object.fromEntries(Object.entries(result).map(([n, s]) => [n, `var(--series-${s + 1})`]));
  }

  function el(name, attrs = {}, parent) {
    const node = document.createElementNS(SVG_NS, name);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  /* Graduations « rondes » : pas de 1, 2 ou 5 × 10^n, environ 4 intervalles. */
  function niceStep(max, format) {
    if (max <= 0) return format === "pct" ? 0.25 : 1;
    const raw = max / 4;
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    let step = [1, 2, 5, 10].map(m => m * pow).find(s => s >= raw);
    if (format === "int" || format === "hours" || format === "num") step = Math.max(1, Math.round(step));
    return step;
  }
  function niceMax(max, format) {
    const step = niceStep(max, format);
    return Math.max(step, Math.ceil(max / step - 1e-9) * step);
  }
  function ticks(max, format) {
    const step = niceStep(max, format);
    const out = [];
    for (let v = 0; v <= max + step / 1000; v += step) out.push(+v.toFixed(10));
    return out;
  }
  function axisLabel(v, format) {
    if (format === "pct") return new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 }).format(v * 100) + " %";
    if (format === "int") return NF_INT.format(Math.round(v));
    return NF_2.format(v);
  }

  /* -- Infobulle --------------------------------------------------------- */
  const tip = () => document.getElementById("tooltip");
  function showTip(html, x, y) {
    const t = tip();
    t.innerHTML = html;
    t.hidden = false;
    const r = t.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
    t.style.left = Math.max(8, left) + "px";
    t.style.top = Math.max(8, top) + "px";
  }
  function hideTip() { tip().hidden = true; }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function renderLegend(container, entries) {
    container.innerHTML = "";
    if (entries.length < 2) return;
    for (const [name, color] of entries) {
      const s = document.createElement("span");
      s.innerHTML = `<i style="background:${color}"></i>${esc(name)}`;
      container.appendChild(s);
    }
  }

  /* -- Courbes ------------------------------------------------------------ */
  function line(container, legend, spec) {
    container.innerHTML = "";
    const names = spec.series.map(s => s.name);
    const colors = assignColors(names);
    renderLegend(legend, names.map(n => [n, colors[n]]));

    const direct = spec.series.length <= 4 && spec.series.length > 1;
    const W = Math.max(container.clientWidth, 320), H = 320;
    const m = { l: 56, r: direct ? 150 : 20, t: 12, b: 34 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const n = spec.x.length;
    const max = niceMax(Math.max(0, ...spec.series.flatMap(s => s.values.filter(v => v !== null))), spec.format);
    const X = i => m.l + (n === 1 ? iw / 2 : (iw * i) / (n - 1));
    const Y = v => m.t + ih - (ih * v) / max;

    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": spec.title }, container);
    const grid = el("g", { class: "grid" }, svg);
    for (const t of ticks(max, spec.format)) {
      el("line", { x1: m.l, x2: m.l + iw, y1: Y(t), y2: Y(t) }, grid);
      el("text", { x: m.l - 8, y: Y(t) + 4, "text-anchor": "end", class: "tick" }, svg).textContent = axisLabel(t, spec.format);
    }
    el("line", { x1: m.l, x2: m.l + iw, y1: Y(0), y2: Y(0), class: "baseline" }, svg);
    const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 80))));
    spec.x.forEach((x, i) => {
      if (i % every !== 0 && i !== n - 1) return;
      if (i === n - 1 && i % every !== 0 && (n - 1) % every < every / 2) return;
      el("text", { x: X(i), y: H - 12, "text-anchor": "middle", class: "tick" }, svg).textContent = shortPeriod(x, spec.xType);
    });

    const ends = [];
    for (const s of spec.series) {
      let d = "", pen = false;
      s.values.forEach((v, i) => {
        if (v === null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`;
        pen = true;
      });
      el("path", { d, fill: "none", stroke: colors[s.name], "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
      if (n === 1 || s.values.filter(v => v !== null).length === 1) {
        s.values.forEach((v, i) => { if (v !== null) el("circle", { cx: X(i), cy: Y(v), r: 4, fill: colors[s.name] }, svg); });
      }
      const lastIdx = s.values.map((v, i) => (v === null ? -1 : i)).filter(i => i >= 0).pop();
      if (direct && lastIdx !== undefined) ends.push({ name: s.name, y: Y(s.values[lastIdx]), color: colors[s.name] });
    }
    // Étiquettes directes (<= 4 séries) écartées pour éviter les chevauchements.
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
    for (const e of ends) {
      const t = el("text", { x: m.l + iw + 10, y: e.y + 4, class: "end-label" }, svg);
      t.textContent = e.name.length > 22 ? e.name.slice(0, 21) + "…" : e.name;
    }

    // Couche de survol : réticule + infobulle multi-séries.
    const cross = el("line", { y1: m.t, y2: m.t + ih, class: "crosshair", visibility: "hidden" }, svg);
    const dots = spec.series.map(s => el("circle", { r: 4, fill: colors[s.name], stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }, svg));
    const hit = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" }, svg);
    hit.addEventListener("mousemove", ev => {
      const box = svg.getBoundingClientRect();
      const px = ((ev.clientX - box.left) / box.width) * W;
      const i = n === 1 ? 0 : Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / iw) * (n - 1))));
      cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible");
      const rows = [];
      spec.series.forEach((s, k) => {
        const v = s.values[i];
        if (v === null) { dots[k].setAttribute("visibility", "hidden"); return; }
        dots[k].setAttribute("cx", X(i)); dots[k].setAttribute("cy", Y(v)); dots[k].setAttribute("visibility", "visible");
        rows.push({ name: s.name, v, color: colors[s.name] });
      });
      rows.sort((a, b) => b.v - a.v);
      showTip(`<div class="t-title">${esc(formatPeriod(spec.x[i], spec.xType))}</div>` +
        rows.map(r => `<div class="t-row"><span><i style="background:${r.color}"></i>${esc(r.name)}</span><b>${fmt(r.v, spec.format === "int" ? "int" : spec.format)}</b></div>`).join(""),
        ev.clientX, ev.clientY);
    });
    hit.addEventListener("mouseleave", () => {
      hideTip(); cross.setAttribute("visibility", "hidden");
      dots.forEach(d => d.setAttribute("visibility", "hidden"));
    });
  }

  /* -- Barres horizontales (une série) ------------------------------------ */
  function bar(container, legend, spec) {
    container.innerHTML = "";
    legend.innerHTML = "";
    const items = spec.items;
    const W = Math.max(container.clientWidth, 320);
    const row = 28, bh = 16;
    const labelW = Math.min(240, Math.max(90, W * 0.3));
    const m = { l: labelW + 12, r: 70, t: 4, b: 24 };
    const iw = W - m.l - m.r;
    const H = m.t + items.length * row + m.b;
    const max = niceMax(Math.max(0, ...items.map(d => d.value || 0)), spec.format === "pct" ? "pct" : "num");
    const tickFormat = spec.format === "pct" ? "pct" : "num";
    const Xv = v => (iw * v) / max;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": spec.title }, container);
    const grid = el("g", { class: "grid" }, svg);
    for (const t of ticks(max, tickFormat)) {
      el("line", { x1: m.l + Xv(t), x2: m.l + Xv(t), y1: m.t, y2: H - m.b }, grid);
      el("text", { x: m.l + Xv(t), y: H - 8, "text-anchor": "middle", class: "tick" }, svg).textContent =
        spec.format === "hours" ? NF_INT.format(t) + " h" : axisLabel(t, spec.format === "pct" ? "pct" : "int");
    }
    items.forEach((d, i) => {
      const y = m.t + i * row + (row - bh) / 2;
      const label = el("text", { x: m.l - 10, y: y + bh / 2 + 4, "text-anchor": "end", class: "bar-label" }, svg);
      const maxChars = Math.floor(labelW / 7);
      label.textContent = d.label.length > maxChars ? d.label.slice(0, maxChars - 1) + "…" : d.label;
      const w = Math.max(0, Xv(d.value || 0));
      const r = Math.min(4, w / 2);
      // Barre ancrée à la ligne de base, extrémité arrondie (4 px) côté valeur.
      const path = w > 0
        ? `M${m.l},${y}H${m.l + w - r}Q${m.l + w},${y} ${m.l + w},${y + r}V${y + bh - r}Q${m.l + w},${y + bh} ${m.l + w - r},${y + bh}H${m.l}Z`
        : "";
      const b = el("path", { d: path, fill: "var(--series-1)" }, svg);
      el("text", { x: m.l + w + 6, y: y + bh / 2 + 4, class: "value-label" }, svg).textContent = fmt(d.value, spec.format);
      const hit = el("rect", { x: 0, y: m.t + i * row, width: W, height: row, fill: "transparent" }, svg);
      hit.addEventListener("mousemove", ev => {
        b.setAttribute("opacity", "0.8");
        showTip(`<div class="t-title">${esc(d.label)}</div><div class="t-row"><span>${esc(spec.title)}</span><b>${fmt(d.value, spec.format)}</b></div>`, ev.clientX, ev.clientY);
      });
      hit.addEventListener("mouseleave", () => { b.removeAttribute("opacity"); hideTip(); });
    });
    el("line", { x1: m.l, x2: m.l, y1: m.t, y2: H - m.b, class: "baseline" }, svg);
  }

  /* -- Carte de chaleur (rampe séquentielle bleue) ------------------------- */
  const SEQ = 9;
  function seqColor(v) {
    const step = Math.min(SEQ, Math.max(1, Math.ceil(v * SEQ) || 1));
    return { bg: `var(--seq-${step})`, dark: step >= 5 };
  }
  function heatmap(container, legend, spec) {
    container.innerHTML = "";
    legend.innerHTML = "";
    const g = document.createElement("div");
    g.className = "heatmap";
    g.style.gridTemplateColumns = `48px repeat(${spec.cols.length}, minmax(28px, 1fr))`;
    g.appendChild(document.createElement("div"));
    for (const c of spec.cols) {
      const h = document.createElement("div");
      h.className = "h"; h.textContent = c.slice(0, 3);
      g.appendChild(h);
    }
    spec.rows.forEach((r, i) => {
      const lab = document.createElement("div");
      lab.className = "r"; lab.textContent = r;
      g.appendChild(lab);
      spec.cols.forEach((c, j) => {
        const v = spec.values[i][j];
        const cell = document.createElement("div");
        cell.className = "c" + (v === null ? " null" : "");
        // Quasi nul : fond neutre, pour distinguer « libre » de « peu utilisé ».
        if (v !== null) cell.style.background = v < 0.005 ? "var(--surface-2)" : seqColor(v).bg;
        cell.addEventListener("mousemove", ev => showTip(
          `<div class="t-title">${esc(c)} ${esc(r)}</div><div class="t-row"><span>Taux moyen</span><b>${v === null ? "aucune donnée" : fmt(v, "pct")}</b></div>`,
          ev.clientX, ev.clientY));
        cell.addEventListener("mouseleave", hideTip);
        g.appendChild(cell);
      });
    });
    container.appendChild(g);
    const leg = document.createElement("div");
    leg.className = "heat-legend";
    leg.innerHTML = "<span>0 %</span><span class='ramp'>" +
      Array.from({ length: SEQ }, (_, i) => `<i style="background:var(--seq-${i + 1})"></i>`).join("") +
      "</span><span>100 %</span><span style='margin-left:12px'><i style='display:inline-block;width:18px;height:10px;border-radius:2px;background:var(--empty);vertical-align:middle'></i> aucune donnée</span>";
    container.appendChild(leg);
  }

  return { fmt, line, bar, heatmap, showTip, hideTip, esc, seqColor };
})();
