/* HeptagonesLM — test d'accès depuis un poste utilisateur (page /acces). */
"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const METHOD_HELP = {
    "Kerberos": "authentification unique optimale",
    "NTLM": "fonctionne ; Kerberos est préférable (SPN à déclarer, voir la vérification de l'installation)",
  };

  async function load() {
    try {
      const r = await fetch("api/access-test", { credentials: "same-origin", cache: "no-store" });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.error || `Erreur ${r.status}`);
      $("access-status").className = "access-status ok";
      $("access-status").innerHTML = `<b>Accès autorisé</b> : vous êtes reconnu comme <b>${esc(body.display_name || body.user)}</b>.`;
      const rows = [
        ["Identifiant", body.user],
        body.department ? ["Service", body.department] : null,
        ["Authentification", body.method + (METHOD_HELP[body.method] ? ` — ${METHOD_HELP[body.method]}` : "")],
        ["Poste", body.workstation ? `${body.workstation} (${body.client})` : body.client],
        ["Navigateur", body.browser],
      ].filter(Boolean);
      $("access-details").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
      $("access-details").hidden = false;
      $("access-form").hidden = false;
    } catch (e) {
      $("access-status").className = "access-status error";
      $("access-status").textContent = `Accès impossible : ${e.message}`;
    }
  }

  $("access-form").addEventListener("change", () => { $("access-submit").disabled = false; });
  $("access-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    const choice = new FormData($("access-form")).get("prompted");
    $("access-submit").disabled = true;
    try {
      const r = await fetch("api/access-test", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-HeptagonesLM": "1" },
        body: JSON.stringify({ prompted: choice === "yes" }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.error || `Erreur ${r.status}`);
      $("access-result").className = "access-result ok";
      $("access-result").textContent = choice === "yes"
        ? "Résultat enregistré. Une saisie de mot de passe indique que le site n'est pas dans la zone Intranet local de ce poste : l'administrateur en est informé."
        : "Résultat enregistré, merci : l'accès fonctionne depuis ce poste.";
      $("access-form").hidden = true;
    } catch (e) {
      $("access-result").className = "access-result error";
      $("access-result").textContent = `Enregistrement impossible : ${e.message}`;
      $("access-submit").disabled = false;
    }
    $("access-result").hidden = false;
  });
  load();
})();
