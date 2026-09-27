/* FreeLM — graphiques SVG sans dépendance : courbes et aires, barres, colonnes,
   carte de chaleur, jauge circulaire et micro-courbes (sparklines). */
"use strict";

const FreeLMCharts = (() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const NF_INT = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
  const NF_1 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  const NF_2 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });
  const NF_COMPACT = new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 1 });
  let currency = "€";

  function setCurrency(c) { if (c) currency = c; }
  function fmt(value, type) {
    if (value === null || value === undefined || value === "") return "—";
    switch (type) {
      case "pct": return NF_1.format(value * 100) + " %";
      case "int": return NF_INT.format(value);
      case "float": return NF_2.format(value);
      case "hours": return NF_1.format(value) + " h";
      case "money": return NF_INT.format(value) + " " + currency;
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
    if (xType === "tokens") return `Au moins ${v} jeton${v > 1 ? "s" : ""}`;
    if (xType === "share") return `Au moins ${Math.round(v * 100)} % du parc`;
    if (xType === "month") {
      const [y, m] = v.split("-");
      return new Date(+y, +m - 1, 1).toLocaleDateString("fr-FR", { month: "short", year: "numeric" });
    }
    if (xType === "hour") return formatDateTime(v);
    if (xType === "week") return "sem. du " + formatDate(v);
    if (xType === "day") {
      const d = new Date(v.slice(0, 10) + "T00:00");
      return isNaN(d) ? formatDate(v) : d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
    }
    return formatDate(v);
  }
  function shortPeriod(v, xType) {
    if (xType === "tokens") return String(v);
    if (xType === "share") return Math.round(v * 100) + " %";
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
  let gradientId = 0;
  function gradient(svg, color, opacity = 0.28) {
    const id = "fl-grad-" + (++gradientId);
    const g = el("linearGradient", { id, x1: 0, y1: 0, x2: 0, y2: 1 }, el("defs", {}, svg));
    el("stop", { offset: "0%", "stop-color": color, "stop-opacity": opacity }, g);
    el("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0 }, g);
    return `url(#${id})`;
  }

  /* Graduations « rondes » : pas de 1, 2 ou 5 × 10^n, environ 4 intervalles. */
  function niceStep(max, format, count = 4) {
    if (max <= 0) return format === "pct" ? 0.25 : 1;
    const raw = max / count;
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    let step = [1, 2, 5, 10].map(m => m * pow).find(s => s >= raw);
    if (format !== "pct" && format !== "float") step = Math.max(1, Math.round(step));
    return step;
  }
  function niceMax(max, format, count) {
    const step = niceStep(max, format, count);
    return Math.max(step, Math.ceil(max / step - 1e-9) * step);
  }
  function ticks(max, format, count) {
    const step = niceStep(max, format, count);
    const out = [];
    for (let v = 0; v <= max + step / 1000; v += step) out.push(+v.toFixed(10));
    return out;
  }
  function axisLabel(v, format) {
    if (format === "pct") return new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 }).format(v * 100) + " %";
    if (format === "money") return (v >= 10000 ? NF_COMPACT.format(v) : NF_INT.format(v)) + " " + currency;
    if (format === "hours") return NF_INT.format(v) + " h";
    if (format === "int") return v >= 10000 ? NF_COMPACT.format(v) : NF_INT.format(Math.round(v));
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
    if (!container) return;
    container.innerHTML = "";
    if (entries.length < 2) return;
    for (const [name, color, dash] of entries) {
      const s = document.createElement("span");
      s.innerHTML = `<i class="${dash ? "dash" : ""}" style="background:${color};color:${color}"></i>${esc(name)}`;
      container.appendChild(s);
    }
  }

  /* -- Courbes et aires ---------------------------------------------------
     spec : {x, xType, format, title, series: [{name, values, color?, area?, dash?}],
             height?, direct? (étiquettes en bout de courbe, défaut : 2 à 4 séries)} */
  function line(container, legend, spec) {
    container.innerHTML = "";
    const names = spec.series.map(s => s.name);
    const auto = assignColors(names.filter((n, i) => !spec.series[i].color));
    const colors = Object.fromEntries(spec.series.map(s => [s.name, s.color || auto[s.name]]));
    renderLegend(legend, spec.series.map(s => [s.name, colors[s.name], s.dash]));

    const direct = spec.direct !== undefined ? spec.direct : (spec.series.length <= 4 && spec.series.length > 1);
    const W = Math.max(container.clientWidth, 280), H = spec.height || 320;
    const m = { l: 52, r: direct ? 150 : 16, t: 12, b: 30 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const n = spec.x.length;
    const top = Math.max(0, ...spec.series.flatMap(s => s.values.filter(v => v !== null)));
    const max = niceMax(spec.format === "pct" ? Math.min(Math.max(top, 0.05), 1) : top, spec.format);
    const X = i => m.l + (n === 1 ? iw / 2 : (iw * i) / (n - 1));
    const Y = v => m.t + ih - (ih * Math.min(v, max)) / max;

    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": spec.title }, container);
    const grid = el("g", { class: "grid" }, svg);
    for (const t of ticks(max, spec.format)) {
      el("line", { x1: m.l, x2: m.l + iw, y1: Y(t), y2: Y(t) }, grid);
      el("text", { x: m.l - 8, y: Y(t) + 4, "text-anchor": "end", class: "tick" }, svg).textContent = axisLabel(t, spec.format);
    }
    el("line", { x1: m.l, x2: m.l + iw, y1: Y(0), y2: Y(0), class: "baseline" }, svg);
    const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 76))));
    spec.x.forEach((x, i) => {
      if (i % every !== 0 && i !== n - 1) return;
      if (i === n - 1 && i % every !== 0 && (n - 1) % every < every / 2) return;
      el("text", { x: X(i), y: H - 10, "text-anchor": "middle", class: "tick" }, svg).textContent = shortPeriod(x, spec.xType);
    });

    const ends = [];
    for (const s of spec.series) {
      const color = colors[s.name];
      // Segments continus (une valeur nulle interrompt la courbe).
      const segments = [];
      let seg = [];
      s.values.forEach((v, i) => {
        if (v === null || v === undefined) { if (seg.length) segments.push(seg); seg = []; return; }
        seg.push([X(i), Y(v)]);
      });
      if (seg.length) segments.push(seg);
      if (s.area) {
        const fill = gradient(svg, color);
        for (const pts of segments) {
          if (pts.length < 2) continue;
          const d = `M${pts[0][0].toFixed(1)},${Y(0)}` + pts.map(p => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("") +
            `L${pts[pts.length - 1][0].toFixed(1)},${Y(0)}Z`;
          el("path", { d, fill, stroke: "none" }, svg);
        }
      }
      const d = segments.map(pts => pts.map((p, k) => `${k ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("")).join("");
      el("path", {
        d, fill: "none", stroke: color, "stroke-width": s.dash ? 1.6 : 2, "stroke-linejoin": "round", "stroke-linecap": "round",
        ...(s.dash ? { "stroke-dasharray": "5 4" } : {}),
      }, svg);
      if (segments.some(pts => pts.length === 1)) {
        segments.filter(pts => pts.length === 1).forEach(pts => el("circle", { cx: pts[0][0], cy: pts[0][1], r: 3.5, fill: color }, svg));
      }
      const lastIdx = s.values.map((v, i) => (v === null || v === undefined ? -1 : i)).filter(i => i >= 0).pop();
      if (direct && lastIdx !== undefined) ends.push({ name: s.name, y: Y(s.values[lastIdx]) });
    }
    // Étiquettes directes écartées pour éviter les chevauchements.
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
        if (v === null || v === undefined) { dots[k].setAttribute("visibility", "hidden"); return; }
        dots[k].setAttribute("cx", X(i)); dots[k].setAttribute("cy", Y(v)); dots[k].setAttribute("visibility", "visible");
        rows.push({ name: s.name, v, color: colors[s.name] });
      });
      if (spec.series.length > 2) rows.sort((a, b) => b.v - a.v);
      showTip(`<div class="t-title">${esc(formatPeriod(spec.x[i], spec.xType))}</div>` +
        rows.map(r => `<div class="t-row"><span><i style="background:${r.color}"></i>${esc(r.name)}</span><b>${fmt(r.v, spec.format)}</b></div>`).join(""),
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
    if (legend) legend.innerHTML = "";
    const items = spec.items;
    const W = Math.max(container.clientWidth, 280);
    const row = 30, bh = 16;
    const labelW = Math.min(240, Math.max(90, W * 0.3));
    const m = { l: labelW + 12, r: 84, t: 4, b: 24 };
    const iw = W - m.l - m.r;
    const H = m.t + items.length * row + m.b;
    const tickFormat = spec.format === "pct" ? "pct" : spec.format;
    const max = niceMax(Math.max(0, ...items.map(d => d.value || 0)), tickFormat);
    const Xv = v => (iw * v) / max;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": spec.title }, container);
    const grid = el("g", { class: "grid" }, svg);
    for (const t of ticks(max, tickFormat)) {
      el("line", { x1: m.l + Xv(t), x2: m.l + Xv(t), y1: m.t, y2: H - m.b }, grid);
      el("text", { x: m.l + Xv(t), y: H - 8, "text-anchor": "middle", class: "tick" }, svg).textContent = axisLabel(t, tickFormat);
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
      const b = el("path", { d: path, fill: spec.color || "var(--series-1)" }, svg);
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

  /* -- Colonnes verticales (une série temporelle) --------------------------
     spec : {x, values, xType, format, title, color?, height?} */
  function columns(container, spec) {
    container.innerHTML = "";
    const W = Math.max(container.clientWidth, 240), H = spec.height || 180;
    const m = { l: 34, r: 8, t: 10, b: 24 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const n = spec.values.length;
    const max = niceMax(Math.max(1, ...spec.values.map(v => v || 0)), spec.format, 3);
    const slot = iw / Math.max(n, 1);
    const bw = Math.max(2, Math.min(18, slot * 0.66));
    const Y = v => m.t + ih - (ih * v) / max;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": spec.title }, container);
    const grid = el("g", { class: "grid" }, svg);
    for (const t of ticks(max, spec.format, 3)) {
      el("line", { x1: m.l, x2: m.l + iw, y1: Y(t), y2: Y(t) }, grid);
      el("text", { x: m.l - 6, y: Y(t) + 4, "text-anchor": "end", class: "tick" }, svg).textContent = axisLabel(t, spec.format);
    }
    const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 60))));
    spec.values.forEach((v, i) => {
      const cx = m.l + slot * i + slot / 2;
      if (i % every === 0) el("text", { x: cx, y: H - 7, "text-anchor": "middle", class: "tick" }, svg).textContent = shortPeriod(spec.x[i], spec.xType);
      const h = v ? Math.max(2, ih - (Y(v) - m.t)) : 0;
      const r = Math.min(3, bw / 2, h);
      const x0 = cx - bw / 2, y0 = m.t + ih - h;
      const b = h ? el("path", {
        d: `M${x0},${m.t + ih}V${y0 + r}Q${x0},${y0} ${x0 + r},${y0}H${x0 + bw - r}Q${x0 + bw},${y0} ${x0 + bw},${y0 + r}V${m.t + ih}Z`,
        fill: spec.color || "var(--series-1)",
      }, svg) : null;
      const hit = el("rect", { x: m.l + slot * i, y: m.t, width: slot, height: ih, fill: "transparent", class: "col-hit" }, svg);
      hit.addEventListener("mousemove", ev => {
        if (b) b.setAttribute("opacity", "0.8");
        showTip(`<div class="t-title">${esc(formatPeriod(spec.x[i], spec.xType))}</div><div class="t-row"><span>${esc(spec.title)}</span><b>${fmt(v, spec.format)}</b></div>`, ev.clientX, ev.clientY);
      });
      hit.addEventListener("mouseleave", () => { if (b) b.removeAttribute("opacity"); hideTip(); });
    });
    el("line", { x1: m.l, x2: m.l + iw, y1: Y(0), y2: Y(0), class: "baseline" }, svg);
  }

  /* -- Micro-courbe (carte KPI) ------------------------------------------- */
  function sparkline(container, values, color = "var(--series-1)") {
    container.innerHTML = "";
    const vals = (values || []).map(v => (v === null || v === undefined ? null : +v));
    const known = vals.filter(v => v !== null);
    if (known.length < 2) return;
    const W = 200, H = 38, pad = 3;
    const lo = Math.min(0, ...known), hi = Math.max(...known);
    const span = hi - lo || 1;
    const X = i => (W * i) / (vals.length - 1);
    const Y = v => H - pad - ((H - 2 * pad) * (v - lo)) / span;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", "aria-hidden": "true" }, container);
    let d = "", pen = false, first = null, last = null;
    vals.forEach((v, i) => {
      if (v === null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`;
      pen = true; if (first === null) first = i; last = i;
    });
    const area = `M${X(first)},${H}` + vals.map((v, i) => (v === null ? "" : `L${X(i).toFixed(1)},${Y(v).toFixed(1)}`)).join("") + `L${X(last)},${H}Z`;
    el("path", { d: area, fill: gradient(svg, color, 0.22), stroke: "none" }, svg);
    el("path", { d, fill: "none", stroke: color, "stroke-width": 1.6, "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" }, svg);
  }

  /* -- Jauge circulaire ---------------------------------------------------- */
  function donut(container, value, total, opts = {}) {
    const share = total ? Math.min(1, Math.max(0, value / total)) : 0;
    const R = 54, C = 2 * Math.PI * R;
    const color = share >= 1 ? "var(--critical)" : share >= 0.85 ? "var(--serious)" : "var(--accent)";
    container.innerHTML =
      `<svg viewBox="0 0 132 132" aria-hidden="true"><circle cx="66" cy="66" r="${R}" fill="none" stroke="var(--surface-3)" stroke-width="12"/>` +
      `<circle cx="66" cy="66" r="${R}" fill="none" stroke="${color}" stroke-width="12" stroke-linecap="round" ` +
      `stroke-dasharray="${(C * share).toFixed(1)} ${C.toFixed(1)}"/></svg>` +
      `<div class="center"><div><b>${total ? Math.round(share * 100) + " %" : "—"}</b><small>${esc(opts.label || "occupés")}</small></div></div>`;
    container.setAttribute("role", "img");
    container.setAttribute("aria-label", `${value} sur ${total} (${Math.round(share * 100)} %)`);
  }

  /* -- Carte de chaleur (rampe séquentielle bleue) ------------------------- */
  const SEQ = 9;
  function seqColor(v) {
    const step = Math.min(SEQ, Math.max(1, Math.ceil(v * SEQ) || 1));
    return { bg: `var(--seq-${step})`, dark: step >= 5 };
  }
  function heatLegend() {
    const leg = document.createElement("div");
    leg.className = "heat-legend";
    leg.innerHTML = "<span>0 %</span><span class='ramp'>" +
      Array.from({ length: SEQ }, (_, i) => `<i style="background:var(--seq-${i + 1})"></i>`).join("") +
      "</span><span>100 %</span><span style='margin-left:12px'><i style='display:inline-block;width:18px;height:10px;border-radius:2px;background:var(--empty);vertical-align:middle'></i> aucune donnée</span>";
    return leg;
  }
  function heatmap(container, legend, spec) {
    container.innerHTML = "";
    if (legend) legend.innerHTML = "";
    const g = document.createElement("div");
    g.className = "heatmap" + (spec.compact ? " compact" : "");
    const colW = spec.compact ? "minmax(6px, 1fr)" : "minmax(28px, 1fr)";
    g.style.gridTemplateColumns = `${spec.compact ? 30 : 48}px repeat(${spec.cols.length}, ${colW})`;
    g.appendChild(document.createElement("div"));
    spec.cols.forEach((c, j) => {
      const h = document.createElement("div");
      h.className = "h";
      h.textContent = spec.compact ? (j % 6 === 0 ? c : "") : c.slice(0, 3);
      g.appendChild(h);
    });
    spec.rows.forEach((r, i) => {
      const lab = document.createElement("div");
      lab.className = "r"; lab.textContent = spec.compact ? r.slice(0, 3) : r;
      g.appendChild(lab);
      spec.cols.forEach((c, j) => {
        const v = spec.values[i][j];
        const cell = document.createElement("div");
        cell.className = "c" + (v === null || v === undefined ? " null" : "");
        // Quasi nul : fond neutre, pour distinguer « libre » de « peu utilisé ».
        if (v !== null && v !== undefined) cell.style.background = v < 0.005 ? "var(--surface-2)" : seqColor(v).bg;
        const when = spec.compact ? `${r} ${c}` : `${c} ${r}`;
        cell.addEventListener("mousemove", ev => showTip(
          `<div class="t-title">${esc(when)}</div><div class="t-row"><span>Taux moyen</span><b>${v === null || v === undefined ? "aucune donnée" : fmt(v, "pct")}</b></div>`,
          ev.clientX, ev.clientY));
        cell.addEventListener("mouseleave", hideTip);
        g.appendChild(cell);
      });
    });
    container.appendChild(g);
    if (!spec.compact) container.appendChild(heatLegend());
  }

  return { fmt, setCurrency, line, bar, columns, sparkline, donut, heatmap, showTip, hideTip, esc, seqColor, formatPeriod };
})();
