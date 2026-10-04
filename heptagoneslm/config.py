"""Chargement et validation du fichier de configuration YAML.

Voir ``config/heptagoneslm.example.yaml`` pour un exemple commenté.
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
    license_type: str = "flexlm"   # clé du registre des collecteurs : flexlm | rlm
    collectors: list[str] = field(default_factory=lambda: ["lmstat"])
    sessions_from: str = "lmstat"  # source des sessions : "lmstat", "log" (FlexLM) ou "rlmstat" (RLM)
    log_path: str | None = None
    lmutil_path: str | None = None
    rlmutil_path: str | None = None
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
    annual_cost: float | None = None   # coût annuel d'UN jeton (maintenance / abonnement)


@dataclass
class SiteRule:
    """Site d'un poste d'après son nom (motif fnmatch, insensible à la casse)."""

    pattern: str
    site: str


@dataclass
class ReportingConfig:
    output_dir: str = "reports"
    top_n_chart: int = 10          # nombre de features/utilisateurs dans les graphiques
    business_hours: tuple[int, int] = (8, 19)
    currency: str = "€"
    target_coverage: float = 0.95  # part du temps ouvré à couvrir pour la recommandation de jetons


@dataclass
class RetentionConfig:
    snapshots_days: int = 90       # snapshots bruts (les agrégats horaires sont conservés)
    events_days: int = 730


@dataclass
class AutodeskApiConfig:
    """API Autodesk « Business Success Plan Reporting » (Insights) : usage des licences nommées sans export manuel."""
    client_id: str
    client_secret: str | None = None   # application « Traditional Web App » ; absent : PKCE (application « Desktop »)
    context_id: str | None = None      # équipe (contexte) Autodesk ; absent : la seule équipe accessible
    callback_port: int = 8770          # URL de rappel à déclarer : http://localhost:<port>/autodesk/callback
    lookback_days: int = 90            # période d'usage interrogée à chaque synchronisation
    sync_hours: int = 24               # synchronisation au plus toutes les N heures (appelée par la collecte)
    token_file: str | None = None      # jeton de renouvellement (chiffré par DPAPI sous Windows)
    base_url: str = "https://developer.api.autodesk.com"
    timeout_s: int = 60

    @property
    def callback_url(self) -> str:
        return f"http://localhost:{self.callback_port}/autodesk/callback"


@dataclass
class AutodeskConfig:
    """Licences Autodesk « utilisateur nommé » importées des exports du portail Autodesk Account."""
    import_dir: str | None = None  # dossier surveillé : chaque export déposé est importé à la collecte
    inactive_days: int = 30        # licence inactive au-delà de ce nombre de jours sans utilisation
    annual_costs: dict[str, float] = field(default_factory=dict)  # motif de produit -> coût annuel d'une licence
    api: AutodeskApiConfig | None = None


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
    groups_header: str = "X-Remote-Groups"  # groupes Windows transmis par le module IIS (rôles)
    trusted_proxies: list[str] = field(default_factory=lambda: ["127.0.0.1", "::1"])
    allowed_users: list[str] = field(default_factory=list)
    # --- commun ---
    session_hours: float = 8.0
    max_failures: int = 5                  # échecs avant blocage temporaire
    lockout_minutes: int = 15


@dataclass
class ScopedRole:
    """Responsable de service : ne voit que les données de ses services (et, en option, serveurs / produits)."""

    departments: list[str]
    group: str | None = None       # groupe AD (« DOMAINE\\groupe », nom simple ou DN)
    user: str | None = None        # ou compte individuel
    servers: list[str] = field(default_factory=list)
    products: list[str] = field(default_factory=list)


@dataclass
class RolesConfig:
    """Rôles de l'interface web (absent : tout utilisateur autorisé voit tout)."""

    admins: list[str] = field(default_factory=list)     # groupes ou comptes : vue globale + administration
    readers: list[str] = field(default_factory=list)    # groupes ou comptes : vue globale
    scoped: list[ScopedRole] = field(default_factory=list)
    default: str = "none"          # utilisateur sans rôle : none (refusé) | reader (vue globale)


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    tls_cert: str | None = None            # certificat PEM : active HTTPS
    tls_key: str | None = None
    public_url: str | None = None          # adresse de l'interface, citée dans les e-mails
    allow_insecure_http: bool = False      # autoriser la connexion LDAP en HTTP sur le réseau (déconseillé)
    # Noms d'hôte acceptés dans l'en-tête Host (protection contre le « DNS rebinding »).
    # Toujours acceptés : localhost, 127.0.0.1, ::1, le nom de public_url, et en écoute
    # réseau le nom et les adresses du serveur. "*" désactive le contrôle (déconseillé).
    allowed_hosts: list[str] = field(default_factory=list)
    request_timeout_s: int = 30            # délai max d'une requête (connexions lentes)
    max_connections: int = 64              # requêtes traitées simultanément
    auth: AuthConfig = field(default_factory=AuthConfig)
    roles: RolesConfig | None = None
    demo: bool = False                     # données simulées : bandeau « Démonstration » dans l'interface


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


