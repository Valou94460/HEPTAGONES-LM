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
class AppConfig:
    database: DatabaseConfig
    servers: list[ServerConfig]
    products: list[ProductRule] = field(default_factory=list)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    lmutil_path: str = "lmutil"
    log_file: str | None = None
    log_level: str = "INFO"

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
        database=DatabaseConfig(connection_string=conn),
        servers=servers,
        products=products,
        reporting=reporting,
        retention=retention,
        lmutil_path=_expand(raw.get("lmutil_path", "lmutil")),
        log_file=_expand(logging_cfg.get("file")),
        log_level=logging_cfg.get("level", "INFO"),
    )
