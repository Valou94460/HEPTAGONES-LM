"""Actions système de l'installation : Windows réel ou simulation.

``WindowsSystem`` exécute PowerShell (``-EncodedCommand`` : aucun problème de
guillemets), ``icacls``, le Planificateur de tâches, IIS, le pare-feu et SQL Server.
``SimulatedSystem`` reproduit les mêmes réponses sans rien modifier (démonstration,
captures d'écran, tests automatisés) ; ses fichiers sont écrits dans un bac à sable.

Les mots de passe ne figurent jamais sur une ligne de commande : ils sont transmis
aux scripts PowerShell par une variable d'environnement du seul processus enfant.
"""

from __future__ import annotations

import base64
import glob
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from heptagoneslm.setup import iis
from heptagoneslm.setup.answers import Answers
from heptagoneslm.setup.iis import ps_quote

SECRET_ENV = "HEPTAGONESLM_SETUP_SECRET"
TASK_FOLDER = "\\HeptagonesLM\\"

LMUTIL_CANDIDATES = [
    r"C:\Program Files\Autodesk\Network License Manager\lmutil.exe",
    r"C:\Autodesk\Network License Manager\lmutil.exe",
    r"C:\Program Files (x86)\Autodesk\Network License Manager\lmutil.exe",
    r"C:\Program Files\ANSYS Inc\Shared Files\Licensing\winx64\lmutil.exe",
    r"C:\Program Files\ANSYS Inc\Shared Files\Licensing\win64\lmutil.exe",
    r"C:\Program Files\MATLAB\*\etc\win64\lmutil.exe",
    r"C:\Program Files\Siemens\PLMLicenseServer\lmutil.exe",
    r"C:\HeptagonesLM\bin\lmutil.exe",
]


class SetupError(Exception):
    """Échec d'une action système, avec un message compréhensible."""


@dataclass
class Result:
    code: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def message(self) -> str:
        """Ligne d'erreur utile : première ligne significative de l'erreur, sinon fin de la sortie."""
        if self.err.strip():
            lines = [line.strip() for line in self.err.splitlines() if line.strip()
                     and not line.strip().startswith(("+", "At line", "Au caractère", "At char"))]
            if lines:
                return lines[0][:500]
        text = self.out.strip()
        return text.splitlines()[-1][:500] if text else f"code {self.code}"


@dataclass
class TaskSpec:
    name: str
    execute: str
    arguments: str
    workdir: str
    trigger: str                    # repeat | daily | weekly | startup
    minutes: int = 5                # repeat
    at: str = "06:00"               # daily / weekly
    description: str = ""
    permanent: bool = False         # interface web : pas de limite de durée, relance auto


