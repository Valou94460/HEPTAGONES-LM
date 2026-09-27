/* Thème choisi (clair / sombre / auto) appliqué avant le premier rendu, sans flash. */
"use strict";
try {
  const t = localStorage.getItem("freelm_theme");
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
} catch (e) { /* stockage indisponible : thème du système */ }
