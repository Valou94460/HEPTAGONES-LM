"""Collecte par ``lmutil lmstat -a -c port@host`` et parsing de sa sortie.

Exemple de sortie traitée (extrait) ::

    Flexible License Manager status on Mon 9/23/2024 10:12

    License server status: 27000@licsrv1
       licsrv1: license server UP (MASTER) v11.16.2

    Vendor daemon status (on licsrv1):
         adskflex: UP v11.16.2

    Feature usage info:

    Users of 87545ACD_2024_0F:  (Total of 10 licenses issued;  Total of 2 licenses in use)

      "87545ACD_2024_0F" v1.000, vendor: adskflex, expiry: permanent(no expiration date)
      floating license

        jdoe PC123 PC123 (v1.0) (licsrv1/27000 1302), start Mon 9/23 8:15
        asmith PC456 PC456 (v1.0) (licsrv1/27000 1502), start Mon 9/23 9:02, 2 licenses
"""

from __future__ import annotations

import logging
import re
import subprocess
from datetime import date, datetime, timedelta

from freelm.collectors.base import CollectorError, StatusCollector, register
from freelm.models import Checkout, FeatureStatus, ServerStatus

log = logging.getLogger(__name__)

_HEADER_DATE_RE = re.compile(
    r"status on \w+\s+(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{2,4})\s+(?P<H>\d{1,2}):(?P<M>\d{2})"
)
_SERVER_UP_RE = re.compile(r"license server UP(?:\s+\((?P<role>[A-Z]+)\))?(?:\s+v(?P<ver>\S+))?")
_SERVER_DOWN_RE = re.compile(
    r"(Cannot connect to license server|lmgrd is not running|license server DOWN|Cannot find license file)",
    re.IGNORECASE,
)
_VENDOR_RE = re.compile(r"^\s*(?P<vendor>[\w.\-]+):\s+(?P<status>UP|DOWN|The desired vendor daemon is down.*|.*)$")
_FEATURE_RE = re.compile(
    r"^Users of (?P<name>[^:]+):\s+\((?P<body>.*)\)\s*$"
)
_TOTALS_RE = re.compile(
    r"Total of (?P<issued>\d+) licenses? issued;\s+Total of (?P<in_use>\d+) licenses? in use",
    re.IGNORECASE,
)
_FEATURE_INFO_RE = re.compile(
    r'^\s*"(?P<name>[^"]+)"\s+v(?P<version>[^,\s]+),\s+vendor:\s+(?P<vendor>[^,\s]+)'
    r'(?:,\s+expiry:\s*(?P<expiry>[^,\s]+))?'
)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_expiry(text: str | None) -> date | None:
    """« 31-dec-2026 » -> date ; « permanent(...) », « 1-jan-0 » ou illisible -> None."""
    if not text:
        return None
    m = re.match(r"^(\d{1,2})-([a-z]{3})-(\d{1,4})$", text.strip().lower())
    if not m or m.group(2) not in _MONTHS:
        return None
    year = int(m.group(3))
    if year == 0:            # convention FlexLM : 1-jan-0 = permanente
        return None
    if year < 100:
        year += 2000
    try:
        return date(year, _MONTHS[m.group(2)], int(m.group(1)))
    except ValueError:
        return None
# Ligne utilisateur : "<user> <host> <display> (vX) (server/port handle), start Day M/D H:MM[, N licenses]"
_CHECKOUT_RE = re.compile(
    r"^\s+(?P<ident>\S.*?)\s+\((?P<version>v[^)]*)\)\s+"
    r"\((?P<server>[^/\s)]+)(?:/(?P<port>\d+))?\s+(?P<handle>\d+)\),\s+"
    r"start\s+\w+\s+(?P<mon>\d{1,2})/(?P<day>\d{1,2})(?:/(?P<year>\d{2,4}))?\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?P<rest>.*)$"
)
_QUEUED_RE = re.compile(
    r"^\s+(?P<ident>\S.*?)\s+\((?P<version>v[^)]*)\)\s+\([^)]*\)\s+queued for (?P<count>\d+) licenses?"
)
_COUNT_RE = re.compile(r",\s*(?P<count>\d+)\s+licenses?")


def _split_ident(ident: str) -> tuple[str, str, str | None]:
    """Découpe "user host display" ; le nom d'utilisateur peut contenir des espaces."""
    tokens = ident.split()
    if len(tokens) >= 3:
        return " ".join(tokens[:-2]), tokens[-2], tokens[-1]
    if len(tokens) == 2:
        return tokens[0], tokens[1], None
    return tokens[0], "", None


def _infer_start(ref: datetime, m: re.Match) -> datetime | None:
    """Reconstruit l'heure de début (lmstat n'affiche pas l'année)."""
    try:
        month, day = int(m["mon"]), int(m["day"])
        hour, minute = int(m["hour"]), int(m["minute"])
        if m["year"]:
            year = int(m["year"])
            year += 2000 if year < 100 else 0
            return datetime(year, month, day, hour, minute)
        start = datetime(ref.year, month, day, hour, minute)
        # Un checkout ne peut pas démarrer dans le futur : changement d'année.
        if start > ref + timedelta(days=1):
            start = start.replace(year=ref.year - 1)
        return start
    except ValueError:
        return None