class System:
    simulated = False
    name = "abstrait"

    # -- informations -----------------------------------------------------------------
    def os_info(self) -> dict: raise NotImplementedError
    def is_admin(self) -> bool: raise NotImplementedError
    def disk_free_gb(self, path: str) -> float: raise NotImplementedError
    def odbc_drivers(self) -> list[str]: raise NotImplementedError
    def find_lmutil(self) -> list[str]: raise NotImplementedError
    def iis_state(self) -> dict: raise NotImplementedError
    def certificates(self) -> list[dict]: raise NotImplementedError
    def domain_info(self) -> dict: raise NotImplementedError
    def resolve_account(self, account: str) -> str | None: raise NotImplementedError
    def validate_credentials(self, account: str, password: str) -> bool | None: raise NotImplementedError
    def task_info(self, name: str) -> dict | None: raise NotImplementedError
    def site_state(self, name: str) -> dict: raise NotImplementedError
    def firewall_exists(self, name: str) -> bool: raise NotImplementedError

    # -- actions ----------------------------------------------------------------------
    def install_iis_features(self, names: list[str]) -> dict: raise NotImplementedError
    def install_module(self, key: str) -> dict: raise NotImplementedError
    def run(self, cmd: list[str], env: dict | None = None, timeout: int = 180) -> Result: raise NotImplementedError
    def makedirs(self, path: str) -> None: raise NotImplementedError
    def write_text(self, path: str, text: str) -> None: raise NotImplementedError
    def read_text(self, path: str) -> str | None: raise NotImplementedError
    def copy_file(self, src: str, dst: str) -> None: raise NotImplementedError
    def exists(self, path: str) -> bool: raise NotImplementedError
    def grant(self, path: str, account: str, rights: str) -> None: raise NotImplementedError
    def set_machine_env(self, name: str, value: str) -> None: raise NotImplementedError
    def register_task(self, spec: TaskSpec, account: str, password: str) -> None: raise NotImplementedError
    def grant_batch_logon(self, account: str) -> bool: raise NotImplementedError
    def start_task(self, name: str) -> None: raise NotImplementedError
    def stop_task(self, name: str) -> None: raise NotImplementedError
    def stop_heptagoneslm(self, exe_path: str) -> int: raise NotImplementedError
    def configure_iis_site(self, a: Answers, physical_path: str) -> dict: raise NotImplementedError
    def firewall_rule(self, name: str, port: int) -> None: raise NotImplementedError

    # -- réseau / base -------------------------------------------------------------------
    def port_free(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                return False

    def http_status(self, url: str, sni_host: str | None = None, timeout: float = 8) -> int | None:
        """Code HTTP d'une adresse (certificat non vérifié : sonde locale uniquement)."""
        m = re.match(r"^(https?)://([^/:]+)(?::(\d+))?(/.*)?$", url)
        if not m:
            return None
        scheme, host, port, path = m.group(1), m.group(2), int(m.group(3) or (443 if m.group(1) == "https" else 80)), m.group(4) or "/"
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            if scheme == "https":
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                sock = ctx.wrap_socket(sock, server_hostname=sni_host or host)
            with sock:
                sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {sni_host or host}\r\nConnection: close\r\n\r\n".encode())
                head = sock.recv(64).decode("latin-1", "replace")
            m2 = re.match(r"HTTP/\d\.\d (\d{3})", head)
            return int(m2.group(1)) if m2 else None
        except OSError:
            return None

    def db_probe(self, a: Answers) -> dict: raise NotImplementedError
    def db_create(self, a: Answers) -> list[str]: raise NotImplementedError

    # -- exécutable HeptagonesLM à installer --------------------------------------------------
    def runtime_source(self) -> str | None:
        """HeptagonesLM.exe à copier : embarqué dans HeptagonesLM-Setup.exe, ou l'exécutable courant."""
        if getattr(sys, "frozen", False):
            embedded = Path(getattr(sys, "_MEIPASS", "")) / "HeptagonesLM.exe"
            if embedded.is_file():
                return str(embedded)
            if Path(sys.executable).name.lower() == "heptagoneslm.exe":
                return sys.executable
        return None

    def runtime_command(self, a: Answers) -> tuple[str, list[str]]:
        """Programme et arguments préfixes pour lancer HeptagonesLM installé."""
        if self.runtime_source() or self.simulated:
            return a.path("HeptagonesLM.exe"), []
        return sys.executable, ["-m", "heptagoneslm"]

    def setup_dir(self) -> str:
        return str(Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0] or ".").resolve().parent)


# ======================================================================================
# Windows
# ======================================================================================


