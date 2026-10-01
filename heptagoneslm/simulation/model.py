"""Modèle de simulation de serveurs de licences FlexLM.

L'activité est une **fonction déterministe du temps** : pour un serveur, une
feature et un jour donnés, les sessions (et les refus) sont générées avec un
générateur pseudo-aléatoire initialisé sur (serveur, feature, jour). On peut
donc demander l'état à n'importe quel instant, dans n'importe quel ordre, et
obtenir toujours la même réponse : l'historique rejoué par ``heptagoneslm demo`` et
les réponses « en direct » du faux ``lmutil`` sont cohérents entre eux.

Les sorties produites (texte ``lmstat -a`` et lignes de log debug) imitent le
format FlexNet réel et sont analysées par les vrais parsers de HeptagonesLM.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass(frozen=True)
class SimFeature:
    name: str
    issued: int
    product: str
    publisher: str
    pool_size: int            # nb d'utilisateurs susceptibles d'utiliser la feature
    daily_prob: float         # probabilité d'usage un jour ouvré, par utilisateur du pool
    mean_hours: float         # durée moyenne d'une session
    counts: tuple[int, ...] = (1,)  # nb de jetons par checkout (ex. HPC)
    annual_cost: float | None = None  # coût annuel d'un jeton (démonstration)
    reservations: tuple[tuple[str, str, int], ...] = ()   # (USER|HOST|GROUP, bénéficiaire, jetons)
    borrow_share: int = 0     # 1 utilisateur sur N emprunte la licence (utilisation hors ligne)


@dataclass(frozen=True)
class SimServer:
    name: str
    host: str
    port: int
    vendor: str
    features: tuple[SimFeature, ...]
    with_log: bool = False    # le serveur expose un log debug (collecteur "log")

    @property
    def port_at_host(self) -> str:
        return f"{self.port}@{self.host}"


@dataclass(frozen=True)
class SimSession:
    feature: str
    user: str
    host: str
    start: datetime
    end: datetime
    count: int
    handle: int


@dataclass(frozen=True)
class SimDenial:
    feature: str
    user: str
    host: str
    when: datetime


_FIRST = ["alice", "bruno", "chloe", "david", "emma", "fabien", "gael", "hugo", "ines", "julien",
          "karim", "laura", "marc", "nadia", "olivier", "pauline", "quentin", "rachid", "sophie"]
_LAST = ["martin", "bernard", "dubois", "thomas", "robert", "richard", "petit", "durand", "leroy",
         "moreau", "simon", "laurent", "lefebvre", "michel", "garcia"]


def _make_users(n: int = 45) -> tuple[str, ...]:
    rng = random.Random("heptagoneslm-users")
    names: list[str] = []
    while len(names) < n:
        name = f"{rng.choice(_FIRST)}.{rng.choice(_LAST)}"
        if name not in names:
            names.append(name)
    return tuple(names)


USERS = _make_users()


DEPARTMENTS = ("Bureau d'études mécanique", "Simulation & calcul", "Méthodes industrialisation",
               "R&D électronique", "Architecture & BIM")


def department_of(user: str) -> str:
    return DEPARTMENTS[zlib.crc32(("dep|" + user).encode()) % len(DEPARTMENTS)]


def display_name_of(user: str) -> str:
    return " ".join(part.capitalize() for part in user.split("."))


def host_of(user: str) -> str:
    return f"PC-{zlib.crc32(user.encode()) % 9000 + 1000}"


DEFAULT_SCENARIO: tuple[SimServer, ...] = (
    SimServer(
        name="AUTODESK", host="licsrv-adsk", port=27000, vendor="adskflex",
        features=(
            SimFeature("87545ACD_2024_0F", 12, "AutoCAD 2024", "Autodesk", 26, 0.55, 3.0, annual_cost=2300),
            SimFeature("87048INVPROSA_2024_0F", 6, "Inventor Professional 2024", "Autodesk", 12, 0.6, 4.0, annual_cost=3900),
            SimFeature("87706RVT_2024_0F", 3, "Revit 2024", "Autodesk", 6, 0.45, 3.5, annual_cost=3400),
            # Licence achetée mais jamais utilisée : vue « Licences inutilisées »
            SimFeature("87763MAYA_2024_0F", 2, "Maya 2024", "Autodesk", 0, 0.0, 1.0, annual_cost=2100),
        ),
    ),
    SimServer(
        name="ANSYS", host="licsrv-ansys", port=1055, vendor="ansyslmd", with_log=True,
        features=(
            SimFeature("ansys", 4, "Ansys Mechanical Enterprise", "Ansys", 10, 0.5, 3.0, annual_cost=28000),
            SimFeature("cfd_base", 2, "Ansys Fluent", "Ansys", 5, 0.45, 5.0, annual_cost=24000),
            SimFeature("a_spaceclaim_dirmod", 3, "Ansys SpaceClaim", "Ansys", 8, 0.35, 1.5, annual_cost=4500),
            SimFeature("anshpc", 32, "Ansys HPC", "Ansys", 6, 0.4, 6.0, counts=(4, 8, 8, 16), annual_cost=900),
        ),
    ),
    SimServer(
        name="MATLAB", host="licsrv-mlm", port=27000, vendor="MLM",
        features=(
            SimFeature("MATLAB", 10, "MATLAB", "MathWorks", 18, 0.5, 2.5, annual_cost=2700,
                       reservations=(("GROUP", "simulation", 2),), borrow_share=6),
            SimFeature("SIMULINK", 4, "Simulink", "MathWorks", 8, 0.4, 3.0, annual_cost=3600,
                       reservations=(("USER", "USER0", 1),)),
            SimFeature("Optimization_Toolbox", 2, "MATLAB Toolboxes", "MathWorks", 6, 0.3, 1.5, annual_cost=1200),
            SimFeature("Signal_Toolbox", 3, "MATLAB Toolboxes", "MathWorks", 6, 0.3, 1.5, annual_cost=1200),
        ),
    ),
)


def find_server(port_at_host: str, scenario=DEFAULT_SCENARIO) -> SimServer | None:
    wanted = port_at_host.lower()
    for srv in scenario:
        if srv.port_at_host.lower() == wanted:
            return srv
    return None


def _pool(server: SimServer, feature: SimFeature) -> list[str]:
    rng = random.Random(f"pool|{server.name}|{feature.name}")
    return sorted(rng.sample(USERS, min(feature.pool_size, len(USERS))))


def _hours(day: date, h: float) -> datetime:
    return datetime.combine(day, time()) + timedelta(seconds=int(h * 3600))


@lru_cache(maxsize=4096)
def day_activity(server: SimServer, feature: SimFeature, day: date) -> tuple[tuple[SimSession, ...], tuple[SimDenial, ...]]:
    """Sessions acceptées et refus d'une feature sur une journée.

    Les sessions se terminent toujours avant 23h30 le jour même : l'occupation
    des jetons d'un jour ne dépend ainsi d'aucun autre jour (déterminisme).
    """
    rng = random.Random(f"{server.name}|{feature.name}|{day.isoformat()}")
    factor = 1.0 if day.weekday() < 5 else 0.04
    latest = _hours(day, 23.5)
    candidates: list[tuple[datetime, datetime, str, int]] = []
    for user in _pool(server, feature):
        if rng.random() >= feature.daily_prob * factor:
            continue
        start = _hours(day, min(max(rng.gauss(9.0, 0.8), 6.5), 12.0))
        for _ in range(2):  # matin, puis éventuellement après-midi
            duration = max(0.2, rng.expovariate(1 / feature.mean_hours))
            end = min(start + timedelta(seconds=int(duration * 3600)), latest)
            candidates.append((start, end, user, rng.choice(feature.counts)))
            if end.hour >= 15 or rng.random() > 0.45:
                break
            start = max(end + timedelta(minutes=rng.randint(20, 90)), _hours(day, 13.5))
            if start >= latest:
                break

    sessions: list[SimSession] = []
    denials: list[SimDenial] = []
    for start, end, user, count in sorted(candidates):
        in_use = sum(s.count for s in sessions if s.start <= start < s.end)
        if in_use + count > feature.issued:
            denials.append(SimDenial(feature.name, user, host_of(user), start))
            continue
        handle = zlib.crc32(f"{feature.name}|{user}|{start.isoformat()}".encode()) % 90000 + 1000
        sessions.append(SimSession(feature.name, user, host_of(user), start, end, count, handle))
    return tuple(sessions), tuple(denials)


def active_sessions(server: SimServer, feature: SimFeature, when: datetime) -> list[SimSession]:
    sessions, _ = day_activity(server, feature, when.date())
    return [s for s in sessions if s.start <= when < s.end]


# ---------------------------------------------------------------------------
# Rendus au format FlexNet
# ---------------------------------------------------------------------------


def _mdy(d: date | datetime) -> str:
    return f"{d.month}/{d.day}/{d.year}"


_MONTHS_EN = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _expiry(feature: SimFeature, when: datetime) -> str:
    """Échéances variées pour la démonstration : permanentes, fin d'année, et
    quelques contrats qui arrivent à terme dans les semaines à venir."""
    rank = zlib.crc32(feature.name.encode()) % 5
    if rank == 0:
        return "permanent(no expiration date)"
    if rank == 1:  # échéance glissante : toujours dans ~3 semaines
        d = (when + timedelta(days=21 + zlib.crc32(feature.name.encode()) % 10)).date()
    elif rank == 2:
        d = (when + timedelta(days=60)).date().replace(day=1)
    else:
        d = date(when.year + 1, 12, 31)
    return f"{d.day}-{_MONTHS_EN[d.month - 1]}-{d.year}"


def render_lmstat(server: SimServer, when: datetime) -> str:
    """Sortie de ``lmutil lmstat -a -c port@host`` à l'instant ``when``."""
    out = [
        "lmutil - Copyright (c) 1989-2023 Flexera. All Rights Reserved.",
        f"Flexible License Manager status on {DOW[when.weekday()]} {_mdy(when)} {when.hour:02d}:{when.minute:02d}",
        "",
        "[Detecting lmgrd processes...]",
        f"License server status: {server.port_at_host}",
        f"    License file(s) on {server.host}: C:\\FlexLM\\{server.name.lower()}.lic:",
        "",
        f"{server.host}: license server UP (MASTER) v11.19.1",
        "",
        f"Vendor daemon status (on {server.host}):",
        "",
        f"  {server.vendor}: UP v11.19.1",
        "Feature usage info:",
        "",
    ]
    for f in server.features:
        active = active_sessions(server, f, when)
        used = sum(s.count for s in active)
        lic = lambda n: "license" if n == 1 else "licenses"  # noqa: E731
        out.append(
            f"Users of {f.name}:  (Total of {f.issued} {lic(f.issued)} issued;  "
            f"Total of {used} {lic(used)} in use)"
        )
        out.append("")
        out.append(f'  "{f.name}" v2024.0, vendor: {server.vendor}, expiry: {_expiry(f, when)}')
        out.append("  floating license")
        out.append("")
        for kind, name, count in f.reservations:
            name = _pool(server, f)[0] if name == "USER0" else name     # 1er utilisateur du pool
            out.append(f"    {count} RESERVATION{'s' if count > 1 else ''} for {kind} {name} ({server.host}/{server.port})")
        if active:
            for s in active:
                line = (
                    f"    {s.user} {s.host} {s.host} (v2024.0) ({server.host}/{server.port} {s.handle}), "
                    f"start {DOW[s.start.weekday()]} {s.start.month}/{s.start.day} {s.start.hour}:{s.start.minute:02d}"
                )
                if s.count > 1:
                    line += f", {s.count} licenses"
                if f.borrow_share and zlib.crc32(s.user.encode()) % f.borrow_share == 0:
                    line += " (linger: 1209600 / 604800)"
                out.append(line)
            out.append("")
    return "\n".join(out) + "\n"