def parse_lmstat(text: str, port_at_host: str = "", queried_at: datetime | None = None) -> ServerStatus:
    """Parse la sortie texte de ``lmstat -a`` en ``ServerStatus``.

    ``queried_at`` sert de référence pour l'année des heures de début ; à
    défaut, la date d'en-tête de lmstat (ou l'heure courante) est utilisée.
    """
    header = _HEADER_DATE_RE.search(text)
    if queried_at is None:
        if header:
            y = int(header["y"])
            y += 2000 if y < 100 else 0
            queried_at = datetime(y, int(header["m"]), int(header["d"]), int(header["H"]), int(header["M"]))
        else:
            queried_at = datetime.now().replace(microsecond=0)

    status = ServerStatus(port_at_host=port_at_host, queried_at=queried_at, server_up=False)
    section = None
    current: FeatureStatus | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue

        if line.startswith("Vendor daemon status"):
            section = "vendors"
            continue
        if line.startswith("Feature usage info"):
            section = "features"
            continue

        up = _SERVER_UP_RE.search(line)
        if up:
            status.server_up = True
            status.server_version = up["ver"] or status.server_version
            continue
        if _SERVER_DOWN_RE.search(line) and section != "features":
            status.errors.append(line.strip())
            continue

        feat = _FEATURE_RE.match(line)
        if feat:
            section = "features"
            body = feat["body"]
            totals = _TOTALS_RE.search(body)
            if totals:
                current = FeatureStatus(
                    name=feat["name"].strip(),
                    issued=int(totals["issued"]),
                    in_use=int(totals["in_use"]),
                )
            else:
                # "(Error: 1 licenses, unsupported by licensed server)", "(Uncounted, node-locked)"...
                current = FeatureStatus(
                    name=feat["name"].strip(),
                    issued=None,
                    in_use=0,
                    error=None if "uncounted" in body.lower() else body.strip(),
                )
            status.features.append(current)
            continue

        if section == "vendors" and current is None:
            vm = _VENDOR_RE.match(line)
            if vm and not line.strip().startswith("License"):
                st = vm["status"].strip()
                status.vendors[vm["vendor"]] = "UP" if st.startswith("UP") else st
            continue

        if current is None:
            continue

        info = _FEATURE_INFO_RE.match(line)
        if info:
            if info["name"] == current.name:
                current.vendor = current.vendor or info["vendor"]
                current.version = current.version or info["version"]
                expires = parse_expiry(info["expiry"])
                # plusieurs lignes (pools) : on retient l'échéance la plus proche
                if expires and (current.expires is None or expires < current.expires):
                    current.expires = expires
            continue

        co = _CHECKOUT_RE.match(line)
        if co:
            user, host, display = _split_ident(co["ident"])
            count_m = _COUNT_RE.search(co["rest"] or "")
            current.checkouts.append(
                Checkout(
                    feature=current.name,
                    user=user,
                    host=host,
                    display=display,
                    version=co["version"][1:],
                    handle=co["handle"],
                    start_time=_infer_start(queried_at, co),
                    count=int(count_m["count"]) if count_m else 1,
                )
            )
            continue

        qm = _QUEUED_RE.match(line)
        if qm:
            current.queued += int(qm["count"])
            continue

    if status.features:
        status.server_up = True
    return status


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


@register("flexlm", "lmstat")
class LmstatCollector(StatusCollector):
    """Exécute ``lmutil lmstat -a -c <port@host>`` et parse le résultat."""

    def command(self) -> list[str]:
        lmutil = self.server.lmutil_path or self.app.lmutil_path
        return [lmutil, "lmstat", "-a", "-c", self.server.port_at_host]

    def run_lmstat(self) -> str:
        cmd = self.command()
        log.debug("Exécution : %s", " ".join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=self.server.timeout_s, check=False)
        except FileNotFoundError:
            raise CollectorError(f"lmutil introuvable : {cmd[0]}") from None
        except subprocess.TimeoutExpired:
            raise CollectorError(
                f"lmstat {self.server.port_at_host} : délai dépassé ({self.server.timeout_s}s)"
            ) from None
        # lmutil retourne parfois un code non nul avec une sortie exploitable :
        # on se fie au contenu plutôt qu'au code retour.
        return _decode(proc.stdout) + "\n" + _decode(proc.stderr)

    def poll(self) -> ServerStatus:
        now = datetime.now().replace(microsecond=0)
        output = self.run_lmstat()
        status = parse_lmstat(output, self.server.port_at_host, queried_at=now)
        if not status.server_up:
            detail = "; ".join(status.errors) or output.strip()[-500:]
            raise CollectorError(f"Serveur {self.server.port_at_host} injoignable : {detail}")
        return status
