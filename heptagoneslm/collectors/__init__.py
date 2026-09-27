"""Collecteurs de données de licences.

L'import de ce package enregistre tous les collecteurs connus dans le
registre (voir ``base.register``).
"""

from heptagoneslm.collectors.base import (  # noqa: F401
    BaseCollector,
    CollectorError,
    LogCollector,
    StatusCollector,
    available_collectors,
    get_collector_class,
    register,
)

# Enregistrement des implémentations : ajouter ici les futurs types (rlm...).
from heptagoneslm.collectors import flexlm  # noqa: F401,E402