def _log_time(t: datetime) -> str:
    return f"{t.hour:2d}:{t.minute:02d}:{t.second:02d}"


def log_lines(server: SimServer, after: datetime, until: datetime) -> list[str]:
    """Lignes de log debug des évènements survenus dans ]after, until]."""
    events: list[tuple[datetime, int, str]] = []
    day = after.date()
    while day <= until.date():
        midnight = datetime.combine(day, time())
        if after < midnight <= until:
            events.append((midnight, 0, f"{_log_time(midnight)} (lmgrd) TIMESTAMP {_mdy(day)}"))
        for f in server.features:
            sessions, denials = day_activity(server, f, day)
            suffix = lambda s: f"  ({s.count} licenses)" if s.count > 1 else ""  # noqa: E731
            for s in sessions:
                events.append((s.start, 1, f'{_log_time(s.start)} ({server.vendor}) OUT: "{f.name}" {s.user}@{s.host}{suffix(s)}'))
                events.append((s.end, 2, f'{_log_time(s.end)} ({server.vendor}) IN: "{f.name}" {s.user}@{s.host}{suffix(s)}'))
            for d in denials:
                events.append((
                    d.when, 3,
                    f'{_log_time(d.when)} ({server.vendor}) DENIED: "{f.name}" {d.user}@{d.host}  '
                    '(Licensed number of users already reached. (-4,342:10054 ""))',
                ))
        day += timedelta(days=1)
    return [line for when, _, line in sorted(events) if after < when <= until]