ALERT_TYPES = ("collection", "denial", "saturation", "expiry")
SEVERITIES = ("info", "warning", "serious", "critical")      # ordre croissant de gravité
ITSM_TYPES = ("servicenow", "jira", "glpi", "webhook")
WEBHOOK_FORMATS = ("json", "teams", "slack")


@dataclass
class ItsmConfig:
    """Connecteur ITSM : chaque alerte ouvre un ticket, le complète puis le résout."""

    name: str
    type: str                                  # servicenow | jira | glpi | webhook
    url: str
    username: str | None = None
    password: str | None = None                # ServiceNow / Jira Cloud : mot de passe ou jeton d'API
    token: str | None = None                   # Jira : jeton d'API (Cloud, avec username) ou PAT (Data Center)
    app_token: str | None = None               # GLPI : jeton d'application
    user_token: str | None = None              # GLPI : jeton d'API de l'utilisateur technique
    project: str | None = None                 # Jira : clé du projet
    issue_type: str = "Incident"               # Jira : type de ticket
    assignment_group: str | None = None        # ServiceNow : groupe d'affectation (nom ou sys_id)
    caller: str | None = None                  # ServiceNow : appelant (nom d'utilisateur ou sys_id)
    category: str | None = None                # ServiceNow : catégorie
    entity_id: int | None = None               # GLPI : entité
    category_id: int | None = None             # GLPI : catégorie ITIL
    resolve_transitions: list[str] = field(default_factory=lambda: ["Done", "Terminé", "Resolve Issue", "Résoudre",
                                                                       "Resolved", "Résolu", "Close Issue", "Fermer"])
    fields: dict = field(default_factory=dict)  # champs supplémentaires transmis à la création
    format: str = "json"                       # webhook : json | teams | slack
    headers: dict = field(default_factory=dict)  # webhook : en-têtes HTTP (ex. authentification)
    types: list[str] = field(default_factory=lambda: list(ALERT_TYPES))
    min_severity: str = "serious"              # gravité minimale pour ouvrir un ticket (refus, pannes, échéances proches)
    timeout_s: int = 20
    verify_tls: bool = True
    ca_file: str | None = None                 # autorité de certification interne
    allow_http: bool = False                   # autoriser http:// (déconseillé : secrets en clair)


@dataclass
class AlertConfig:
    """Alertes immédiates, évaluées après chaque collecte : e-mail et / ou tickets ITSM."""

    recipients: list[str] = field(default_factory=list)       # destinataires e-mail (vide : pas d'e-mail)
    itsm: list[ItsmConfig] = field(default_factory=list)
    types: list[str] = field(default_factory=lambda: list(ALERT_TYPES))
    servers: list[str] = field(default_factory=list)     # vide = tous
    products: list[str] = field(default_factory=list)    # vide = tous
    expiry_days: int = 30          # préavis avant l'échéance d'une licence
    repeat_hours: float = 24.0     # délai avant de renvoyer une alerte toujours présente
    min_denials: int = 1           # refus minimum pour alerter sur un produit


