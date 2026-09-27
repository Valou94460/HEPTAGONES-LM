"""Chargement et validation du fichier de configuration YAML.

Voir ``config/freelm.example.yaml`` pour un exemple commenté.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(ValueError):
    """Erreur de configuration (champ manquant, valeur invalide...)."""


@dataclass
class DatabaseConfig:
    # Chaîne de connexion ODBC (SQL Server) ou "sqlite:///chemin.db"
    connection_string: str


@dataclass
class ServerConfig:
    """Un serveur de licences à surveiller."""

    name: str
    port_at_host: str              # ex. "27000@licsrv1" ou triade "27000@a:27000@b:27000@c"
    license_type: str = "flexlm"   # clé du registre des collecteurs (flexlm, rlm...)
    collectors: list[str] = field(default_factory=lambda: ["lmstat"])
    sessions_from: str = "lmstat"  # source des sessions : "lmstat" ou "log"
    log_path: str | None = None
    lmutil_path: str | None = None
    timeout_s: int = 60
    enabled: bool = True


@dataclass
class ProductRule:
    """Règle de correspondance feature technique -> produit lisible.

    ``pattern`` est un motif fnmatch (``*``, ``?``) insensible à la casse,
    appliqué au nom de la feature (et optionnellement au démon vendeur).
    """

    pattern: str
    product: str
    publisher: str | None = None
    vendor: str | None = None


@dataclass
class ReportingConfig:
    output_dir: str = "reports"
    top_n_chart: int = 10          # nombre de features/utilisateurs dans les graphiques
    business_hours: tuple[int, int] = (8, 19)


@dataclass
class RetentionConfig:
    snapshots_days: int = 90       # snapshots bruts (les agrégats horaires sont conservés)
    events_days: int = 730


@dataclass
class AuthConfig:
    """Authentification de l'interface web.

    * ``none``   : pas d'authentification (interface limitée au poste local par défaut).
    * ``ldap``   : page de connexion, identifiants vérifiés auprès de l'Active Directory.
    * ``header`` : authentification unique Windows assurée par un reverse proxy (IIS)
      qui transmet l'utilisateur dans un en-tête HTTP.
    """

    mode: str = "none"
    # --- ldap ---
    ldap_servers: list[str] = field(default_factory=list)   # ex. ldaps://dc01.entreprise.local
    ldap_starttls: bool = False            # ldap:// + STARTTLS
    ldap_ca_file: str | None = None        # AC interne pour valider le certificat du DC
    ldap_validate_cert: bool = True
    base_dn: str = ""                      # ex. DC=entreprise,DC=local
    upn_suffix: str | None = None          # connexion directe en user@suffixe (AD)
    bind_dn: str | None = None             # sinon : compte de service pour rechercher l'utilisateur
    bind_password: str | None = None
    user_filter: str = "(sAMAccountName={username})"
    allowed_groups: list[str] = field(default_factory=list)   # vide = tout utilisateur authentifié
    # --- header ---
    header_name: str = "X-Remote-User"
    trusted_proxies: list[str] = field(default_factory=lambda: ["127.0.0.1", "::1"])
    allowed_users: list[str] = field(default_factory=list)
    # --- commun ---
    session_hours: float = 8.0
    max_failures: int = 5                  # échecs avant blocage temporaire
    lockout_minutes: int = 15


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    tls_cert: str | None = None            # certificat PEM : active HTTPS
    tls_key: str | None = None
    public_url: str | None = None          # adresse de l'interface, citée dans les e-mails
    allow_insecure_http: bool = False      # autoriser la connexion LDAP en HTTP sur le réseau (déconseillé)
    auth: AuthConfig = field(default_factory=AuthConfig)


@dataclass
class Subscription:
    """Abonnement à l'envoi périodique du rapport par e-mail."""

    name: str
    recipients: list[str]
    period: str = "last-month"
    servers: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)
    attach_excel: bool = True
    subject: str | None = None


@dataclass
class EmailConfig:
    smtp_host: str | None = None
    smtp_port: int = 25
    security: str = "starttls"             # starttls | ssl | none
    username: str | None = None
    password: str | None = None
    sender: str = "FreeLM <freelm@localhost>"
    timeout_s: int = 30
    max_attachment_mb: float = 15.0
    subscriptions: list[Subscription] = field(default_factory=list)


