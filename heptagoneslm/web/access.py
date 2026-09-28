"""Test d'accès depuis un poste utilisateur (page ``/acces``).

Un utilisateur ouvre ``https://<site>/acces`` depuis son poste : la page affiche le nom sous
lequel HeptagonesLM le reconnaît et la méthode d'authentification (Kerberos, NTLM…), puis
enregistre le résultat avec sa réponse à « Avez-vous dû saisir un mot de passe ? ». La
vérification finale de l'assistant d'installation lit ces résultats (``logs/tests-acces.jsonl``).
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from datetime import datetime
from pathlib import Path

FILE_NAME = "tests-acces.jsonl"
MAX_LINES = 200
METHOD_HEADER = "X-Remote-Auth-Method"     # posé par le module IIS HeptagonesLMUserHeader
METHOD_TTL = 3600                          # s : méthode mémorisée par utilisateur

_lock = threading.Lock()
_methods: dict[str, tuple[str, float]] = {}


def results_path(log_file: str | None) -> Path:
    """Fichier des résultats, à côté du journal de l'application."""
    return (Path(log_file).parent if log_file else Path("logs")) / FILE_NAME


def remember_method(username: str, method: str | None) -> None:
    """Mémorise la méthode d'authentification vue pour un utilisateur.

    IIS n'authentifie qu'au début d'une connexion (Kerberos et NTLM persistent sur la
    connexion) : l'en-tête n'est présent que sur la première requête de chaque connexion.
    """
    if method in ("Kerberos", "NTLM"):
        with _lock:
            _methods[username] = (method, time.monotonic())


def last_method(username: str) -> str | None:
    with _lock:
        seen = _methods.get(username)
    if seen and time.monotonic() - seen[1] < METHOD_TTL:
        return seen[0]
    return None


def browser_of(user_agent: str) -> str:
    ua = user_agent or ""
    for token, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"),
                        ("Safari/", "Safari")):
        if token in ua:
            return name
    return "Navigateur inconnu"


def workstation_of(ip: str, timeout: float = 1.5) -> str | None:
    """Nom DNS du poste (résolution inverse, au mieux, sans bloquer la requête)."""
    if not ip or ip in ("127.0.0.1", "::1"):
        return None
    result: list[str] = []

    def lookup():
        try:
            result.append(socket.gethostbyaddr(ip)[0])
        except OSError:
            pass
    t = threading.Thread(target=lookup, daemon=True)
    t.start()
    t.join(timeout)
    return result[0].split(".")[0].upper() if result else None


def client_address(headers, peer_ip: str, trusted: bool) -> str:
    """Adresse du poste : dernière valeur de X-Forwarded-For (ajoutée par ARR) derrière le proxy."""
    if trusted:
        forwarded = [p.strip() for p in (headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
        if forwarded:
            value = forwarded[-1]
            m = re.fullmatch(r"\[([0-9a-fA-F:.]+)\](?::\d+)?", value)      # [IPv6]:port
            if m:
                return m.group(1)
            return value.rsplit(":", 1)[0] if value.count(":") == 1 else value   # IPv4:port
    return peer_ip


def record(path: Path, entry: dict) -> None:
    """Ajoute un résultat (fichier JSON Lines limité aux MAX_LINES derniers)."""
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        lines.append(json.dumps(entry, ensure_ascii=False))
        path.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")


def parse_results(text: str | None) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("user"):
            out.append(item)
    return out


def latest_by_user(results: list[dict], limit: int = 10) -> list[dict]:
    """Dernier résultat de chaque utilisateur, du plus récent au plus ancien."""
    seen: dict[str, dict] = {}
    for item in results:
        seen[str(item["user"]).lower()] = item
    return sorted(seen.values(), key=lambda i: i.get("at", ""), reverse=True)[:limit]


def make_entry(user: str, display_name: str | None, method: str, client: str, workstation: str | None,
               browser: str, prompted) -> dict:
    return {"at": datetime.now().isoformat(timespec="seconds"), "user": user, "display_name": display_name,
            "method": method, "client": client, "workstation": workstation, "browser": browser,
            "prompted": prompted if prompted in (True, False) else None}
