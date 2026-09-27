/* FreeLM — page de connexion (comptes Windows / Active Directory). */
"use strict";

(() => {
  const form = document.getElementById("login-form");
  const error = document.getElementById("login-error");
  const submit = document.getElementById("login-submit");

  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    error.hidden = true;
    const username = form.username.value.trim();
    const password = form.password.value;
    if (!username || !password) {
      error.textContent = "Saisissez votre identifiant et votre mot de passe.";
      error.hidden = false;
      return;
    }
    submit.disabled = true;
    submit.textContent = "Connexion…";
    try {
      const r = await fetch("api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
        credentials: "same-origin",
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.error || `Erreur ${r.status}`);
      // Retour au rapport demandé avant la connexion (filtres conservés).
      // (le navigateur conserve le fragment #… lors de la redirection vers cette page)
      let next = location.hash;
      try {
        next = next || sessionStorage.getItem("freelm_next_hash") || "";
        sessionStorage.removeItem("freelm_next_hash");
      } catch (e) { /* stockage indisponible */ }
      location.replace("./" + next);
    } catch (e) {
      error.textContent = e.message;
      error.hidden = false;
      form.password.value = "";
      form.password.focus();
    } finally {
      submit.disabled = false;
      submit.textContent = "Se connecter";
    }
  });
})();