class WindowsSystem(System):
    name = "Windows"

    PREAMBLE = ("$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; "
                "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
                # lecteur Cert: : module de sécurité chargé explicitement (sans bloquer s'il manque)
                "try { Import-Module Microsoft.PowerShell.Security -ErrorAction Stop } catch { }\n")

    def ps(self, script: str, secret: str | None = None, timeout: int = 600) -> Result:
        full = self.PREAMBLE + "try {\n" + script + "\n} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }"
        encoded = base64.b64encode(full.encode("utf-16-le")).decode("ascii")
        env = dict(os.environ)
        # Lancé depuis PowerShell 7, PSModulePath désigne des modules incompatibles avec Windows
        # PowerShell 5.1 (Microsoft.PowerShell.Security, lecteur Cert:…) : chemin par défaut rétabli.
        env.pop("PSModulePath", None)
        if secret is not None:
            env[SECRET_ENV] = secret
        try:
            # stdin fermé : sinon Windows PowerShell attend la fin d'une entrée standard héritée
            # (tâche planifiée, CI, service) et ne se termine jamais.
            proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                   "-EncodedCommand", encoded], capture_output=True, timeout=timeout, env=env,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return Result(124, "", f"délai dépassé ({timeout} s)")
        return Result(proc.returncode, _decode(proc.stdout), _clixml_errors(_decode(proc.stderr)))

    def ps_json(self, script: str, secret: str | None = None, timeout: int = 600):
        r = self.ps(script, secret, timeout)
        if not r.ok:
            raise SetupError(r.message)
        text = r.out.strip()
        start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
        if start < 0:
            return None
        return json.loads(text[start:])

    # -- informations -------------------------------------------------------------------
    def os_info(self) -> dict:
        return self.ps_json("""
$os = Get-CimInstance Win32_OperatingSystem; $cs = Get-CimInstance Win32_ComputerSystem
$fqdn = try { [System.Net.Dns]::GetHostEntry($env:COMPUTERNAME).HostName } catch { $env:COMPUTERNAME }
[pscustomobject]@{ caption = $os.Caption; version = $os.Version; build = [int]$os.BuildNumber;
  product_type = [int]$os.ProductType; hostname = $env:COMPUTERNAME; fqdn = $fqdn; domain = $cs.Domain;
  part_of_domain = [bool]$cs.PartOfDomain; psversion = $PSVersionTable.PSVersion.ToString() } | ConvertTo-Json -Compress
""")

    def is_admin(self) -> bool:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def disk_free_gb(self, path: str) -> float:
        drive = PureWindowsPath(path).drive or "C:"
        return shutil.disk_usage(drive + "\\").free / 1024 ** 3

    def odbc_drivers(self) -> list[str]:
        try:
            import pyodbc
            return list(pyodbc.drivers())
        except Exception:
            pass
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ODBC\ODBCINST.INI\ODBC Drivers") as k:
                out, i = [], 0
                while True:
                    try:
                        out.append(winreg.EnumValue(k, i)[0]); i += 1
                    except OSError:
                        return out
        except OSError:
            return []

    def find_lmutil(self) -> list[str]:
        found = []
        for pattern in LMUTIL_CANDIDATES:
            found += [p for p in glob.glob(pattern) if os.path.isfile(p)]
        which = shutil.which("lmutil")
        if which:
            found.append(which)
        return list(dict.fromkeys(found))

    def iis_state(self) -> dict:
        return self.ps_json(iis.state_script())

    def certificates(self) -> list[dict]:
        data = self.ps_json("""
$now = Get-Date
$store = New-Object System.Security.Cryptography.X509Certificates.X509Store('My', 'LocalMachine')
$store.Open('ReadOnly')
$certs = @($store.Certificates | Where-Object { $_.HasPrivateKey } | ForEach-Object {
  [pscustomobject]@{ thumbprint = $_.Thumbprint; subject = $_.Subject; friendly = $_.FriendlyName;
    not_after = $_.NotAfter.ToString('yyyy-MM-dd'); expired = ($_.NotAfter -lt $now);
    dns = (@($_.DnsNameList | ForEach-Object { $_.Unicode }) -join ', ') } })
$store.Close()
ConvertTo-Json -InputObject $certs -Compress
""")
        return data if isinstance(data, list) else ([data] if data else [])

    def domain_info(self) -> dict:
        data = self.ps_json("""
$r = [ordered]@{ domain = ''; netbios = $env:USERDOMAIN; dcs = @() }
try {
  $d = [System.DirectoryServices.ActiveDirectory.Domain]::GetComputerDomain()
  $r.domain = $d.Name; $r.dcs = @($d.DomainControllers | Select-Object -First 3 | ForEach-Object { $_.Name })
} catch { }
[pscustomobject]$r | ConvertTo-Json -Compress
""") or {}
        dom = data.get("domain") or ""
        data["base_dn"] = ",".join(f"DC={p}" for p in dom.split(".")) if dom else ""
        data["dcs"] = data.get("dcs") or []
        return data

    def resolve_account(self, account: str) -> str | None:
        data = self.ps_json(f"""
try {{ $sid = (New-Object System.Security.Principal.NTAccount({ps_quote(account)})).Translate([System.Security.Principal.SecurityIdentifier]).Value }}
catch {{ $sid = $null }}
[pscustomobject]@{{ sid = $sid }} | ConvertTo-Json -Compress
""")
        return (data or {}).get("sid")

    def validate_credentials(self, account: str, password: str) -> bool | None:
        dom, _, user = account.partition("\\")
        data = self.ps_json(f"""
Add-Type -AssemblyName System.DirectoryServices.AccountManagement
$dom = {ps_quote(dom)}; $user = {ps_quote(user)}
if ($dom -eq '.' -or $dom -ieq $env:COMPUTERNAME) {{ $ctx = New-Object System.DirectoryServices.AccountManagement.PrincipalContext('Machine', $env:COMPUTERNAME) }}
else {{ $ctx = New-Object System.DirectoryServices.AccountManagement.PrincipalContext('Domain', $dom) }}
[pscustomobject]@{{ valid = $ctx.ValidateCredentials($user, $env:{SECRET_ENV}) }} | ConvertTo-Json -Compress
""", secret=password)
        return bool((data or {}).get("valid"))

    def task_info(self, name: str) -> dict | None:
        data = self.ps_json(f"""
$t = Get-ScheduledTask -TaskPath {ps_quote(TASK_FOLDER)} -TaskName {ps_quote(name)} -ErrorAction SilentlyContinue
if (-not $t) {{ [pscustomobject]@{{ exists = $false }} | ConvertTo-Json -Compress; return }}
$i = $t | Get-ScheduledTaskInfo
[pscustomobject]@{{ exists = $true; state = [string]$t.State; last_result = [int64]$i.LastTaskResult;
  last_run = if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) {{ $i.LastRunTime.ToString('s') }} else {{ $null }};
  next_run = if ($i.NextRunTime) {{ $i.NextRunTime.ToString('s') }} else {{ $null }}; user = $t.Principal.UserId }} | ConvertTo-Json -Compress
""")
        return data if data and data.get("exists") else None

    def site_state(self, name: str) -> dict:
        return self.ps_json(iis.site_state_script(name)) or {"exists": False}

    def firewall_exists(self, name: str) -> bool:
        data = self.ps_json(f"[pscustomobject]@{{ exists = [bool](Get-NetFirewallRule -DisplayName {ps_quote(name)} "
                            "-ErrorAction SilentlyContinue) } | ConvertTo-Json -Compress")
        return bool((data or {}).get("exists"))

    # -- actions ----------------------------------------------------------------------
    def install_iis_features(self, names: list[str]) -> dict:
        return self.ps_json(iis.install_features_script(names), timeout=1800) or {}

    def install_module(self, key: str) -> dict:
        mod = iis.MODULES[key]
        local = None
        for folder in (self.setup_dir(), os.path.join(self.setup_dir(), "prerequis")):
            candidate = os.path.join(folder, mod["msi"])
            if os.path.isfile(candidate):
                local = candidate
        return self.ps_json(iis.install_msi_script(local, mod["url"], mod["msi"]), timeout=1800) or {}

    def run(self, cmd: list[str], env: dict | None = None, timeout: int = 180) -> Result:
        full_env = dict(os.environ)
        full_env.setdefault("PYTHONIOENCODING", "utf-8")     # HeptagonesLM.exe enfant : sortie UTF-8
        full_env.update(env or {})
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=full_env, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            return Result(127, "", f"programme introuvable : {cmd[0]}")
        except subprocess.TimeoutExpired:
            return Result(124, "", f"délai dépassé ({timeout} s)")
        return Result(proc.returncode, _decode(proc.stdout), _decode(proc.stderr))

    def makedirs(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def write_text(self, path: str, text: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(text)

    def read_text(self, path: str) -> str | None:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def copy_file(self, src: str, dst: str) -> None:
        if os.path.abspath(src).lower() == os.path.abspath(dst).lower():
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        for attempt in range(15):          # fichier encore verrouillé quelques instants après un arrêt
            try:
                shutil.copy2(src, dst)
                return
            except PermissionError:
                if attempt == 14:
                    raise SetupError(f"{dst} est utilisé par un autre processus (HeptagonesLM en cours d'exécution ?)")
                time.sleep(1)

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def grant(self, path: str, account: str, rights: str) -> None:
        r = self.run(["icacls", path, "/grant", f"{account}:(OI)(CI){rights}", "/T", "/C", "/Q"], timeout=300)
        if not r.ok:
            raise SetupError(f"icacls {path} : {r.message}")

    def set_machine_env(self, name: str, value: str) -> None:
        r = self.ps(f"[Environment]::SetEnvironmentVariable({ps_quote(name)}, $env:{SECRET_ENV}, 'Machine')", secret=value)
        if not r.ok:
            raise SetupError(r.message)
        os.environ[name] = value     # disponible aussi pour les étapes suivantes (init-db, collecte)

    def register_task(self, spec: TaskSpec, account: str, password: str) -> None:
        if spec.trigger == "repeat":
            trigger = f"New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes {int(spec.minutes)})"
        elif spec.trigger == "daily":
            trigger = f"New-ScheduledTaskTrigger -Daily -At {ps_quote(spec.at)}"
        elif spec.trigger == "weekly":
            trigger = f"New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At {ps_quote(spec.at)}"
        else:
            trigger = "New-ScheduledTaskTrigger -AtStartup"
        if spec.permanent:
            settings = ("New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew "
                        "-ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)")
        else:
            settings = ("New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew "
                        "-ExecutionTimeLimit (New-TimeSpan -Minutes 30)")
        if account.endswith("$"):
            who = f"-Principal (New-ScheduledTaskPrincipal -UserId {ps_quote(account)} -LogonType Password)"
        else:
            who = f"-User {ps_quote(account)} -Password $env:{SECRET_ENV}"
        script = f"""
$action = New-ScheduledTaskAction -Execute {ps_quote(spec.execute)} -Argument {ps_quote(spec.arguments)} -WorkingDirectory {ps_quote(spec.workdir)}
Register-ScheduledTask -TaskName {ps_quote(spec.name)} -TaskPath {ps_quote(TASK_FOLDER)} -Force `
  -Action $action -Trigger ({trigger}) -Settings ({settings}) -Description {ps_quote(spec.description)} {who} | Out-Null
"""
        r = self.ps(script, secret=password)
        if not r.ok:
            raise SetupError(f"Tâche « {spec.name} » : {r.message}")

    def grant_batch_logon(self, account: str) -> bool:
        """Droit « Ouvrir une session en tant que tâche » (SeBatchLogonRight), requis pour les tâches planifiées.

        Renvoie True si le droit a été ajouté, False s'il était déjà accordé. Une stratégie de groupe
        qui définit ce droit l'emporte : ajouter alors le compte dans la GPO.
        """
        data = self.ps_json(f"""
$sid = (New-Object System.Security.Principal.NTAccount({ps_quote(account)})).Translate([System.Security.Principal.SecurityIdentifier]).Value
$export = Join-Path $env:TEMP 'heptagoneslm-rights.inf'; $import = Join-Path $env:TEMP 'heptagoneslm-rights-new.inf'
secedit /export /cfg $export /areas USER_RIGHTS | Out-Null
$line = Get-Content $export | Where-Object {{ $_ -like 'SeBatchLogonRight*' }} | Select-Object -First 1
$added = $false
if (-not $line -or $line -notmatch [regex]::Escape('*' + $sid)) {{
  $new = if ($line) {{ $line + ',*' + $sid }} else {{ 'SeBatchLogonRight = *' + $sid }}
  $inf = "[Unicode]`r`nUnicode=yes`r`n[Version]`r`nsignature=`"`$CHICAGO`$`"`r`nRevision=1`r`n[Privilege Rights]`r`n" + $new
  Set-Content -Path $import -Value $inf -Encoding Unicode
  secedit /configure /db (Join-Path $env:TEMP 'heptagoneslm-rights.sdb') /cfg $import /areas USER_RIGHTS /quiet | Out-Null
  $added = $true
}}
Remove-Item $export, $import -ErrorAction SilentlyContinue
[pscustomobject]@{{ added = $added }} | ConvertTo-Json -Compress
""")
        return bool((data or {}).get("added"))

    def start_task(self, name: str) -> None:
        r = self.ps(f"Start-ScheduledTask -TaskPath {ps_quote(TASK_FOLDER)} -TaskName {ps_quote(name)}")
        if not r.ok:
            raise SetupError(r.message)

    def stop_task(self, name: str) -> None:
        self.ps(f"Stop-ScheduledTask -TaskPath {ps_quote(TASK_FOLDER)} -TaskName {ps_quote(name)} -ErrorAction SilentlyContinue")

    def stop_heptagoneslm(self, exe_path: str) -> int:
        """Arrête les tâches HeptagonesLM en cours et les processus de cet exécutable (mise à jour)."""
        data = self.ps_json(f"""
Get-ScheduledTask -TaskPath {ps_quote(TASK_FOLDER)} -ErrorAction SilentlyContinue |
    Where-Object {{ $_.State -eq 'Running' }} | Stop-ScheduledTask -ErrorAction SilentlyContinue
$target = {ps_quote(exe_path)}
$procs = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -and ($_.Path -ieq $target) }})
$procs | Stop-Process -Force -ErrorAction SilentlyContinue
$procs | Wait-Process -Timeout 30 -ErrorAction SilentlyContinue
[pscustomobject]@{{ stopped = $procs.Count }} | ConvertTo-Json -Compress
""", timeout=120) or {}
        return int(data.get("stopped") or 0)

    def configure_iis_site(self, a: Answers, physical_path: str) -> dict:
        return self.ps_json(iis.configure_site_script(a, physical_path), timeout=600) or {}

    def firewall_rule(self, name: str, port: int) -> None:
        r = self.ps(f"""
Get-NetFirewallRule -DisplayName {ps_quote(name)} -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName {ps_quote(name)} -Direction Inbound -Protocol TCP -LocalPort {int(port)} -Action Allow -Profile Domain,Private | Out-Null
""")
        if not r.ok:
            raise SetupError(r.message)

    # -- base de données ---------------------------------------------------------------
    def _connect(self, a: Answers, database: str):
        import pyodbc
        cs = a.connection_string().replace(f"DATABASE={a.db_name}", f"DATABASE={database}")
        if a.db_auth == "sql":
            cs = cs.replace("${HEPTAGONESLM_DB_PASSWORD}", a.db_password)
        return pyodbc.connect(cs, autocommit=True, timeout=10)

    def db_probe(self, a: Answers) -> dict:
        if a.db_mode == "sqlite":
            return {"ok": True, "server": "SQLite", "exists": False, "can_create": True}
        try:
            conn = self._connect(a, "master")
        except Exception as exc:  # pyodbc.Error, ImportError
            return {"ok": False, "error": _odbc_message(exc)}
        try:
            version = conn.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(40)), "
                                   "CAST(SERVERPROPERTY('Edition') AS nvarchar(120))").fetchone()
            exists = conn.execute("SELECT DB_ID(?)", a.db_name).fetchone()[0] is not None
            can_create = bool(conn.execute("SELECT HAS_PERMS_BY_NAME(NULL, NULL, 'CREATE ANY DATABASE')").fetchone()[0])
            sysadmin = bool(conn.execute("SELECT IS_SRVROLEMEMBER('sysadmin')").fetchone()[0])
            out = {"ok": True, "server": f"SQL Server {version[0]} ({version[1]})", "exists": exists,
                   "can_create": can_create, "sysadmin": sysadmin, "schema": None}
            if exists:
                row = conn.execute(f"SELECT CASE WHEN OBJECT_ID(N'[{a.db_name}].dbo.schema_info') IS NULL THEN NULL "
                                   f"ELSE (SELECT MAX(version) FROM [{a.db_name}].dbo.schema_info) END").fetchone()
                out["schema"] = row[0]
            return out
        except Exception as exc:
            return {"ok": False, "error": _odbc_message(exc)}
        finally:
            conn.close()

    def db_create(self, a: Answers) -> list[str]:
        """Base, connexion Windows du compte de service et rôles (lecture / écriture)."""
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", a.db_name):
            raise SetupError("Nom de base invalide")
        done = []
        conn = self._connect(a, "master")
        try:
            db = f"[{a.db_name}]"
            if conn.execute("SELECT DB_ID(?)", a.db_name).fetchone()[0] is None:
                conn.execute(f"CREATE DATABASE {db}")
                conn.execute(f"ALTER DATABASE {db} SET RECOVERY SIMPLE")
                done.append(f"base {a.db_name} créée (récupération SIMPLE)")
            else:
                done.append(f"base {a.db_name} déjà présente")
            if a.db_auth == "windows":
                acct = a.service_account.replace("]", "]]")
                if conn.execute("SELECT SUSER_ID(?)", a.service_account).fetchone()[0] is None:
                    conn.execute(f"CREATE LOGIN [{acct}] FROM WINDOWS")
                    done.append(f"connexion {a.service_account} créée")
                conn.execute(f"USE {db}")
                if conn.execute("SELECT USER_ID(?)", a.service_account).fetchone()[0] is None \
                        and conn.execute("SELECT COUNT(*) FROM sys.database_principals WHERE sid = SUSER_SID(?)",
                                         a.service_account).fetchone()[0] == 0:
                    conn.execute(f"CREATE USER [{acct}] FOR LOGIN [{acct}]")
                for role in ("db_datareader", "db_datawriter"):
                    conn.execute(f"ALTER ROLE {role} ADD MEMBER [{acct}]")
                done.append(f"{a.service_account} : lecture et écriture (db_datareader, db_datawriter)")
        finally:
            conn.close()
        return done


