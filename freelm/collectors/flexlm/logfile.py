"""Lecture incrémentale et parsing du log debug FlexLM (lmgrd / démon vendeur).

Lignes exploitées ::

     8:15:32 (lmgrd) FlexNet Licensing (v11.16.2.0 build 242433 x64_n6) started on licsrv1 (IBM PC) (9/23/2024)
     8:15:40 (adskflex) OUT: "87545ACD_2024_0F" jdoe@PC123
     9:12:05 (adskflex) IN: "87545ACD_2024_0F" jdoe@PC123
     9:15:00 (adskflex) DENIED: "87545ACD_2024_0F" bob@PC999  (Licensed number of users already reached. (-4,342:10054 ""))
     9:20:00 (MLM) OUT: "MATLAB" jdoe@PC123  (2 licenses)
     0:03:12 (lmgrd) TIMESTAMP 9/24/2024
    18:00:00 (adskflex) EXITING DUE TO SIGNAL 15 Exit reason 2

Les lignes du log classique ne portent que l'heure : la date est déduite des
lignes ``TIMESTAMP`` et ``started on ... (M/D/YYYY)``, et du passage de
minuit (l'heure qui recule). Les logs dont chaque ligne commence par une
date ``M/D/YYYY`` sont également supportés.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import date, datetime, time, timedelta
from typing import Iterable

from freelm.collectors.base import CollectorError, LogCollector, register
from freelm.models import EventType, LicenseEvent

log = logging.getLogger(__name__)

_LINE_RE = re.compile(
    r"^\s*(?:(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\s+)?"
    r"(?P<time>\d{1,2}:\d{2}:\d{2})\s+\((?P<daemon>[^)]+)\)\s+(?P<msg>.*?)\s*$"
)
_TIMESTAMP_RE = re.compile(r"^TIMESTAMP\s+(?P<date>\d{1,2}/\d{1,2}/\d{2,4})")
_STARTED_RE = re.compile(r"started on .*\((?P<date>\d{1,2}/\d{1,2}/\d{4})\)")
_EVENT_RE = re.compile(
    r'^(?P<type>OUT|IN|DENIED|QUEUED|DEQUEUED|UNSUPPORTED):\s+"(?P<feature>[^"]+)"\s+'
    r"(?P<ident>[^\s(\[][^(\[]*?)\s*(?P<rest>[(\[].*)?$"
)
_COUNT_RE = re.compile(r"^\((?P<count>\d+) licenses?\)")
_SHUTDOWN_RE = re.compile(r"(EXITING DUE TO SIGNAL|Shutting down)", re.IGNORECASE)

# Recul d'heure au-delà duquel on considère un passage de minuit.
_ROLLOVER_THRESHOLD = timedelta(hours=2)
_HEAD_BYTES = 256


def _parse_mdy(value: str) -> date:
    m, d, y = (int(x) for x in value.split("/"))
    if y < 100:
        y += 2000
    return date(y, m, d)


class FlexlmLogParser:
    """Parse des lignes de log en conservant le contexte de date entre elles."""

    def __init__(self, current_date: date | None = None, last_time: time | None = None):
        self.current_date = current_date
        self.last_time = last_time
        self.skipped_undated = 0

    def _advance(self, t: time, explicit_date: date | None) -> datetime | None:
        if explicit_date is not None:
            self.current_date = explicit_date
        elif self.current_date is not None and self.last_time is not None:
            delta = datetime.combine(date.min, self.last_time) - datetime.combine(date.min, t)
            if delta > _ROLLOVER_THRESHOLD:
                self.current_date += timedelta(days=1)
        self.last_time = t
        if self.current_date is None:
            return None
        return datetime.combine(self.current_date, t)

    def parse_line(self, line: str) -> LicenseEvent | None:
        m = _LINE_RE.match(line)
        if not m:
            return None
        msg = m["msg"]
        daemon = m["daemon"].strip()
        t = time.fromisoformat(m["time"].zfill(8))

        explicit = None
        if m["date"]:
            explicit = _parse_mdy(m["date"])
        else:
            ts = _TIMESTAMP_RE.match(msg) or _STARTED_RE.search(msg)
            if ts:
                explicit = _parse_mdy(ts["date"])

        when = self._advance(t, explicit)

        ev = _EVENT_RE.match(msg)
        shutdown = ev is None and _SHUTDOWN_RE.search(msg)
        if not ev and not shutdown:
            return None
        if when is None:
            self.skipped_undated += 1
            return None

        if shutdown:
            return LicenseEvent(
                event_time=when,
                event_type=EventType.SERVER_DOWN,
                vendor=None if daemon.lower() == "lmgrd" else daemon,
                detail=msg[:500],
            )

        ident = ev["ident"].strip()
        user, _, host = ident.rpartition("@")
        if not user:  # pas de "@" : on garde tout comme utilisateur
            user, host = ident, ""
        rest = (ev["rest"] or "").strip()
        count = 1
        cm = _COUNT_RE.match(rest)
        if cm:
            count = int(cm["count"])
        return LicenseEvent(
            event_time=when,
            event_type=EventType(ev["type"]),
            feature=ev["feature"],
            user=user,
            host=host,
            vendor=daemon,
            count=count,
            detail=rest[:500] or None,
        )

    def parse_lines(self, lines: Iterable[str]) -> list[LicenseEvent]:
        events = []
        for line in lines:
            e = self.parse_line(line)
            if e is not None:
                events.append(e)
        return events

    def state(self) -> dict:
        return {
            "date": self.current_date.isoformat() if self.current_date else None,
            "last_time": self.last_time.isoformat() if self.last_time else None,
        }

    @classmethod
    def from_state(cls, state: dict) -> "FlexlmLogParser":
        d = state.get("date")
        t = state.get("last_time")
        return cls(date.fromisoformat(d) if d else None, time.fromisoformat(t) if t else None)


def _head_signature(path: str, length: int) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha1(fh.read(length)).hexdigest()


def _decode_line(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


@register("flexlm", "log")
class FlexlmLogCollector(LogCollector):
    """Suit un log debug FlexLM entre deux exécutions (offset + détection de rotation)."""

    def read_new_events(self, state: dict) -> tuple[list[LicenseEvent], dict]:
        path = self.server.log_path
        if not path or not os.path.exists(path):
            raise CollectorError(f"Log introuvable : {path}")
        return read_log_incremental(path, state)


def read_log_incremental(path: str, state: dict) -> tuple[list[LicenseEvent], dict]:
    """Lit ``path`` à partir de l'état précédent et retourne (évènements, nouvel état).

    L'état contient l'offset, une signature du début du fichier (pour
    détecter une rotation/recréation) et le contexte de date du parser.
    Seules les lignes complètes (terminées par un saut de ligne) sont lues.
    """
    size = os.path.getsize(path)
    offset = int(state.get("offset", 0))
    head_len = int(state.get("head_len", 0))
    parser = FlexlmLogParser.from_state(state)

    rotated = size < offset
    if not rotated and head_len and _head_signature(path, head_len) != state.get("head"):
        rotated = True
    if rotated:
        log.info("Rotation détectée sur %s : relecture depuis le début", path)
        offset = 0
        # On garde le contexte de date : un log recréé commence en général
        # par une ligne "started on ... (date)" qui le corrigera.

    events: list[LicenseEvent] = []
    with open(path, "rb") as fh:
        fh.seek(offset)
        while True:
            raw = fh.readline()
            if not raw or not raw.endswith(b"\n"):
                break  # fin de fichier ou ligne en cours d'écriture
            offset += len(raw)
            e = parser.parse_line(_decode_line(raw).rstrip("\r\n"))
            if e is not None:
                events.append(e)

    if parser.skipped_undated:
        log.warning(
            "%s : %d évènement(s) ignoré(s) faute de date connue (aucune ligne TIMESTAMP/started lue)",
            path,
            parser.skipped_undated,
        )
    new_head_len = min(_HEAD_BYTES, size)
    new_state = {
        "offset": offset,
        "head_len": new_head_len,
        "head": _head_signature(path, new_head_len),
        **parser.state(),
    }
    return events, new_state
