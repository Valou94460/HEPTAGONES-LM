"""Modèle de données normalisé, commun à tous les types de gestionnaires
de licences (FlexLM aujourd'hui, RLM ou autre demain).

Les collecteurs produisent ces objets ; le service de collecte les persiste.
Aucun code de parsing ne doit écrire directement en base.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    """Types d'évènements issus des logs du gestionnaire de licences."""

    OUT = "OUT"                  # checkout
    IN = "IN"                    # checkin
    DENIED = "DENIED"            # refus (plus de jeton disponible, etc.)
    QUEUED = "QUEUED"            # mise en file d'attente
    DEQUEUED = "DEQUEUED"
    UNSUPPORTED = "UNSUPPORTED"  # feature inconnue du serveur
    SERVER_DOWN = "SERVER_DOWN"  # arrêt du démon : ferme les sessions ouvertes


@dataclass
class Checkout:
    """Un jeton actuellement emprunté, vu par une requête d'état (lmstat)."""

    feature: str
    user: str
    host: str
    display: str | None = None
    version: str | None = None
    handle: str | None = None
    start_time: datetime | None = None
    count: int = 1

    def session_key(self) -> tuple:
        """Clé d'identification stable d'un checkout entre deux sondages.

        Le handle FlexLM est unique pour un checkout donné ; à défaut on
        s'appuie sur l'heure de début.
        """
        discriminator = self.handle or (self.start_time.isoformat() if self.start_time else "")
        return (self.feature, self.user.lower(), self.host.lower(), discriminator)


@dataclass
class FeatureStatus:
    """État d'une feature à un instant donné (jetons émis / utilisés)."""

    name: str
    issued: int | None
    in_use: int
    vendor: str | None = None
    version: str | None = None
    error: str | None = None
    checkouts: list[Checkout] = field(default_factory=list)
    queued: int = 0


@dataclass
class ServerStatus:
    """Résultat d'un sondage complet d'un serveur de licences."""

    port_at_host: str
    queried_at: datetime
    server_up: bool
    server_version: str | None = None
    vendors: dict[str, str] = field(default_factory=dict)  # démon -> "UP"/"DOWN"/message
    features: list[FeatureStatus] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class LicenseEvent:
    """Évènement horodaté extrait d'un log de licences."""

    event_time: datetime
    event_type: EventType
    feature: str | None = None
    user: str | None = None
    host: str | None = None
    vendor: str | None = None
    count: int = 1
    detail: str | None = None