@dataclass
class AppConfig:
    database: DatabaseConfig
    servers: list[ServerConfig]
    products: list[ProductRule] = field(default_factory=list)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    lmutil_path: str = "lmutil"
    log_file: str | None = None
    log_level: str = "INFO"
    web: WebConfig = field(default_factory=WebConfig)
    email: EmailConfig = field(default_factory=EmailConfig)

    def server(self, name: str) -> ServerConfig:
        for srv in self.servers:
            if srv.name.lower() == name.lower():
                return srv
        raise ConfigError(f"Serveur inconnu dans la configuration : {name}")

    def resolve_product(self, feature: str, vendor: str | None = None) -> ProductRule | None:
        """Retourne la première règle produit correspondant à la feature."""
        for rule in self.products:
            if rule.vendor and (vendor or "").lower() != rule.vendor.lower():
                continue
            if fnmatch.fnmatchcase(feature.lower(), rule.pattern.lower()):
                return rule
        return None


def _expand(value: str | None) -> str | None:
    """Permet d'utiliser des variables d'environnement (%VAR% ou ${VAR})."""
    if value is None:
        return None
    return os.path.expandvars(value)


def load_config(path: str | os.PathLike) -> AppConfig:
    """Charge le fichier YAML et retourne une configuration validée."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Fichier de configuration introuvable : {path}")
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return parse_config(raw)


def parse_config(raw: dict) -> AppConfig:
    db = raw.get("database") or {}
    conn = _expand(db.get("connection_string"))
    if not conn:
        raise ConfigError("database.connection_string est obligatoire")

    servers: list[ServerConfig] = []
    names: set[str] = set()
    for i, s in enumerate(raw.get("servers") or []):
        try:
            srv = ServerConfig(
                name=s["name"],
                port_at_host=s["port_at_host"],
                license_type=s.get("license_type", "flexlm"),
                collectors=list(s.get("collectors", ["lmstat"])),
                sessions_from=s.get("sessions_from", "lmstat"),
                log_path=_expand(s.get("log_path")),
                lmutil_path=_expand(s.get("lmutil_path")),
                timeout_s=int(s.get("timeout_s", 60)),
                enabled=bool(s.get("enabled", True)),
            )
        except KeyError as exc:
            raise ConfigError(f"servers[{i}] : champ obligatoire manquant {exc}") from None
        if srv.name.lower() in names:
            raise ConfigError(f"Nom de serveur en double : {srv.name}")
        names.add(srv.name.lower())
        if srv.sessions_from not in srv.collectors:
            raise ConfigError(
                f"{srv.name} : sessions_from='{srv.sessions_from}' mais ce collecteur n'est pas activé"
            )
        if "log" in srv.collectors and not srv.log_path:
            raise ConfigError(f"{srv.name} : log_path est obligatoire avec le collecteur 'log'")
        servers.append(srv)

    products = [
        ProductRule(
            pattern=p["pattern"],
            product=p["product"],
            publisher=p.get("publisher"),
            vendor=p.get("vendor"),
        )
        for p in raw.get("products") or []
    ]

    rep = raw.get("reporting") or {}
    bh = rep.get("business_hours", [8, 19])
    reporting = ReportingConfig(
        output_dir=_expand(rep.get("output_dir", "reports")),
        top_n_chart=int(rep.get("top_n_chart", 10)),
        business_hours=(int(bh[0]), int(bh[1])),
    )
    ret = raw.get("retention") or {}
    retention = RetentionConfig(
        snapshots_days=int(ret.get("snapshots_days", 90)),
        events_days=int(ret.get("events_days", 730)),
    )
    logging_cfg = raw.get("logging") or {}

    return AppConfig(
        web=_parse_web(raw.get("web") or {}),
        email=_parse_email(raw.get("email") or {}),
        database=DatabaseConfig(connection_string=conn),
        servers=servers,
        products=products,
        reporting=reporting,
        retention=retention,
        lmutil_path=_expand(raw.get("lmutil_path", "lmutil")),
        log_file=_expand(logging_cfg.get("file")),
        log_level=logging_cfg.get("level", "INFO"),
    )


PERIODS = ("last-month", "this-month", "last-week", "last-7d", "last-year")


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _parse_web(w: dict) -> WebConfig:
    a = w.get("auth") or {}
    auth = AuthConfig(
        mode=str(a.get("mode", "none")).lower(),
        ldap_servers=_as_list(a.get("ldap_servers") or a.get("ldap_server")),
        ldap_starttls=bool(a.get("ldap_starttls", False)),
        ldap_ca_file=_expand(a.get("ldap_ca_file")),
        ldap_validate_cert=bool(a.get("ldap_validate_cert", True)),
        base_dn=a.get("base_dn", ""),
        upn_suffix=a.get("upn_suffix"),
        bind_dn=_expand(a.get("bind_dn")),
        bind_password=_expand(a.get("bind_password")),
        user_filter=a.get("user_filter", "(sAMAccountName={username})"),
        allowed_groups=_as_list(a.get("allowed_groups")),
        header_name=a.get("header_name", "X-Remote-User"),
        trusted_proxies=_as_list(a.get("trusted_proxies")) or ["127.0.0.1", "::1"],
        allowed_users=[u.lower() for u in _as_list(a.get("allowed_users"))],
        session_hours=float(a.get("session_hours", 8)),
        max_failures=int(a.get("max_failures", 5)),
        lockout_minutes=int(a.get("lockout_minutes", 15)),
    )
    if auth.mode not in ("none", "ldap", "header"):
        raise ConfigError("web.auth.mode doit valoir none, ldap ou header")
    if auth.mode == "ldap":
        if not auth.ldap_servers:
            raise ConfigError("web.auth.ldap_servers est obligatoire en mode ldap")
        if not auth.base_dn:
            raise ConfigError("web.auth.base_dn est obligatoire en mode ldap")
        if not auth.upn_suffix and not auth.bind_dn:
            raise ConfigError("web.auth : renseigner upn_suffix (AD) ou bind_dn/bind_password (compte de service)")
        if "{username}" not in auth.user_filter:
            raise ConfigError("web.auth.user_filter doit contenir {username}")
    web = WebConfig(
        host=w.get("host", "127.0.0.1"),
        port=int(w.get("port", 8765)),
        tls_cert=_expand(w.get("tls_cert")),
        tls_key=_expand(w.get("tls_key")),
        public_url=w.get("public_url"),
        allow_insecure_http=bool(w.get("allow_insecure_http", False)),
        auth=auth,
    )
    if bool(web.tls_cert) != bool(web.tls_key):
        raise ConfigError("web.tls_cert et web.tls_key doivent être renseignés ensemble")
    return web


def _parse_email(e: dict) -> EmailConfig:
    subs = []
    names: set[str] = set()
    for i, s in enumerate(e.get("subscriptions") or []):
        try:
            sub = Subscription(
                name=s["name"],
                recipients=_as_list(s["recipients"]),
                period=s.get("period", "last-month"),
                servers=_as_list(s.get("servers")),
                products=_as_list(s.get("products")),
                attach_excel=bool(s.get("attach_excel", True)),
                subject=s.get("subject"),
            )
        except KeyError as exc:
            raise ConfigError(f"email.subscriptions[{i}] : champ obligatoire manquant {exc}") from None
        if not sub.recipients:
            raise ConfigError(f"Abonnement {sub.name} : aucun destinataire")
        if sub.period not in PERIODS:
            raise ConfigError(f"Abonnement {sub.name} : period doit valoir {', '.join(PERIODS)}")
        if sub.name.lower() in names:
            raise ConfigError(f"Abonnement en double : {sub.name}")
        names.add(sub.name.lower())
        subs.append(sub)
    cfg = EmailConfig(
        smtp_host=e.get("smtp_host"),
        smtp_port=int(e.get("smtp_port", 25)),
        security=str(e.get("security", "starttls")).lower(),
        username=_expand(e.get("username")),
        password=_expand(e.get("password")),
        sender=e.get("sender", "FreeLM <freelm@localhost>"),
        timeout_s=int(e.get("timeout_s", 30)),
        max_attachment_mb=float(e.get("max_attachment_mb", 15)),
        subscriptions=subs,
    )
    if cfg.security not in ("starttls", "ssl", "none"):
        raise ConfigError("email.security doit valoir starttls, ssl ou none")
    if subs and not cfg.smtp_host:
        raise ConfigError("email.smtp_host est obligatoire pour envoyer des rapports")
    return cfg
