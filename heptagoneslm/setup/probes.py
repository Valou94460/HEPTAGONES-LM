"""Tests de connexion proposés par l'assistant (boutons « Tester »).

Chaque test renvoie ``{"status": ok|warning|error, "message": str, "details": [str]}``
et s'appuie sur le code réel de HeptagonesLM (parser lmstat, authentification LDAP, envoi SMTP).
"""

from __future__ import annotations

from heptagoneslm.collectors.flexlm.lmstat import parse_lmstat
from heptagoneslm.collectors.rlm.rlmstat import parse_rlmstat
from heptagoneslm.setup.answers import Answers
from heptagoneslm.setup.system import SetupError, System


def _res(status: str, message: str, details: list[str] | None = None, **extra) -> dict:
    return {"status": status, "message": message, "details": details or [], **extra}


def test_account(system: System, a: Answers) -> dict:
    if not a.service_account:
        return _res("error", "Compte de service non renseigné")
    try:
        sid = system.resolve_account(a.service_account)
    except SetupError as exc:
        return _res("error", f"Vérification impossible : {exc}")
    if not sid:
        return _res("error", f"Compte {a.service_account} introuvable dans l'annuaire")
    if a.is_gmsa:
        return _res("ok", f"Compte de service géré (gMSA) trouvé ({sid})",
                    ["Vérifier que ce serveur fait partie de PrincipalsAllowedToRetrieveManagedPassword",
                     "et exécuter Install-ADServiceAccount sur ce serveur si nécessaire."])
    if not a.service_password:
        return _res("warning", "Compte trouvé ; saisir son mot de passe pour le vérifier")
    try:
        valid = system.validate_credentials(a.service_account, a.service_password)
    except SetupError as exc:
        return _res("warning", f"Compte trouvé, mot de passe non vérifiable : {exc}")
    if valid is False:
        return _res("error", "Mot de passe incorrect pour ce compte")
    return _res("ok", f"Compte {a.service_account} trouvé et mot de passe valide")


def test_database(system: System, a: Answers) -> dict:
    if a.db_mode == "sqlite":
        return _res("ok", "Base SQLite locale : aucun serveur requis",
                    [f"Fichier : {a.path('data', 'heptagoneslm.db')}", "Réservé à l'évaluation ou à un petit parc"])
    probe = system.db_probe(a)
    if not probe.get("ok"):
        return _res("error", f"Connexion impossible : {probe.get('error')}",
                    ["Vérifier le nom de l'instance, le pare-feu (TCP 1433) et le chiffrement (certificat SQL)."])
    details = [probe.get("server", "")]
    who = "votre compte Windows" if a.db_auth == "windows" else f"l'identifiant SQL {a.db_user}"
    details.append(f"Test réalisé avec {who}")
    if probe.get("exists"):
        schema = probe.get("schema")
        details.append(f"Base {a.db_name} présente" + (f" (schéma v{schema})" if schema else " (vide)"))
        return _res("ok", "Connexion réussie", details, exists=True)
    if a.db_create and not probe.get("can_create"):
        return _res("error", f"Base {a.db_name} absente et droits insuffisants pour la créer", details +
                    ["Demander au DBA d'exécuter sql\\sqlserver\\00_create_database.sql, ou utiliser un compte sysadmin."])
    if not a.db_create:
        return _res("warning", f"Base {a.db_name} absente et création non demandée", details)
    details.append(f"Base {a.db_name} absente : elle sera créée par l'installation")
    return _res("ok", "Connexion réussie", details, exists=False)


def test_lmstat(system: System, lmutil: str, port_at_host: str, license_type: str = "flexlm") -> dict:
    """Interroge un serveur de licences en direct (lmstat pour FlexLM, rlmstat pour RLM)."""
    rlm = license_type == "rlm"
    tool, label = ("rlmutil", "rlmstat") if rlm else ("lmutil", "lmstat")
    if not lmutil:
        return _res("error", f"Chemin de {tool}.exe non renseigné")
    r = system.run([lmutil, label, "-a", "-c", port_at_host], timeout=90)
    if r.code in (124, 126, 127):
        return _res("error", r.message, [f"Indiquer le chemin complet de {tool}.exe, ou utiliser « Rechercher l'outil »."])
    status = (parse_rlmstat if rlm else parse_lmstat)(r.out + "\n" + r.err, port_at_host)
    if not status.server_up:
        return _res("error", f"Serveur {port_at_host} injoignable",
                    [line for line in status.errors[:3]] or [r.message])
    feats = status.features
    issued = sum(f.issued or 0 for f in feats)
    in_use = sum(f.in_use for f in feats)
    vendors = ", ".join(f"{k} ({v})" for k, v in status.vendors.items()) or "—"
    names = ", ".join(sorted({f.name for f in feats})[:6]) + (" …" if len(feats) > 6 else "")
    return _res("ok", f"{len(feats)} {'licences' if rlm else 'features'}, {in_use} / {issued} jetons utilisés",
                [f"{'RLM' if rlm else 'FlexLM'} {status.server_version or '?'} · démons : {vendors}",
                 f"{'Licences' if rlm else 'Features'} : {names}"],
                features=len(feats))


