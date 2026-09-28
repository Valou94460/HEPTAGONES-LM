"""Authentification unique (IIS) : contrôle du SPN Kerberos, de la zone Intranet et des tests d'accès.

* **SPN** : un navigateur n'obtient un ticket Kerberos pour ``https://<nom>`` que si le SPN
  ``HTTP/<nom>`` est déclaré sur le compte qui déchiffre les tickets (le compte ordinateur du
  serveur, IIS utilisant l'authentification en mode noyau). Nom du serveur lui-même : couvert par
  ``HOST/<serveur>``. SPN absent : repli NTLM. SPN porté par un autre compte : Kerberos échoue.
* **Zone Intranet** : Edge et Chrome n'envoient automatiquement la session Windows qu'aux sites
  de la zone *Intranet local* (un nom avec des points est en zone Internet par défaut). Le
  contrôle lit les stratégies appliquées à ce serveur : indicatif, les postes doivent recevoir
  la même GPO, ce que confirme le test d'accès depuis un poste.
* **Test d'accès** : résultats enregistrés par la page ``/acces`` (``logs/tests-acces.jsonl``).
"""

from __future__ import annotations

import fnmatch
from datetime import datetime

from heptagoneslm.setup.answers import Answers
from heptagoneslm.setup.checks import Check
from heptagoneslm.setup.system import SetupError, System
from heptagoneslm.web import access

CATEGORY = "Authentification unique"
GPO_PATH = ("Configuration ordinateur › Stratégies › Modèles d'administration › Composants Windows › "
            "Internet Explorer › Panneau de configuration Internet › Page Sécurité › "
            "Liste des attributions de sites aux zones")


def _host(a: Answers) -> str:
    return (a.public_host or "").strip().lower()


def spn_check(system: System, a: Answers, info: dict | None) -> Check | None:
    """SPN HTTP/<nom public> sur le compte ordinateur du serveur (mode IIS uniquement)."""
    host = _host(a)
    if a.web_mode != "iis" or not host:
        return None
    label = "SPN Kerberos"
    if not info or not info.get("part_of_domain"):
        return Check("spn", CATEGORY, label, "info", "Serveur hors domaine : authentification NTLM, SPN sans objet")
    computer = str(info.get("hostname") or "").upper()
    names = {computer.lower(), str(info.get("fqdn") or "").lower()}
    spn = f"HTTP/{host}"
    command = f"setspn -S {spn} {computer}"
    if host in names:
        return Check("spn", CATEGORY, label, "ok",
                     f"{host} est le nom du serveur : couvert par le SPN HOST/ du compte {computer}$")
    try:
        owners = system.spn_owners(spn)
    except SetupError as exc:
        return Check("spn", CATEGORY, label, "warning",
                     f"Vérification impossible ({exc}) : faire exécuter la commande par un administrateur du domaine",
                     command=command)
    mine = [o for o in owners if o.lower() == computer.lower() + "$"]
    others = [o for o in owners if o.lower() != computer.lower() + "$"]
    if mine and not others:
        return Check("spn", CATEGORY, label, "ok", f"{spn} déclaré sur {computer}$ : Kerberos opérationnel")
    if others:
        fix = " ; ".join([f"setspn -D {spn} {o.rstrip('$')}" for o in others] + ([] if mine else [command]))
        return Check("spn", CATEGORY, label, "error",
                     f"{spn} déclaré sur {', '.join(others)}" + (f" et {computer}$ (doublon)" if mine else "")
                     + " : Kerberos échouera pour ce site. À corriger par un administrateur du domaine",
                     command=fix)
    return Check("spn", CATEGORY, label, "warning",
                 f"{spn} non déclaré : repli sur NTLM (fonctionne, Kerberos recommandé et parfois exigé "
                 "par la politique de sécurité). Droits d'administrateur du domaine nécessaires",
                 fix="register_spn", fix_label="Déclarer le SPN", command=command)


def zone_pattern_matches(pattern: str, host: str) -> bool:
    """Entrée de la liste des attributions de sites aux zones (ex. https://*.entreprise.local)."""
    p = pattern.strip().lower()
    scheme = None
    if "://" in p:
        scheme, p = p.split("://", 1)
    if scheme not in (None, "*", "https"):
        return False
    p = p.split("/")[0].split(":")[0]
    return p == host or p == "*" or (p.startswith("*.") and host.endswith(p[1:]))


def allowlist_matches(value: str, host: str) -> bool:
    """Stratégie AuthServerAllowlist d'Edge / Chrome (noms séparés par des virgules, jokers *)."""
    for item in (value or "").split(","):
        item = item.strip().lower()
        if item and (fnmatch.fnmatch(host, item) or host == item or host.endswith("." + item.lstrip("*."))):
            return True
    return False


