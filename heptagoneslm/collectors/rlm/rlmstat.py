"""Collecte par ``rlmutil rlmstat -a -c port@host`` (Reprise License Manager).

RLM est utilisé notamment par Foundry (Nuke), Maxon, Chaos (V-Ray), Altium ou
certains produits Siemens. Exemple de sortie traitée (extrait) ::

    rlm status on licsrv3 (port 5053), up 20d 03:11:42
    ...
    --------- ISV servers ----------
       Name           Port Running Restarts
    foundry          62137     Yes        0

    foundry license pool status on licsrv3 (port 62137)

    nuke_i v2024.0
        count: 10, # reservations: 0, inuse: 3, exp: 31-dec-2026
        obsolete: 0, min_remove: 120, total checkouts: 57

    foundry license usage status on licsrv3 (port 62137)

    nuke_i v2024.0: jdoe@PC123 1/0 at 09/23 08:15  (handle: 41)

Une même licence peut exister en plusieurs pools (versions, échéances) : les
jetons sont additionnés par nom de licence et l'échéance la plus proche est
retenue, comme pour FlexLM. RLM n'indique pas les refus par licence dans
``rlmstat`` (seulement un total par démon) : ils figurent dans le report log.
"""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import datetime

from heptagoneslm.collectors.base import NO_WINDOW, CollectorError, StatusCollector, register
from heptagoneslm.collectors.flexlm.lmstat import _decode, _infer_start, parse_expiry
from heptagoneslm.models import Checkout, FeatureStatus, ServerStatus

log = logging.getLogger(__name__)

_SERVER_UP_RE = re.compile(r"^rlm status on \S+ \(port \d+\), up")
_SERVER_VERSION_RE = re.compile(r"^rlm software version v(?P<ver>\S+)")
_SERVER_DOWN_RE = re.compile(
    r"(Error connecting to|Communications error|Connection refused|Cannot connect|"
    r"rlm server is not running|No license server|Timeout)",
    re.IGNORECASE,
)
_ISV_ROW_RE = re.compile(r"^(?P<isv>[\w.\-]+)\s+\d+\s+(?P<running>Yes|No)\s+\d+\s*$", re.IGNORECASE)
_POOL_SECTION_RE = re.compile(r"^(?P<isv>[\w.\-]+) license pool status on ")
_USAGE_SECTION_RE = re.compile(r"^(?P<isv>[\w.\-]+) license usage status on ")
_POOL_HEADER_RE = re.compile(r"^(?P<name>[\w.\-+]+) v(?P<version>\S+)$")
_POOL_COUNT_RE = re.compile(
    r"^(?:count:\s*(?P<count>\d+)|(?P<uncounted>UNCOUNTED)).*?inuse:\s*(?P<inuse>\d+)"
    r"(?:.*?exp:\s*(?P<exp>[^,\s]+))?", re.IGNORECASE)
# "nuke_i v2024.0: jdoe@PC123 1/0 at 09/23 08:15  (handle: 41)"
_USAGE_RE = re.compile(
    r"^(?P<name>[\w.\-+]+) v(?P<version>[^:\s]+):\s+(?P<user>.+?)@(?P<host>[^\s@]+)\s+"
    r"(?P<count>\d+)/(?P<res>\d+)\s+at\s+(?P<mon>\d{1,2})/(?P<day>\d{1,2})(?:/(?P<year>\d{2,4}))?\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?P<rest>.*)$")
_HANDLE_RE = re.compile(r"\(handle:\s*(?P<handle>\w+)\)")
_QUEUED_RE = re.compile(r"queued", re.IGNORECASE)