@dataclass
class EmailConfig:
    smtp_host: str | None = None
    smtp_port: int = 25
    security: str = "starttls"             # starttls | ssl | none
    username: str | None = None
    password: str | None = None
    sender: str = "HeptagonesLM <heptagoneslm@localhost>"
    timeout_s: int = 30
    max_attachment_mb: float = 15.0
    subscriptions: list[Subscription] = field(default_factory=list)
    alerts: AlertConfig | None = None      # même objet que AppConfig.alerts (compatibilité)


@dataclass
class AppConfig:
    database: DatabaseConfig
    servers: list[ServerConfig]
    products: list[ProductRule] = field(default_factory=list)
    sites: list[SiteRule] = field(default_factory=list)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    lmutil_path: str = "lmutil"
    rlmutil_path: str = "rlmutil"
    log_file: str | None = None
    log_level: str = "INFO"
    web: WebConfig = field(default_factory=WebConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    alerts: AlertConfig | None = None
    autodesk: AutodeskConfig = field(default_factory=AutodeskConfig)

    def server(self, name: str) -> ServerConfig:
        for srv in self.servers:
            if srv.name.lower() == name.lower():
                return srv
        raise ConfigError(f"Serveur inconnu dans la configuration : {name}")

    def product_costs(self) -> dict[str, float]:
        """Coût annuel d'un jeton par produit (première règle renseignée)."""
        costs: dict[str, float] = {}
        for rule in self.products:
            if rule.annual_cost is not None:
                costs.setdefault(rule.product, rule.annual_cost)
        return costs

    def resolve_site(self, hostname: str) -> str | None:
        """Site d'un poste : première règle ``sites`` dont le motif correspond au nom."""
        for rule in self.sites:
            if fnmatch.fnmatchcase((hostname or "").lower(), rule.pattern.lower()):
                return rule.site
        return None

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


#: Collecteur par défaut de chaque type de gestionnaire de licences.
DEFAULT_COLLECTOR = {"flexlm": "lmstat", "rlm": "rlmstat"}
#: Collecteurs utilisables par type (vérifiés au chargement de la configuration).
KNOWN_COLLECTORS = {"flexlm": ("lmstat", "log"), "rlm": ("rlmstat",)}


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
            license_type = str(s.get("license_type", "flexlm")).lower()
            if license_type not in DEFAULT_COLLECTOR:
                raise ConfigError(f"servers[{i}] : license_type '{license_type}' inconnu "
                                  f"(valeurs possibles : {', '.join(DEFAULT_COLLECTOR)})")
            default = DEFAULT_COLLECTOR[license_type]
            srv = ServerConfig(
                name=s["name"],
                port_at_host=s["port_at_host"],
                license_type=license_type,
                collectors=list(s.get("collectors", [default])),
                sessions_from=s.get("sessions_from", default),
                log_path=_expand(s.get("log_path")),
                lmutil_path=_expand(s.get("lmutil_path")),
                rlmutil_path=_expand(s.get("rlmutil_path")),
                timeout_s=int(s.get("timeout_s", 60)),
                enabled=bool(s.get("enabled", True)),
            )
        except KeyError as exc:
            raise ConfigError(f"servers[{i}] : champ obligatoire manquant {exc}") from None
        if srv.name.lower() in names:
            raise ConfigError(f"Nom de serveur en double : {srv.name}")
        names.add(srv.name.lower())
        bad = [c for c in srv.collectors if c not in KNOWN_COLLECTORS[srv.license_type]]
        if bad:
            raise ConfigError(f"{srv.name} : collecteur(s) {', '.join(bad)} inconnu(s) pour {srv.license_type} "
                              f"(possibles : {', '.join(KNOWN_COLLECTORS[srv.license_type])})")
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
            annual_cost=float(p["annual_cost"]) if p.get("annual_cost") not in (None, "") else None,
        )
        for p in raw.get("products") or []
    ]

    sites = []
    for i, r in enumerate(raw.get("sites") or []):
        if not isinstance(r, dict) or not r.get("pattern") or not r.get("site"):
            raise ConfigError(f"sites[{i}] : pattern et site sont obligatoires")
        sites.append(SiteRule(str(r["pattern"]), str(r["site"])))

    rep = raw.get("reporting") or {}
    bh = rep.get("business_hours", [8, 19])
    reporting = ReportingConfig(
        output_dir=_expand(rep.get("output_dir", "reports")),
        top_n_chart=int(rep.get("top_n_chart", 10)),
        business_hours=(int(bh[0]), int(bh[1])),
        currency=str(rep.get("currency", "€")),
        target_coverage=min(0.999, max(0.5, float(rep.get("target_coverage", 0.95)))),
    )
    ret = raw.get("retention") or {}
    retention = RetentionConfig(
        snapshots_days=int(ret.get("snapshots_days", 90)),
        events_days=int(ret.get("events_days", 730)),
    )
    logging_cfg = raw.get("logging") or {}
    autodesk = _parse_autodesk(raw.get("autodesk") or {})
    email_cfg = _parse_email(raw.get("email") or {})
    # Section de premier niveau « alerts » ; « email.alerts » accepté (première forme, 26.2 en préparation).
    if raw.get("alerts") and (raw.get("email") or {}).get("alerts"):
        raise ConfigError("alerts : section déclarée deux fois (alerts et email.alerts) — n'en garder qu'une")
    alerts = _parse_alerts(raw.get("alerts")) or _parse_alerts((raw.get("email") or {}).get("alerts"), "email.alerts")
    if alerts and alerts.recipients and not email_cfg.smtp_host:
        raise ConfigError("email.smtp_host est obligatoire pour envoyer les alertes par e-mail")
    email_cfg.alerts = alerts

    return AppConfig(
        web=_parse_web(raw.get("web") or {}),
        email=email_cfg,
        alerts=alerts,
        database=DatabaseConfig(connection_string=conn),
        servers=servers,
        products=products,
        sites=sites,
        reporting=reporting,
        retention=retention,
        lmutil_path=_expand(raw.get("lmutil_path", "lmutil")),
        rlmutil_path=_expand(raw.get("rlmutil_path", "rlmutil")),
        log_file=_expand(logging_cfg.get("file")),
        log_level=logging_cfg.get("level", "INFO"),
        autodesk=autodesk,
    )