def sync_log(server: SimServer, path: Path, until: datetime, start: datetime | None = None) -> int:
    """Complète le fichier de log simulé jusqu'à ``until``. Retourne le nb de lignes ajoutées.

    La position de simulation est mémorisée dans ``<log>.sim`` ; à la création,
    le log commence par la ligne de démarrage de lmgrd (qui porte la date).
    """
    path = Path(path)
    state = path.with_name(path.name + ".sim")
    lines: list[str] = []
    if state.exists() and path.exists():
        after = datetime.fromisoformat(state.read_text().strip())
    else:
        after = start or until
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        lines += [
            f"{_log_time(after)} (lmgrd) -----------------------------------------------",
            f"{_log_time(after)} (lmgrd) FlexNet Licensing (v11.19.1.0 build 297441 x64_n6) started on "
            f"{server.host} (IBM PC) ({_mdy(after)})",
            f"{_log_time(after)} ({server.vendor}) Server started on {server.host} for: "
            + " ".join(f.name for f in server.features),
        ]
    if until <= after and not lines:
        return 0
    lines += log_lines(server, after, until)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(line + "\n")
    state.write_text(max(after, until).isoformat())
    return len(lines)


def render_rlmstat(port_at_host: str, when: datetime | None = None) -> str:
    """Sortie ``rlmstat -a`` d'un serveur RLM fictif (assistant d'installation en simulation)."""
    when = when or datetime.now().replace(microsecond=0)
    port, _, host = port_at_host.partition("@")
    host, port = host or "licsrv-rlm", port or "5053"
    day = f"{when:%m/%d}"
    return (
        "rlmutil v14.2\nCopyright (C) 2006-2021, Reprise Software, Inc. All rights reserved.\n\n"
        f"\trlm status on {host} (port {port}), up 12d 04:10:02\n"
        "\trlm software version v14.2 (build:2)\n\n"
        "\t--------- ISV servers ----------\n\t   Name           Port Running Restarts\n"
        "\tfoundry          62137     Yes        0\n\n\t------------------------\n\n"
        f"\tfoundry license pool status on {host} (port 62137)\n\n"
        f"\tnuke_i v{when.year}.0\n"
        f"\t\tcount: 10, # reservations: 0, inuse: 2, exp: 31-dec-{when.year + 1}\n"
        "\t\tobsolete: 0, min_remove: 120, total checkouts: 57\n"
        f"\tnukex_i v{when.year}.0\n"
        "\t\tcount: 4, # reservations: 0, inuse: 1, exp: permanent\n"
        "\t\tobsolete: 0, min_remove: 120, total checkouts: 12\n\n\t------------------------\n\n"
        f"\tfoundry license usage status on {host} (port 62137)\n\n"
        f"\tnuke_i v{when.year}.0: alice.martin@PC-ALICE 1/0 at {day} 08:12  (handle: 41)\n"
        f"\tnuke_i v{when.year}.0: bruno.bernard@PC-BRUNO 1/0 at {day} 09:40  (handle: 42)\n"
        f"\tnukex_i v{when.year}.0: chloe.dubois@PC-CHLOE 1/0 at {day} 10:05  (handle: 43)\n"
    )
