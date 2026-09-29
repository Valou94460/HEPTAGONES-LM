"""Réponses de l'assistant et génération de la configuration ``heptagoneslm.yaml``.

Les mots de passe ne sont jamais écrits dans le fichier : ils sont stockés dans des
variables d'environnement machine (``HEPTAGONESLM_DB_PASSWORD``, ``HEPTAGONESLM_SMTP_PASSWORD``)
référencées par ``${VAR}``, ou servent une seule fois (compte de service des tâches).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import PureWindowsPath

import yaml

from heptagoneslm.config import ConfigError, parse_config

DB_PASSWORD_VAR = "HEPTAGONESLM_DB_PASSWORD"
SMTP_PASSWORD_VAR = "HEPTAGONESLM_SMTP_PASSWORD"
DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"

WEB_MODES = ("iis", "https", "local")
DB_MODES = ("sqlserver", "sqlite")
SECRET_FIELDS = ("service_password", "db_password", "smtp_password")

_ACCOUNT_RE = re.compile(r"^[\w.\- ]+\\[\w.\-$ ]+$")
_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_PORT_AT_HOST_RE = re.compile(r"^\d{2,5}@[\w.\-]+(:\d{2,5}@[\w.\-]+)*$")
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,62}))*$")
_SERVER_NAME_RE = re.compile(r"^[\w.\-]{1,64}$")
_EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
_THUMB_RE = re.compile(r"^[0-9A-Fa-f]{40}$")


LICENSE_TYPES = ("flexlm", "rlm")


@dataclass
class LicenseServer:
    name: str = ""
    port_at_host: str = ""
    collectors: list[str] = field(default_factory=lambda: ["lmstat"])
    log_path: str = ""
    license_type: str = "flexlm"      # flexlm | rlm

    @property
    def sessions_from(self) -> str:
        if self.license_type == "rlm":
            return "rlmstat"
        return "log" if "log" in self.collectors else "lmstat"

    @classmethod
    def from_dict(cls, s: dict) -> "LicenseServer":
        license_type = str(s.get("license_type") or "flexlm").lower()
        if license_type == "rlm":
            collectors = ["rlmstat"]
        else:
            collectors = [c for c in (s.get("collectors") or ["lmstat"]) if c in ("lmstat", "log")] or ["lmstat"]
        return cls(name=str(s.get("name", "")).strip(), port_at_host=str(s.get("port_at_host", "")).strip(),
                   collectors=collectors, log_path=str(s.get("log_path", "") or "").strip(),
                   license_type=license_type)


@dataclass
class Answers:
    # Emplacement et compte de service
    install_dir: str = r"C:\HeptagonesLM"
    service_account: str = ""
    service_password: str = ""        # jamais écrit sur disque ; vide pour un gMSA
    # Base de données
    db_mode: str = "sqlserver"
    db_server: str = ""
    db_name: str = "HeptagonesLM"
    db_driver: str = DEFAULT_DRIVER
    db_auth: str = "windows"          # windows (compte de service) | sql
    db_user: str = ""
    db_password: str = ""
    db_encrypt: bool = True
    db_trust_cert: bool = False
    db_create: bool = True            # créer la base, la connexion et les droits du compte de service
    # Serveurs de licences
    lmutil_path: str = ""
    rlmutil_path: str = ""            # serveurs RLM (Reprise) : rlmutil.exe
    servers: list[LicenseServer] = field(default_factory=list)
    # Interface web
    web_mode: str = "iis"             # iis | https | local
    web_port: int = 8765
    public_host: str = ""             # nom DNS utilisé par les navigateurs
    access_group: str = ""            # DOMAINE\groupe autorisé (vide = tout le domaine)
    iis_site: str = "HeptagonesLM"
    iis_port: int = 443
    iis_cert_thumbprint: str = ""
    tls_cert: str = ""
    tls_key: str = ""
    ldap_servers: list[str] = field(default_factory=list)
    base_dn: str = ""
    upn_suffix: str = ""
    open_firewall: bool = True
    # E-mail (optionnel)
    email_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 25
    smtp_security: str = "starttls"
    smtp_user: str = ""
    smtp_password: str = ""
    sender: str = ""
    recipients: list[str] = field(default_factory=list)
    email_alerts: bool = True         # alertes immédiates (collecte, refus, saturation, échéances)
    # Tâches planifiées
    collect_interval: int = 5
    # Mode silencieux : corriger automatiquement les prérequis manquants
    auto_fix: bool = False

    # ------------------------------------------------------------------ entrée
    @classmethod
    def from_dict(cls, raw: dict | None) -> "Answers":
        raw = dict(raw or {})
        known = {f.name: f for f in fields(cls)}
        kwargs = {}
        for name, value in raw.items():
            if name not in known:
                continue
            default = getattr(cls(), name)
            if name == "servers":
                kwargs[name] = [s if isinstance(s, LicenseServer) else LicenseServer.from_dict(s)
                                for s in (value or [])]
            elif isinstance(default, bool):
                kwargs[name] = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "oui", "on")
            elif isinstance(default, int):
                try:
                    kwargs[name] = int(value)
                except (TypeError, ValueError):
                    kwargs[name] = -1           # signalé par validate()
            elif isinstance(default, list):
                if isinstance(value, str):
                    value = re.split(r"[,;\s]+", value)
                kwargs[name] = [str(v).strip() for v in value or [] if str(v).strip()]
            else:
                kwargs[name] = "" if value is None else str(value).strip()
        return cls(**kwargs)

    def to_dict(self, secrets: bool = False) -> dict:
        data = asdict(self)
        if not secrets:
            for name in SECRET_FIELDS:
                data[name] = "********" if data[name] else ""
        return data

    # ----------------------------------------------------------------- dérivés
    @property
    def install_path(self) -> PureWindowsPath:
        return PureWindowsPath(self.install_dir)

    def path(self, *parts: str) -> str:
        return str(self.install_path.joinpath(*parts))

    @property
    def config_path(self) -> str:
        return self.path("heptagoneslm.yaml")

    @property
    def is_gmsa(self) -> bool:
        return self.service_account.endswith("$")

    @property
    def access_group_name(self) -> str:
        """Nom du groupe sans le domaine (comparaison LDAP par CN)."""
        return self.access_group.split("\\", 1)[-1]

    @property
    def public_url(self) -> str | None:
        if self.web_mode == "local":
            return f"http://localhost:{self.web_port}"
        if not self.public_host:
            return None
        port = self.iis_port if self.web_mode == "iis" else self.web_port
        return f"https://{self.public_host}" + ("" if port == 443 else f":{port}")

    def connection_string(self) -> str:
        if self.db_mode == "sqlite":
            return "sqlite:///" + self.path("data", "heptagoneslm.db").replace("\\", "/")
        parts = [f"DRIVER={{{self.db_driver}}}", f"SERVER={self.db_server}", f"DATABASE={self.db_name}"]
        if self.db_auth == "sql":
            parts += [f"UID={self.db_user}", f"PWD=${{{DB_PASSWORD_VAR}}}"]
        else:
            parts.append("Trusted_Connection=yes")
        parts.append("Encrypt=yes" if self.db_encrypt else "Encrypt=no")
        if self.db_trust_cert:
            parts.append("TrustServerCertificate=yes")
        return ";".join(parts)

    def machine_secrets(self) -> dict[str, str]:
        """Variables d'environnement machine à créer (secrets hors du fichier YAML)."""
        out = {}
        if self.db_mode == "sqlserver" and self.db_auth == "sql" and self.db_password:
            out[DB_PASSWORD_VAR] = self.db_password
        if self.email_enabled and self.smtp_user and self.smtp_password:
            out[SMTP_PASSWORD_VAR] = self.smtp_password
        return out

    # -------------------------------------------------------------- validation
    STEPS = ("location", "database", "licenses", "web", "email")

    def validate(self, step: str | None = None) -> list[dict]:
        """Erreurs de saisie : [{field, message}] (toutes, ou celles d'une étape)."""
        errors: list[dict] = []

        def err(name: str, message: str) -> None:
            errors.append({"field": name, "message": message})

        want = (lambda s: step is None or step == s)
        if want("location"):
            p = self.install_path
            if not self.install_dir or not p.drive or not p.is_absolute():
                err("install_dir", "Chemin absolu attendu, par exemple C:\\HeptagonesLM")
            elif any(c in self.install_dir for c in '<>"|?*'):
                err("install_dir", "Caractères interdits dans le chemin")
            if not _ACCOUNT_RE.match(self.service_account or ""):
                err("service_account", "Format DOMAINE\\compte attendu (gMSA : DOMAINE\\compte$)")
            elif self.service_account.upper().split("\\")[-1] in ("SYSTEM", "SYSTÈME", "LOCALSYSTEM"):
                err("service_account", "Le compte SYSTEM est refusé : utiliser un compte de service dédié")
            elif not self.is_gmsa and not self.service_password:
                err("service_password", "Mot de passe du compte de service requis (sauf gMSA)")
            if not 1 <= self.collect_interval <= 60:
                err("collect_interval", "Intervalle entre 1 et 60 minutes")
        if want("database"):
            if self.db_mode not in DB_MODES:
                err("db_mode", "Mode de base inconnu")
            elif self.db_mode == "sqlserver":
                if not re.match(r"^[\w.\-\\,]+$", self.db_server or ""):
                    err("db_server", "Nom de l'instance SQL Server requis (ex. SQLSRV01 ou SQLSRV01\\INSTANCE)")
                if not _DB_NAME_RE.match(self.db_name or ""):
                    err("db_name", "Nom de base : lettres, chiffres et _ uniquement")
                if self.db_auth not in ("windows", "sql"):
                    err("db_auth", "Authentification inconnue")
                if self.db_auth == "sql":
                    if not self.db_user:
                        err("db_user", "Identifiant SQL requis")
                    if not self.db_password:
                        err("db_password", "Mot de passe SQL requis")
        if want("licenses"):
            types = {s.license_type for s in self.servers}
            if (not types or "flexlm" in types) and not self.lmutil_path:
                err("lmutil_path", "Chemin de lmutil.exe requis")
            if "rlm" in types and not self.rlmutil_path:
                err("rlmutil_path", "Chemin de rlmutil.exe requis pour les serveurs RLM")
            if not self.servers:
                err("servers", "Déclarer au moins un serveur de licences")
            names = set()
            for i, s in enumerate(self.servers):
                if not _SERVER_NAME_RE.match(s.name):
                    err(f"servers.{i}.name", "Nom : lettres, chiffres, point, tiret")
                elif s.name.lower() in names:
                    err(f"servers.{i}.name", "Nom déjà utilisé")
                names.add(s.name.lower())
                if s.license_type not in LICENSE_TYPES:
                    err(f"servers.{i}.license_type", "Type de gestionnaire inconnu")
                if not _PORT_AT_HOST_RE.match(s.port_at_host):
                    err(f"servers.{i}.port_at_host", "Format port@serveur attendu (ex. 27000@licsrv1)")
                if "log" in s.collectors and not s.log_path:
                    err(f"servers.{i}.log_path", "Chemin du log debug requis pour le collecteur log")
        if want("web"):
            if self.web_mode not in WEB_MODES:
                err("web_mode", "Mode d'interface inconnu")
            if not 1024 <= self.web_port <= 65535:
                err("web_port", "Port entre 1024 et 65535")
            if self.web_mode in ("iis", "https"):
                if not _HOST_RE.match(self.public_host or ""):
                    err("public_host", "Nom DNS attendu (ex. heptagoneslm.entreprise.local)")
                if self.access_group and not _ACCOUNT_RE.match(self.access_group):
                    err("access_group", "Format DOMAINE\\groupe attendu")
            if self.web_mode == "iis":
                if not re.match(r"^[\w .\-]{1,64}$", self.iis_site or ""):
                    err("iis_site", "Nom de site IIS invalide")
                if not 1 <= self.iis_port <= 65535:
                    err("iis_port", "Port invalide")
                if not _THUMB_RE.match(self.iis_cert_thumbprint or ""):
                    err("iis_cert_thumbprint", "Choisir un certificat (empreinte SHA-1 de 40 caractères)")
                if self.iis_port == self.web_port:
                    err("web_port", "Le port interne doit différer du port IIS")
            if self.web_mode == "https":
                if not self.tls_cert:
                    err("tls_cert", "Chemin du certificat PEM requis")
                if not self.tls_key:
                    err("tls_key", "Chemin de la clé PEM requis")
                if not self.ldap_servers or not all(re.match(r"^ldaps?://[\w.\-]+(:\d+)?$", s) for s in self.ldap_servers):
                    err("ldap_servers", "Contrôleurs de domaine attendus (ex. ldaps://dc01.entreprise.local)")
                if not re.match(r"^(DC|OU|CN)=", self.base_dn or "", re.I):
                    err("base_dn", "Base de recherche attendue (ex. DC=entreprise,DC=local)")
                if not _HOST_RE.match(self.upn_suffix or ""):
                    err("upn_suffix", "Suffixe UPN attendu (ex. entreprise.local)")
        if want("email") and self.email_enabled:
            if not _HOST_RE.match(self.smtp_host or ""):
                err("smtp_host", "Serveur SMTP requis")
            if not 1 <= self.smtp_port <= 65535:
                err("smtp_port", "Port invalide")
            if self.smtp_security not in ("starttls", "ssl", "none"):
                err("smtp_security", "Sécurité inconnue")
            if self.smtp_user and self.smtp_security == "none":
                err("smtp_security", "Authentification SMTP interdite sans chiffrement")
            sender_addr = re.sub(r"^.*<([^>]+)>\s*$", r"\1", self.sender or "")
            if not _EMAIL_RE.match(sender_addr):
                err("sender", "Adresse d'expéditeur attendue")
            if not self.recipients or not all(_EMAIL_RE.match(r) for r in self.recipients):
                err("recipients", "Au moins une adresse de destinataire valide")
        return errors

    # ------------------------------------------------------------ configuration
    def config_dict(self) -> dict:
        cfg: dict = {
            "database": {"connection_string": self.connection_string()},
            "lmutil_path": self.lmutil_path or "lmutil",  # tel quel (scripts .cmd : barres obliques inversées)
            **({"rlmutil_path": self.rlmutil_path} if self.rlmutil_path else {}),
            "logging": {"file": self.path("logs", "heptagoneslm.log").replace("\\", "/"), "level": "INFO"},
            "servers": [],
            "products": [],
            "reporting": {"output_dir": self.path("reports").replace("\\", "/"),
                          "business_hours": [8, 19], "currency": "€", "target_coverage": 0.95},
            "retention": {"snapshots_days": 90, "events_days": 730},
        }
        for s in self.servers:
            entry = {"name": s.name, "port_at_host": s.port_at_host, "collectors": list(s.collectors),
                     "sessions_from": s.sessions_from}
            if s.license_type != "flexlm":
                entry = {"name": s.name, "port_at_host": s.port_at_host, "license_type": s.license_type,
                         "collectors": list(s.collectors), "sessions_from": s.sessions_from}
            if s.log_path:
                entry["log_path"] = s.log_path
            cfg["servers"].append(entry)
        web: dict = {"host": "127.0.0.1", "port": self.web_port}
        if self.web_mode == "iis":
            web["public_url"] = self.public_url
            web["auth"] = {"mode": "header", "header_name": "X-Remote-User",
                           "trusted_proxies": ["127.0.0.1", "::1"], "allowed_users": []}
        elif self.web_mode == "https":
            web.update({"host": "0.0.0.0", "tls_cert": self.tls_cert.replace("\\", "/"),
                        "tls_key": self.tls_key.replace("\\", "/"), "public_url": self.public_url})
            web["auth"] = {"mode": "ldap", "ldap_servers": list(self.ldap_servers), "base_dn": self.base_dn,
                           "upn_suffix": self.upn_suffix,
                           "allowed_groups": [self.access_group_name] if self.access_group else []}
        else:
            web["auth"] = {"mode": "none"}
        cfg["web"] = web
        if self.email_enabled:
            email = {"smtp_host": self.smtp_host, "smtp_port": self.smtp_port, "security": self.smtp_security,
                     "sender": self.sender,
                     "subscriptions": [{"name": "Direction", "recipients": list(self.recipients),
                                        "period": "last-month"}]}
            if self.email_alerts:
                email["alerts"] = {"recipients": list(self.recipients)}
            if self.smtp_user:
                email["username"] = self.smtp_user
                email["password"] = f"${{{SMTP_PASSWORD_VAR}}}"
            cfg["email"] = email
        return cfg

    def yaml_text(self, generated_at: str = "") -> str:
        header = ("# HeptagonesLM - configuration générée par l'assistant d'installation"
                  + (f" le {generated_at}" if generated_at else "") + "\n"
                  "# Référence de tous les paramètres : heptagoneslm.example.yaml / dossier d'exploitation (DEX).\n"
                  "# Après modification des règles « products » : HeptagonesLM.exe apply-products\n"
                  "# Les mots de passe sont dans des variables d'environnement machine (${VAR}).\n\n")
        body = yaml.safe_dump(self.config_dict(), allow_unicode=True, sort_keys=False, default_flow_style=False,
                              width=120)
        body = body.replace("products: []\n", "products: []   # règles feature -> produit (voir heptagoneslm.example.yaml)\n")
        return header + body

    def check_config(self) -> str | None:
        """Valide la configuration générée avec le chargeur réel de HeptagonesLM."""
        try:
            parse_config(self.config_dict())
        except ConfigError as exc:
            return str(exc)
        return None