def parse_rlmstat(text: str, port_at_host: str = "", queried_at: datetime | None = None) -> ServerStatus:
    """Parse la sortie de ``rlmstat -a`` en ``ServerStatus`` (modèle commun à tous les gestionnaires)."""
    queried_at = queried_at or datetime.now().replace(microsecond=0)
    status = ServerStatus(port_at_host=port_at_host, queried_at=queried_at, server_up=False)
    features: dict[str, FeatureStatus] = {}
    section, isv, pool = None, None, None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _SERVER_UP_RE.match(line):
            status.server_up = True
            continue
        vm = _SERVER_VERSION_RE.match(line)
        if vm:
            status.server_version = vm["ver"]
            continue
        if line.startswith("--------- ISV servers"):
            section = "isv"
            continue
        m = _POOL_SECTION_RE.match(line)
        if m:
            section, isv, pool = "pool", m["isv"], None
            continue
        m = _USAGE_SECTION_RE.match(line)
        if m:
            section, isv, pool = "usage", m["isv"], None
            continue
        if line.startswith("----"):
            section = None if section == "isv" else section
            continue

        if section == "isv":
            m = _ISV_ROW_RE.match(line)
            if m:
                status.vendors[m["isv"]] = "UP" if m["running"].lower() == "yes" else "DOWN"
            continue

        if section == "pool":
            m = _POOL_HEADER_RE.match(line)
            if m:
                pool = m
                continue
            m = _POOL_COUNT_RE.match(line)
            if m and pool:
                name = pool["name"]
                feat = features.get(name)
                if feat is None:
                    feat = features[name] = FeatureStatus(name=name, issued=0, in_use=0, vendor=isv,
                                                          version=pool["version"])
                    status.features.append(feat)
                if m["uncounted"]:     # licence non comptée (nodelock), comme « Uncounted » de FlexLM
                    feat.issued = None
                elif feat.issued is not None:
                    feat.issued += int(m["count"])
                feat.in_use += int(m["inuse"])
                expires = parse_expiry(m["exp"])
                if expires and (feat.expires is None or expires < feat.expires):
                    feat.expires = expires
                pool = None
            continue

        if section == "usage":
            m = _USAGE_RE.match(line)
            if not m:
                continue
            name = m["name"]
            feat = features.get(name)
            if feat is None:       # licence absente des pools (sortie tronquée) : créée à la volée
                feat = features[name] = FeatureStatus(name=name, issued=None, in_use=0, vendor=isv,
                                                      version=m["version"])
                status.features.append(feat)
            if _QUEUED_RE.search(m["rest"] or ""):
                feat.queued += int(m["count"])
                continue
            handle = _HANDLE_RE.search(m["rest"] or "")
            feat.checkouts.append(Checkout(
                feature=name, user=m["user"].strip(), host=m["host"], version=m["version"],
                handle=handle["handle"] if handle else None,
                start_time=_infer_start(queried_at, m), count=int(m["count"])))
            continue

        if _SERVER_DOWN_RE.search(line) and section is None:
            status.errors.append(line)

    if status.features:
        status.server_up = True
    return status


@register("rlm", "rlmstat")
class RlmstatCollector(StatusCollector):
    """Exécute ``rlmutil rlmstat -a -c <port@host>`` et parse le résultat."""

    def command(self) -> list[str]:
        rlmutil = self.server.rlmutil_path or self.app.rlmutil_path
        return [rlmutil, "rlmstat", "-a", "-c", self.server.port_at_host]

    def run_rlmstat(self) -> str:
        cmd = self.command()
        log.debug("Exécution : %s", " ".join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=self.server.timeout_s, check=False, **NO_WINDOW)
        except FileNotFoundError:
            raise CollectorError(f"rlmutil introuvable : {cmd[0]}") from None
        except subprocess.TimeoutExpired:
            raise CollectorError(
                f"rlmstat {self.server.port_at_host} : délai dépassé ({self.server.timeout_s}s)"
            ) from None
        return _decode(proc.stdout) + "\n" + _decode(proc.stderr)

    def poll(self) -> ServerStatus:
        now = datetime.now().replace(microsecond=0)
        output = self.run_rlmstat()
        status = parse_rlmstat(output, self.server.port_at_host, queried_at=now)
        if not status.server_up:
            detail = "; ".join(status.errors) or output.strip()[-500:]
            raise CollectorError(f"Serveur RLM {self.server.port_at_host} injoignable : {detail}")
        return status