def test_ldap(system: System, a: Answers, username: str, password: str) -> dict:
    if not username or not password:
        return _res("error", "Saisir un identifiant et un mot de passe de test")
    if system.simulated:
        if password == "mauvais":
            return _res("error", "Identifiant ou mot de passe incorrect")
        return _res("ok", f"Connexion réussie : {username}",
                    [f"Annuaire : {', '.join(a.ldap_servers)}",
                     f"Groupe {a.access_group_name} : membre" if a.access_group else "Aucune restriction de groupe"])
    from heptagoneslm.config import AuthConfig
    from heptagoneslm.web.auth import AuthError, LdapAuthenticator

    cfg = AuthConfig(mode="ldap", ldap_servers=list(a.ldap_servers), base_dn=a.base_dn, upn_suffix=a.upn_suffix,
                     allowed_groups=[a.access_group_name] if a.access_group else [])
    try:
        user = LdapAuthenticator(cfg).authenticate(username, password)
    except AuthError as exc:
        return _res("error", str(exc), [exc.reason] if getattr(exc, "reason", None) else [])
    except Exception as exc:
        return _res("error", f"Annuaire indisponible : {exc}")
    return _res("ok", f"Connexion réussie : {user.display_name or user.username}",
                [f"Service : {user.department}" if user.department else "Service non renseigné dans l'annuaire"])


def test_group(system: System, a: Answers) -> dict:
    if not a.access_group:
        return _res("warning", "Aucun groupe : tout utilisateur du domaine pourra se connecter")
    try:
        sid = system.resolve_account(a.access_group)
    except SetupError as exc:
        return _res("error", f"Vérification impossible : {exc}")
    return _res("ok", f"Groupe {a.access_group} trouvé") if sid else _res("error", f"Groupe {a.access_group} introuvable")


def test_certificate(system: System, a: Answers) -> dict:
    try:
        certs = {c["thumbprint"].upper(): c for c in system.certificates()}
    except SetupError as exc:
        return _res("error", f"Lecture du magasin impossible : {exc}")
    cert = certs.get((a.iis_cert_thumbprint or "").upper())
    if not cert:
        return _res("error", "Certificat introuvable dans LocalMachine\\My")
    names = [n.strip().lower() for n in (cert.get("dns") or "").split(",") if n.strip()]
    if cert.get("expired"):
        return _res("error", f"Certificat expiré le {cert.get('not_after')}")
    if a.public_host and names and a.public_host.lower() not in names:
        return _res("warning", f"Le certificat ne couvre pas {a.public_host}",
                    [f"Noms du certificat : {', '.join(names)}", "Le navigateur affichera un avertissement."])
    return _res("ok", f"{cert.get('subject')} · valide jusqu'au {cert.get('not_after')}")


def test_smtp(system: System, a: Answers, to: str | None = None) -> dict:
    to = to or (a.recipients[0] if a.recipients else "")
    if not to:
        return _res("error", "Aucun destinataire de test")
    if system.simulated:
        return _res("ok", f"Message de test envoyé à {to}", [f"Relais {a.smtp_host}:{a.smtp_port} ({a.smtp_security})"])
    from email.message import EmailMessage

    from heptagoneslm.config import EmailConfig
    from heptagoneslm.mailer import send_message

    cfg = EmailConfig(smtp_host=a.smtp_host, smtp_port=a.smtp_port, security=a.smtp_security,
                      username=a.smtp_user or None, password=a.smtp_password or None, sender=a.sender)
    msg = EmailMessage()
    msg["Subject"] = "HeptagonesLM : message de test de l'assistant d'installation"
    msg["From"] = a.sender
    msg["To"] = to
    msg.set_content("Ce message confirme que HeptagonesLM peut envoyer les rapports par e-mail.")
    try:
        send_message(msg, cfg)
    except Exception as exc:
        return _res("error", f"Envoi impossible : {exc}")
    return _res("ok", f"Message de test envoyé à {to}")