def _parse_autodesk(a) -> AutodeskConfig:
    if not isinstance(a, dict):
        raise ConfigError("autodesk doit être une section (import_dir, inactive_days, annual_costs)")
    costs = a.get("annual_costs") or {}
    if not isinstance(costs, dict):
        raise ConfigError("autodesk.annual_costs : associer un produit (motif * accepté) à un coût annuel")
    try:
        parsed = {str(k): float(v) for k, v in costs.items()}
        inactive = int(a.get("inactive_days", 30))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"autodesk : valeur numérique attendue ({exc})") from exc
    if inactive < 1:
        raise ConfigError("autodesk.inactive_days doit être supérieur ou égal à 1")
    return AutodeskConfig(import_dir=_expand(a.get("import_dir")), inactive_days=inactive, annual_costs=parsed,
                          api=_parse_autodesk_api(a.get("api")))


def _parse_autodesk_api(c) -> AutodeskApiConfig | None:
    if not c:
        return None
    where = "autodesk.api"
    if not isinstance(c, dict):
        raise ConfigError(f"{where} doit être une section (client_id, client_secret, context_id…)")
    client_id = _expand(str(c.get("client_id") or "")).strip()
    if not client_id:
        raise ConfigError(f"{where}.client_id est obligatoire (application créée sur aps.autodesk.com)")
    try:
        port, lookback, sync = int(c.get("callback_port", 8770)), int(c.get("lookback_days", 90)), \
            int(c.get("sync_hours", 24))
        timeout = int(c.get("timeout_s", 60))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where} : valeur numérique attendue ({exc})") from exc
    if not 1024 <= port <= 65535:
        raise ConfigError(f"{where}.callback_port doit être compris entre 1024 et 65535")
    if not 1 <= lookback <= 365:
        raise ConfigError(f"{where}.lookback_days doit être compris entre 1 et 365")
    if sync < 1:
        raise ConfigError(f"{where}.sync_hours doit être supérieur ou égal à 1")
    base = str(c.get("base_url") or "https://developer.api.autodesk.com").rstrip("/")
    if not base.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise ConfigError(f"{where}.base_url doit être en HTTPS")
    secret = c.get("client_secret")
    return AutodeskApiConfig(client_id=client_id, client_secret=_expand(str(secret)) if secret else None,
                             context_id=str(c["context_id"]) if c.get("context_id") else None, callback_port=port,
                             lookback_days=lookback, sync_hours=sync, token_file=_expand(c.get("token_file")),
                             base_url=base, timeout_s=timeout)


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
        groups_header=a.get("groups_header", "X-Remote-Groups"),
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
        allowed_hosts=[h.lower() for h in _as_list(w.get("allowed_hosts"))],
        request_timeout_s=max(1, int(w.get("request_timeout_s", 30))),
        max_connections=max(1, int(w.get("max_connections", 64))),
        auth=auth,
        roles=_parse_roles(w.get("roles")),
        demo=bool(w.get("demo", False)),
    )
    if bool(web.tls_cert) != bool(web.tls_key):
        raise ConfigError("web.tls_cert et web.tls_key doivent être renseignés ensemble")
    return web


