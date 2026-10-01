"""Recherche de lmutil.exe / rlmutil.exe sur ce serveur et sur les serveurs de licences.

L'outil se trouve presque toujours dans le dossier du démon de licences (``lmgrd.exe``
pour FlexLM, ``rlm.exe`` pour RLM). La recherche, par ordre de fiabilité :

1. processus en cours d'exécution (démons, outils) : dossier de l'exécutable ;
2. services Windows dont la commande lance ``lmgrd.exe`` ou ``rlm.exe`` ;
3. dossiers d'installation connus (Autodesk, ANSYS, MATLAB, Siemens…) ;
4. recherche dans Program Files et à la racine de C:, profondeur limitée.

Un serveur distant est examiné par WMI/CIM et par son partage administratif ``\\\\serveur\\C$``
(droits d'administrateur du serveur de licences nécessaires). Un outil trouvé à distance est
ensuite copié sur ce serveur (``<installation>\\bin``) : le compte de service n'a pas à accéder
au partage administratif.
"""

from __future__ import annotations

import re

from heptagoneslm.setup.iis import ps_quote

TOOLS = {"lmutil": "lmutil.exe", "rlmutil": "rlmutil.exe"}

# Dossiers connus (sans le nom de l'outil) ; * accepté
KNOWN_DIRS = [
    r"C:\Program Files\Autodesk\Network License Manager",
    r"C:\Program Files (x86)\Autodesk\Network License Manager",
    r"C:\Autodesk\Network License Manager",
    r"C:\Program Files\ANSYS Inc\Shared Files\Licensing\winx64",
    r"C:\Program Files\ANSYS Inc\Shared Files\Licensing\win64",
    r"C:\Program Files\MATLAB\*\etc\win64",
    r"C:\Program Files\MATLAB\*\bin\win64",
    r"C:\Program Files\Siemens\PLMLicenseServer",
    r"C:\Program Files\Siemens\LicenseServer",
    r"C:\Program Files\PTC\FLEXnet Admin License Server\bin",
    r"C:\Program Files\Foundry\LicensingTools*\bin\RLM",
    r"C:\Program Files\The Foundry\LicensingTools*\bin\RLM",
    r"C:\Program Files\Reprise\rlm",
    r"C:\Program Files\RLM",
    r"C:\Flexlm",
    r"C:\FlexLM",
    r"C:\RLM",
    r"C:\HeptagonesLM\bin",
]
# (racine, profondeur) de la recherche de dernier recours
SEARCH_ROOTS = [(r"C:\Program Files", 5), (r"C:\Program Files (x86)", 5), ("C:\\", 2)]
DAEMONS = ["lmgrd.exe", "rlm.exe", "lmutil.exe", "rlmutil.exe", "adskflex.exe", "mlm.exe", "ansyslmd.exe"]

_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,252}$")


def hosts_of(servers) -> list[str]:
    """Noms des serveurs de licences (triades comprises), sans doublon."""
    out: list[str] = []
    for s in servers:
        for part in str(getattr(s, "port_at_host", "") or "").split(":"):
            host = part.split("@", 1)[-1].strip() if "@" in part else ""
            if host and _HOST_RE.match(host) and host.lower() not in {h.lower() for h in out}:
                out.append(host)
    return out