def _clixml_errors(err: str) -> str:
    """Erreurs PowerShell sérialisées (CLIXML, sortie d'erreur redirigée) -> texte lisible."""
    if "#< CLIXML" not in err:
        return err
    parts = re.findall(r'<S S="Error">(.*?)</S>', err, re.S)
    text = "".join(parts)
    text = re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        import locale
        return data.decode(locale.getpreferredencoding(False) or "cp1252", "replace")


def _odbc_message(exc: Exception) -> str:
    text = str(exc)
    m = re.findall(r"\[SQL Server\]([^\[\]]+?)(?: \(\d+\)|$)", text) or re.findall(r"\]([^\[\]]+?) \(\d+\)", text)
    return (m[-1] if m else text).strip()[:400]


# ======================================================================================
# Simulation
# ======================================================================================


class SimulatedSystem(System):
    """Réponses réalistes, sans aucune modification du poste (bac à sable temporaire)."""

    simulated = True
    name = "Simulation"

    def __init__(self, delay: float = 0.35, fail_once: set[str] | None = None, sandbox: str | None = None):
        self.delay = delay
        self.fail_once = set(fail_once or ())
        self.sandbox = Path(sandbox or tempfile.mkdtemp(prefix="heptagoneslm-setup-sim-"))
        self.lock = threading.Lock()
        self.features = {f[0]: False for f in iis.FEATURES}
        self.modules = {"rewrite": False, "arr": False}
        self.tasks: dict[str, dict] = {}
        self.sites: dict[str, dict] = {}
        self.firewall: set[str] = set()
        self.env: dict[str, str] = {}
        self.acl: list[tuple[str, str, str]] = []
        self.calls: list[str] = []
        self.db = {"exists": False, "schema": None}

    def _step(self, name: str, factor: float = 1.0) -> None:
        self.calls.append(name)
        if self.delay:
            time.sleep(self.delay * factor)
        if name in self.fail_once:
            self.fail_once.discard(name)
            raise SetupError(f"Échec simulé : {name}")

    def _real(self, path: str) -> Path:
        p = PureWindowsPath(path)
        return self.sandbox.joinpath(p.drive.rstrip(":") or "C", *p.parts[1:])

    # -- informations -------------------------------------------------------------------
    def os_info(self) -> dict:
        return {"caption": "Microsoft Windows Server 2022 Standard", "version": "10.0.20348", "build": 20348,
                "product_type": 3, "hostname": "SRV-HEPTAGONESLM", "fqdn": "srv-heptagoneslm.entreprise.local",
                "domain": "entreprise.local", "part_of_domain": True, "psversion": "5.1.20348.2849"}

    def is_admin(self) -> bool:
        return True

    def disk_free_gb(self, path: str) -> float:
        return 84.2

    def odbc_drivers(self) -> list[str]:
        return ["SQL Server", "ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server"]

    def find_lmutil(self) -> list[str]:
        return [r"C:\Program Files\Autodesk\Network License Manager\lmutil.exe"]

    def port_free(self, port: int) -> bool:
        return True

    def iis_state(self) -> dict:
        return {"kind": "server", "features": dict(self.features), "modules": dict(self.modules)}

    def certificates(self) -> list[dict]:
        return [
            {"thumbprint": "3F2A9C4D8E1B7A6F5C0D2E9B8A7F6C5D4E3B2A19", "subject": "CN=heptagoneslm.entreprise.local",
             "friendly": "HeptagonesLM (AC Entreprise)", "not_after": "2027-09-30", "expired": False,
             "dns": "heptagoneslm.entreprise.local, srv-heptagoneslm.entreprise.local"},
            {"thumbprint": "A1B2C3D4E5F60718293A4B5C6D7E8F9012345678", "subject": "CN=srv-heptagoneslm.entreprise.local",
             "friendly": "Certificat machine", "not_after": "2026-12-31", "expired": False,
             "dns": "srv-heptagoneslm.entreprise.local"},
        ]

    def domain_info(self) -> dict:
        return {"domain": "entreprise.local", "netbios": "ENTREPRISE",
                "dcs": ["dc01.entreprise.local", "dc02.entreprise.local"], "base_dn": "DC=entreprise,DC=local"}

    def resolve_account(self, account: str) -> str | None:
        return None if "inconnu" in account.lower() else "S-1-5-21-1004336348-1177238915-682003330-1105"

    def validate_credentials(self, account: str, password: str) -> bool | None:
        return password != "mauvais"

    def task_info(self, name: str) -> dict | None:
        t = self.tasks.get(name)
        return dict(t) if t else None

    def site_state(self, name: str) -> dict:
        s = self.sites.get(name)
        return dict(s) if s else {"exists": False}

    def firewall_exists(self, name: str) -> bool:
        return name in self.firewall

    # -- actions ----------------------------------------------------------------------
    def install_iis_features(self, names: list[str]) -> dict:
        self._step("install_iis_features", 4)
        for n in names:
            self.features[n] = True
        return {"restart": False}

    def install_module(self, key: str) -> dict:
        self._step(f"install_module:{key}", 3)
        self.modules[key] = True
        return {"code": 0, "source": iis.MODULES[key]["url"]}

    def run(self, cmd: list[str], env: dict | None = None, timeout: int = 180) -> Result:
        self._step("run:" + " ".join(cmd[1:3]), 2)
        joined = " ".join(cmd)
        if " lmstat " in f" {joined} ":
            return self._lmstat(cmd[cmd.index("-c") + 1] if "-c" in cmd else "")
        if "init-db" in cmd:
            self.db["exists"] = True
            self.db["schema"] = 3
            return Result(0, "Schéma créé (version 3) ; serveurs référencés : " +
                          str(len(re.findall(r"^- name:", self.read_text(cmd[cmd.index('-c') + 1]) or "", re.M))))
        if "collect" in cmd:
            self.db["collected"] = time.time()
            return Result(0, "Collecte terminée")
        return Result(0, "")

    def _lmstat(self, target: str) -> Result:
        from datetime import datetime

        from heptagoneslm.simulation.model import find_server, render_lmstat

        server = find_server(target)
        if server is None:
            if "inconnu" in target.lower():
                return Result(1, f"License server status: {target}\nlmgrd is not running: Cannot connect to license "
                                 "server system. (-15,570:10061 \"WinSock: Connection refused\")")
            server = find_server("27000@licsrv-adsk")
            text = render_lmstat(server, datetime.now().replace(microsecond=0))
            return Result(0, text.replace("27000@licsrv-adsk", target))
        return Result(0, render_lmstat(server, datetime.now().replace(microsecond=0)))

    def makedirs(self, path: str) -> None:
        self._real(path).mkdir(parents=True, exist_ok=True)

    def write_text(self, path: str, text: str) -> None:
        real = self._real(path)
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(text, encoding="utf-8")

    def read_text(self, path: str) -> str | None:
        real = self._real(path)
        return real.read_text(encoding="utf-8") if real.is_file() else None

    def copy_file(self, src: str, dst: str) -> None:
        self._step("copy_file", 2)
        real = self._real(dst)
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_bytes(b"MZ simulated HeptagonesLM.exe")

    def exists(self, path: str) -> bool:
        return self._real(path).exists()

    def grant(self, path: str, account: str, rights: str) -> None:
        self._step("grant", 0.3)
        self.acl.append((path, account, rights))

    def set_machine_env(self, name: str, value: str) -> None:
        self._step("set_machine_env", 0.3)
        self.env[name] = "********"

    def register_task(self, spec: TaskSpec, account: str, password: str) -> None:
        self._step(f"register_task:{spec.name}", 0.6)
        if not account.endswith("$") and not password:
            raise SetupError("Mot de passe du compte de service requis")
        self.tasks[spec.name] = {"exists": True, "state": "Ready", "last_result": 267011, "last_run": None,
                                 "next_run": None, "user": account, "spec": spec.arguments}

    def grant_batch_logon(self, account: str) -> bool:
        self._step("grant_batch_logon", 0.3)
        return True

    def start_task(self, name: str) -> None:
        self._step(f"start_task:{name}", 0.5)
        if name in self.tasks:
            self.tasks[name].update(state="Running", last_result=267009,
                                    last_run=time.strftime("%Y-%m-%dT%H:%M:%S"))

    def stop_task(self, name: str) -> None:
        if name in self.tasks:
            self.tasks[name]["state"] = "Ready"

    def stop_heptagoneslm(self, exe_path: str) -> int:
        running = [t for t in self.tasks.values() if t.get("state") == "Running"]
        for t in running:
            t["state"] = "Ready"
        return len(running)

    def configure_iis_site(self, a: Answers, physical_path: str) -> dict:
        self._step("configure_iis_site", 3)
        missing = [f for f, ok in self.features.items() if not ok] + [m for m, ok in self.modules.items() if not ok]
        if missing:
            raise SetupError("Prérequis IIS manquants : " + ", ".join(missing))
        self.sites[a.iis_site] = {"exists": True, "state": "Started", "pool": "Started",
                                  "bindings": [f"https *:{a.iis_port}:{a.public_host}"]}
        return {"state": "Started"}

    def firewall_rule(self, name: str, port: int) -> None:
        self._step("firewall_rule", 0.5)
        self.firewall.add(name)

    def http_status(self, url: str, sni_host: str | None = None, timeout: float = 8) -> int | None:
        web = self.tasks.get("HeptagonesLM - Interface web", {})
        if sni_host and self.sites and f":{next(iter(self.sites.values()))['bindings'][0].split(':')[1]}/" in url:
            return 401          # IIS demande l'authentification Windows : chaîne opérationnelle
        if "127.0.0.1" in url and web.get("state") == "Running":
            return 200
        return None

    def db_probe(self, a: Answers) -> dict:
        self._step("db_probe", 2)
        if a.db_mode == "sqlite":
            return {"ok": True, "server": "SQLite", "exists": False, "can_create": True}
        if "inconnu" in a.db_server.lower():
            return {"ok": False, "error": "Named Pipes Provider: Could not open a connection to SQL Server [53]."}
        if a.db_auth == "sql" and a.db_password == "mauvais":
            return {"ok": False, "error": f"Login failed for user '{a.db_user}'."}
        return {"ok": True, "server": "SQL Server 16.0.4135.4 (Standard Edition (64-bit))",
                "exists": self.db["exists"], "can_create": True, "sysadmin": True, "schema": self.db["schema"]}

    def db_create(self, a: Answers) -> list[str]:
        self._step("db_create", 3)
        self.db["exists"] = True
        out = [f"base {a.db_name} créée (récupération SIMPLE)"]
        if a.db_auth == "windows":
            out += [f"connexion {a.service_account} créée",
                    f"{a.service_account} : lecture et écriture (db_datareader, db_datawriter)"]
        return out


def make_system(simulate: bool) -> System:
    if simulate:
        return SimulatedSystem()
    if os.name != "nt":
        raise SetupError("L'assistant installe HeptagonesLM sur un serveur Windows. "
                         "Pour une démonstration sur ce poste : heptagoneslm setup --simulate")
    return WindowsSystem()