def _parse_roles(r) -> RolesConfig | None:
    if not r:
        return None
    if not isinstance(r, dict):
        raise ConfigError("web.roles doit être une section (admins, readers, departments)")
    scoped = []
    for i, d in enumerate(r.get("departments") or []):
        where = f"web.roles.departments[{i}]"
        if not isinstance(d, dict):
            raise ConfigError(f"{where} doit être une section (group ou user, departments)")
        role = ScopedRole(departments=_as_list(d.get("departments") or d.get("department")),
                          group=d.get("group"), user=(str(d["user"]).lower() if d.get("user") else None),
                          servers=_as_list(d.get("servers")), products=_as_list(d.get("products")))
        if bool(role.group) == bool(role.user):
            raise ConfigError(f"{where} : renseigner group OU user")
        if not role.departments:
            raise ConfigError(f"{where} : au moins un service (departments)")
        scoped.append(role)
    cfg = RolesConfig(admins=_as_list(r.get("admins")), readers=_as_list(r.get("readers")), scoped=scoped,
                      default=str(r.get("default", "none")).lower())
    if cfg.default not in ("none", "reader"):
        raise ConfigError("web.roles.default doit valoir none ou reader")
    if not (cfg.admins or cfg.readers or cfg.scoped):
        raise ConfigError("web.roles : déclarer au moins un rôle (admins, readers ou departments)")
    return cfg


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
        sender=e.get("sender", "HeptagonesLM <heptagoneslm@localhost>"),
        timeout_s=int(e.get("timeout_s", 30)),
        max_attachment_mb=float(e.get("max_attachment_mb", 15)),
        subscriptions=subs,
    )
    if cfg.security not in ("starttls", "ssl", "none"):
        raise ConfigError("email.security doit valoir starttls, ssl ou none")
    if cfg.security == "none" and cfg.username:
        raise ConfigError("email : authentification SMTP sans chiffrement refusée (identifiants en clair) — "
                          "utiliser security: starttls ou ssl")
    if subs and not cfg.smtp_host:
        raise ConfigError("email.smtp_host est obligatoire pour envoyer des rapports")
    return cfg


def _types(value, where: str) -> list[str]:
    types = [t.lower() for t in _as_list(value)] or list(ALERT_TYPES)
    unknown = [t for t in types if t not in ALERT_TYPES]
    if unknown:
        raise ConfigError(f"{where}.types : {', '.join(unknown)} inconnu(s) "
                          f"(valeurs possibles : {', '.join(ALERT_TYPES)})")
    return types