def script(hosts: list[str]) -> str:
    """Script PowerShell : liste JSON de {host, local, tool, path, how}."""
    host_list = ", ".join(ps_quote(h) for h in [""] + hosts)
    dirs = ", ".join(ps_quote(d) for d in KNOWN_DIRS)
    roots = ", ".join(f"@({ps_quote(r)}, {d})" for r, d in SEARCH_ROOTS)
    daemons = ", ".join(ps_quote(d) for d in DAEMONS)
    return f"""
$ErrorActionPreference = 'SilentlyContinue'
$tools = @{{ 'lmutil.exe' = 'lmutil'; 'rlmutil.exe' = 'rlmutil' }}
$daemons = @({daemons})
$me = @($env:COMPUTERNAME, 'localhost', '127.0.0.1', '.')
try {{ $me += [System.Net.Dns]::GetHostEntry('').HostName }} catch {{ }}
$me = @($me | ForEach-Object {{ "$_".ToLower() }})
$out = New-Object System.Collections.ArrayList
$seen = @{{}}
function Add-Tool($h, $local, $path, $how) {{
  $k = "$path".ToLower(); if ($seen.ContainsKey($k)) {{ return }}; $seen[$k] = 1
  $leaf = (Split-Path $path -Leaf).ToLower()
  if (-not $tools.ContainsKey($leaf)) {{ return }}
  [void]$out.Add([pscustomobject]@{{ host = $h; local = $local; tool = $tools[$leaf]; path = "$path"; how = $how }})
}}
function To-Path($h, $local, $p) {{
  if ($local -or $p -notmatch '^[A-Za-z]:') {{ return $p }}
  return '\\\\' + $h + '\\' + $p.Substring(0, 1) + '$' + $p.Substring(2)
}}
function Probe-Dir($h, $local, $dir, $how) {{
  foreach ($d in @(Get-Item -Path (To-Path $h $local $dir) -ErrorAction SilentlyContinue)) {{
    foreach ($n in $tools.Keys) {{
      $p = Join-Path $d.FullName $n
      if (Test-Path -LiteralPath $p -PathType Leaf) {{ Add-Tool $h $local $p $how }}
    }}
  }}
}}
foreach ($h in @({host_list})) {{
  $local = ($h -eq '') -or ($me -contains $h.ToLower()) -or ($me -contains $h.Split('.')[0].ToLower())
  if ($h -eq '') {{ $h = $env:COMPUTERNAME }} elseif ($local) {{ continue }}
  if (-not $local) {{
    try {{ [void][System.Net.Dns]::GetHostAddresses($h) }}
    catch {{ [void]$out.Add([pscustomobject]@{{ host = $h; problem = 'nom introuvable (DNS)' }}); continue }}
    if (-not (Test-Path -LiteralPath ('\\\\' + $h + '\\C$'))) {{
      [void]$out.Add([pscustomobject]@{{ host = $h; problem = 'partage C$ inaccessible (droits d''administrateur de ce serveur nécessaires)' }})
      continue
    }}
  }}
  $cim = @{{ OperationTimeoutSec = 20; ErrorAction = 'Stop' }}
  if (-not $local) {{ $cim.ComputerName = $h }}
  try {{
    Get-CimInstance Win32_Process @cim | Where-Object {{ $_.ExecutablePath -and ($daemons -contains $_.Name.ToLower()) }} |
      ForEach-Object {{ Probe-Dir $h $local (Split-Path $_.ExecutablePath) ('processus ' + $_.Name) }}
  }} catch {{ }}
  try {{
    Get-CimInstance Win32_Service @cim | Where-Object {{ $_.PathName -match '(lmgrd|rlm)\\.exe' }} | ForEach-Object {{
      if ($_.PathName -match '^\\s*"?([A-Za-z]:\\\\[^"]*?)\\\\(lmgrd|rlm)\\.exe') {{ Probe-Dir $h $local $Matches[1] ('service ' + $_.Name) }}
    }}
  }} catch {{ }}
  foreach ($d in @({dirs})) {{ Probe-Dir $h $local $d 'dossier connu' }}
  foreach ($r in @({roots})) {{
    $root = To-Path $h $local $r[0]
    if (-not (Test-Path -LiteralPath $root)) {{ continue }}
    Get-ChildItem -LiteralPath $root -Recurse -Depth $r[1] -File -Filter '*util.exe' -ErrorAction SilentlyContinue |
      Where-Object {{ $tools.ContainsKey($_.Name.ToLower()) }} | ForEach-Object {{ Add-Tool $h $local $_.FullName 'recherche' }}
  }}
}}
ConvertTo-Json -InputObject @($out) -Compress
"""


def normalize(rows) -> dict:
    """Résultats du script : outils dédoublonnés et triés (ce serveur d'abord, puis les plus fiables),
    et serveurs non examinés avec la raison."""
    rank = {"processus": 0, "service": 1, "dossier connu": 2, "recherche": 3}
    found, problems, seen = [], [], set()
    for r in rows if isinstance(rows, list) else ([rows] if rows else []):
        if r.get("problem"):
            problems.append({"host": str(r.get("host") or ""), "problem": str(r["problem"])})
            continue
        tool, path = str(r.get("tool") or ""), str(r.get("path") or "")
        if tool not in TOOLS or not path or path.lower() in seen:
            continue
        seen.add(path.lower())
        found.append({"tool": tool, "path": path, "host": str(r.get("host") or ""), "local": bool(r.get("local")),
                      "how": str(r.get("how") or "")})
    found.sort(key=lambda r: (not r["local"], rank.get(r["how"].split(" ")[0], 9)))
    return {"found": found, "problems": problems}
