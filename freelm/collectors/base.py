"""Interfaces communes des collecteurs.

Deux familles de collecteurs existent :

* ``StatusCollector`` : interroge l'état courant d'un serveur (ex. ``lmutil
  lmstat -a``) et retourne un ``ServerStatus`` (jetons émis/utilisés et
  checkouts en cours). Appelé à chaque exécution planifiée (polling).
* ``LogCollector``    : lit de façon incrémentale un fichier de log et
  retourne les nouveaux ``LicenseEvent`` (OUT/IN/DENIED...). Il reçoit et
  renvoie un état opaque (offset, date courante...) persisté par le service.

Pour ajouter un nouveau gestionnaire de licences (ex. RLM) : implémenter une
ou deux classes héritant de ces interfaces, puis les déclarer avec
``@register("rlm", "rlmstat")`` dans un module importé par
``freelm.collectors``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from freelm.config import AppConfig, ServerConfig
from freelm.models import LicenseEvent, ServerStatus


class CollectorError(RuntimeError):
    """Erreur bloquante lors d'une collecte (serveur injoignable, binaire absent...)."""


class BaseCollector(ABC):
    #: "status" ou "log" ; renseigné par les sous-classes intermédiaires.
    kind: ClassVar[str]
    #: nom du collecteur dans la configuration (ex. "lmstat").
    name: ClassVar[str] = ""

    def __init__(self, server: ServerConfig, app: AppConfig):
        self.server = server
        self.app = app


class StatusCollector(BaseCollector):
    kind = "status"

    @abstractmethod
    def poll(self) -> ServerStatus:
        """Interroge le serveur et retourne son état courant."""


class LogCollector(BaseCollector):
    kind = "log"

    @abstractmethod
    def read_new_events(self, state: dict) -> tuple[list[LicenseEvent], dict]:
        """Lit les évènements apparus depuis ``state``.

        Retourne la liste des évènements et le nouvel état à persister. Le
        nouvel état ne doit être enregistré qu'une fois les évènements
        commités en base (garantie "au moins une fois", sans doublon en
        pratique grâce à la transaction unique du service).
        """


_REGISTRY: dict[tuple[str, str], type[BaseCollector]] = {}


def register(license_type: str, name: str):
    """Décorateur d'enregistrement d'un collecteur."""

    def decorator(cls: type[BaseCollector]) -> type[BaseCollector]:
        cls.name = name
        _REGISTRY[(license_type.lower(), name.lower())] = cls
        return cls

    return decorator


def get_collector_class(license_type: str, name: str) -> type[BaseCollector]:
    try:
        return _REGISTRY[(license_type.lower(), name.lower())]
    except KeyError:
        known = ", ".join(f"{t}/{n}" for t, n in sorted(_REGISTRY))
        raise CollectorError(
            f"Aucun collecteur '{name}' pour le type '{license_type}' (disponibles : {known})"
        ) from None


def available_collectors() -> list[tuple[str, str]]:
    return sorted(_REGISTRY)