def workstation_command(host: str) -> str:
    """Commande PowerShell (poste de test, utilisateur courant) : site en zone Intranet local."""
    labels = host.split(".")
    key = f"{'.'.join(labels[1:])}\\{labels[0]}" if len(labels) > 2 else host
    return ("New-Item -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings\\ZoneMap\\"
            f"Domains\\{key}' -Force | New-ItemProperty -Name https -Value 1 -PropertyType DWord -Force")


def zone_check(system: System, a: Answers) -> Check | None:
    """Zone Intranet local et stratégies Edge / Chrome (modes IIS et HTTPS intégré)."""
    host = _host(a)
    if a.web_mode != "iis" or not host:          # HTTPS intégré : saisie des identifiants, zone sans objet
        return None
    label = "Zone Intranet local (connexion automatique)"
    if "." not in host:
        return Check("zone", CATEGORY, label, "ok", f"Nom court {host} : zone Intranet local par défaut")
    try:
        pol = system.browser_policies() or {}
    except SetupError as exc:
        return Check("zone", CATEGORY, label, "warning", f"Lecture des stratégies impossible : {exc}",
                     command=workstation_command(host))
    zones = [z for z in pol.get("zones") or [] if zone_pattern_matches(str(z.get("pattern", "")), host)]
    blocked = [name for name, value in (pol.get("allowlist") or {}).items() if not allowlist_matches(value, host)]
    allow_note = (f" Stratégie AuthServerAllowlist de {', '.join(blocked)} définie sans ce nom : l'y ajouter."
                  if blocked else "")
    gpo = f"GPO « {GPO_PATH} » : https://{host} = 1 (ou https://*.{host.split('.', 1)[1]} = 1)"
    intranet = [z for z in zones if str(z.get("zone")) == "1"]
    if intranet:
        z = intranet[0]
        return Check("zone", CATEGORY, label, "warning" if blocked else "ok",
                     f"Zone Intranet local par GPO {z.get('scope')} (entrée {z.get('pattern')}), vérifiée sur ce "
                     "serveur : les postes doivent recevoir la même stratégie (confirmé par le test d'accès)."
                     + allow_note)
    if zones:
        z = zones[0]
        name = {"2": "Sites de confiance", "3": "Internet", "4": "Sites sensibles"}.get(str(z.get("zone")),
                                                                                        f"zone {z.get('zone')}")
        return Check("zone", CATEGORY, label, "warning",
                     f"Site attribué à « {name} » par GPO (entrée {z.get('pattern')}) : la session Windows n'y est "
                     f"pas transmise automatiquement. Attribuer la zone 1 (Intranet local) : {gpo}." + allow_note,
                     command=workstation_command(host))
    return Check("zone", CATEGORY, label, "warning",
                 f"Aucune attribution de zone pour {host} sur ce serveur : sans GPO, les navigateurs demanderont "
                 f"les identifiants. À déployer sur les postes : {gpo}. Test sur un seul poste : commande ci-dessous."
                 + allow_note, command=workstation_command(host))


def access_checks(system: System, a: Answers) -> list[Check]:
    """Résultats des tests d'accès depuis les postes (page /acces), pour la vérification finale."""
    if a.web_mode == "local" or not a.public_url:
        return []
    url = a.public_url.rstrip("/") + "/acces"
    results = access.latest_by_user(access.parse_results(system.read_text(a.path("logs", access.FILE_NAME))), 8)
    category = "Test depuis un poste utilisateur"
    if not results:
        return [Check("access", category, "Test d'accès depuis un poste", "warning",
                      f"À faire : ouvrir {url} depuis un poste utilisateur (membre du groupe autorisé), répondre à "
                      "la question, puis « Relancer la vérification »", command=url)]
    out = [Check("access", category, "Page de test", "info",
                 f"{len(results)} poste(s) testé(s) · autres tests : {url}", command=url)]
    for r in results:
        where = r.get("workstation") or r.get("client") or "poste inconnu"
        try:
            when = datetime.fromisoformat(r.get("at", "")).strftime("%d/%m %H:%M")
        except ValueError:
            when = "?"
        prompted = r.get("prompted")
        method = r.get("method") or "?"
        parts = [str(r.get("browser") or "navigateur ?"), method]
        if prompted is True:
            status = "warning"
            parts.append("mot de passe demandé : ajouter le site à la zone Intranet local de ce poste (GPO)")
        else:
            status = "ok"
            parts.append("connecté sans saisie" if prompted is False else "saisie non renseignée")
        if method == "NTLM":
            parts.append("NTLM : déclarer le SPN pour utiliser Kerberos")
        out.append(Check(f"access:{r.get('user')}", category, f"{where} · {r.get('display_name') or r.get('user')}",
                         status, " · ".join(parts) + f" ({when})"))
    return out