def _parse_itsm(i: int, c) -> ItsmConfig:
    where = f"alerts.itsm[{i}]"
    if not isinstance(c, dict):
        raise ConfigError(f"{where} doit être une section (type, url...)")
    kind = str(c.get("type", "")).lower()
    if kind not in ITSM_TYPES:
        raise ConfigError(f"{where}.type : valeurs possibles {', '.join(ITSM_TYPES)}")
    url = _expand(c.get("url")) or ""
    if not url.lower().startswith(("https://", "http://")):
        raise ConfigError(f"{where}.url : adresse https:// attendue")
    try:
        cfg = ItsmConfig(
            name=str(c.get("name") or kind), type=kind, url=url.rstrip("/"),
            username=_expand(c.get("username")), password=_expand(c.get("password")),
            token=_expand(c.get("token")), app_token=_expand(c.get("app_token")),
            user_token=_expand(c.get("user_token")),
            project=c.get("project"), issue_type=str(c.get("issue_type", "Incident")),
            assignment_group=c.get("assignment_group"), caller=c.get("caller"), category=c.get("category"),
            entity_id=int(c["entity_id"]) if c.get("entity_id") not in (None, "") else None,
            category_id=int(c["category_id"]) if c.get("category_id") not in (None, "") else None,
            fields=dict(c.get("fields") or {}), format=str(c.get("format", "json")).lower(),
            headers={str(k): _expand(str(v)) for k, v in (c.get("headers") or {}).items()},
            types=_types(c.get("types"), where), min_severity=str(c.get("min_severity", "serious")).lower(),
            timeout_s=int(c.get("timeout_s", 20)), verify_tls=bool(c.get("verify_tls", True)),
            ca_file=_expand(c.get("ca_file")), allow_http=bool(c.get("allow_http", False)),
        )
    except (TypeError, ValueError, AttributeError):
        raise ConfigError(f"{where} : valeur invalide (entity_id, category_id, timeout_s, fields, headers)") from None
    if c.get("resolve_transitions"):
        cfg.resolve_transitions = _as_list(c.get("resolve_transitions"))
    if cfg.url.lower().startswith("http://") and not cfg.allow_http:
        raise ConfigError(f"{where}.url : http:// refusé (identifiants en clair) — utiliser https:// "
                          "ou allow_http: true en test")
    if cfg.min_severity not in SEVERITIES:
        raise ConfigError(f"{where}.min_severity : valeurs possibles {', '.join(SEVERITIES)}")
    need = {"servicenow": ("username", "password"), "jira": ("project",), "glpi": ("app_token", "user_token"),
            "webhook": ()}[kind]
    missing = [n for n in need if not getattr(cfg, n)]
    if kind == "jira" and not cfg.token and not cfg.password:
        missing.append("token")
    if missing:
        raise ConfigError(f"{where} ({kind}) : {', '.join(missing)} obligatoire(s)")
    if kind == "webhook" and cfg.format not in WEBHOOK_FORMATS:
        raise ConfigError(f"{where}.format : valeurs possibles {', '.join(WEBHOOK_FORMATS)}")
    return cfg


def _parse_alerts(a, where: str = "alerts") -> AlertConfig | None:
    if not a:
        return None
    if not isinstance(a, dict):
        raise ConfigError(f"{where} doit être une section (recipients, itsm, types...)")
    recipients = _as_list(a.get("recipients"))
    connectors = [_parse_itsm(i, c) for i, c in enumerate(a.get("itsm") or [])]
    names = [c.name.lower() for c in connectors]
    if len(set(names)) != len(names):
        raise ConfigError(f"{where}.itsm : noms de connecteurs en double (renseigner name)")
    if not recipients and not connectors:
        raise ConfigError(f"{where} : au moins un destinataire (recipients) ou un connecteur (itsm)")
    types = _types(a.get("types"), where)
    try:
        cfg = AlertConfig(
            recipients=recipients,
            itsm=connectors,
            types=types,
            servers=_as_list(a.get("servers")),
            products=_as_list(a.get("products")),
            expiry_days=int(a.get("expiry_days", 30)),
            repeat_hours=float(a.get("repeat_hours", 24)),
            min_denials=int(a.get("min_denials", 1)),
        )
    except (TypeError, ValueError):
        raise ConfigError(f"{where} : expiry_days, repeat_hours et min_denials doivent être des nombres") from None
    if cfg.expiry_days < 0 or cfg.repeat_hours <= 0 or cfg.min_denials < 1:
        raise ConfigError(f"{where} : expiry_days >= 0, repeat_hours > 0 et min_denials >= 1")
    return cfg
