"""Configuration d'IIS en frontal de HeptagonesLM (authentification unique Windows).

IIS authentifie l'utilisateur (Kerberos / NTLM), vérifie le groupe autorisé puis
relaie la requête à HeptagonesLM (écoute locale) avec l'en-tête ``X-Remote-User``
(module URL Rewrite + Application Request Routing en mode proxy).

L'en-tête est posé par un petit module ASP.NET (``App_Code``) *après*
l'authentification : les règles URL Rewrite s'exécutent avant celle-ci, une
variable ``{LOGON_USER}`` y serait toujours vide. Le module supprime aussi tout
en-tête ``X-Remote-User`` envoyé par le navigateur.
"""

from __future__ import annotations

from xml.sax.saxutils import quoteattr

from heptagoneslm.setup.answers import Answers

# Rôles IIS nécessaires : Windows Server (Install-WindowsFeature) / Windows client (optionnel).
FEATURES = [
    # (nom serveur, nom client, libellé)
    ("Web-Server", "IIS-WebServerRole", "Serveur Web IIS"),
    ("Web-Windows-Auth", "IIS-WindowsAuthentication", "Authentification Windows"),
    ("Web-Url-Auth", "IIS-URLAuthorization", "Autorisation d'URL (restriction par groupe)"),
    ("Web-Asp-Net45", "IIS-ASPNET45", "ASP.NET 4.x (transmission de l'utilisateur à HeptagonesLM)"),
    ("Web-Mgmt-Console", "IIS-ManagementConsole", "Console de gestion IIS"),
    ("Web-Scripting-Tools", "IIS-ManagementScriptingTools", "Outils de script IIS (PowerShell)"),
]

# Modules Microsoft (installeurs MSI officiels ; ou fichiers déposés dans prerequis\ hors ligne).
MODULES = {
    "rewrite": {
        "label": "Module URL Rewrite 2.1",
        "msi": "rewrite_amd64_en-US.msi",
        "url": "https://download.microsoft.com/download/1/2/8/128E2E22-C1B9-44A4-BE2A-5859ED1D4592/rewrite_amd64_en-US.msi",
        "module": "RewriteModule",
    },
    "arr": {
        "label": "Application Request Routing 3.0",
        "msi": "requestRouter_amd64.msi",
        "url": "https://download.microsoft.com/download/E/9/8/E9849D6A-020E-47E4-9FD0-A023E99B54EB/requestRouter_amd64.msi",
        "module": "ApplicationRequestRouting",
    },
}

HEADER_NAME = "X-Remote-User"
MODULE_NAME = "HeptagonesLMUserHeader"

# Module ASP.NET compilé par IIS au premier appel (dossier App_Code du site).
METHOD_HEADER = "X-Remote-Auth-Method"

# Module ASP.NET compilé par IIS au premier appel (dossier App_Code du site). C# 5 (compilateur .NET 4.x).
USER_MODULE_CS = f"""// Généré par l'assistant d'installation HeptagonesLM.
// Transmet l'utilisateur authentifié par IIS (Windows) à HeptagonesLM dans l'en-tête {HEADER_NAME},
// et la méthode d'authentification (Kerberos ou NTLM) dans l'en-tête {METHOD_HEADER}.
// Les règles URL Rewrite s'exécutent avant l'authentification : ce module intervient après.
using System;
using System.Security.Principal;
using System.Text;
using System.Web;

public class {MODULE_NAME} : IHttpModule
{{
    const string Header = "{HEADER_NAME}";
    const string MethodHeader = "{METHOD_HEADER}";
    static readonly byte[] KerberosOid = {{ 0x06, 0x09, 0x2A, 0x86, 0x48, 0x86, 0xF7, 0x12, 0x01, 0x02, 0x02 }};

    public void Init(HttpApplication app)
    {{
        // Jamais de valeur fournie par le navigateur
        app.BeginRequest += (s, e) =>
        {{
            HttpRequest req = ((HttpApplication)s).Context.Request;
            req.Headers.Remove(Header);
            req.Headers.Remove(MethodHeader);
        }};
        app.PostAuthenticateRequest += (s, e) =>
        {{
            HttpContext ctx = ((HttpApplication)s).Context;
            ctx.Request.Headers.Remove(Header);
            ctx.Request.Headers.Remove(MethodHeader);
            if (ctx.User != null && ctx.User.Identity != null && ctx.User.Identity.IsAuthenticated)
            {{
                ctx.Request.Headers.Set(Header, ctx.User.Identity.Name);
                string method = Method(ctx);
                if (method != null) ctx.Request.Headers.Set(MethodHeader, method);
            }}
        }};
    }}

    // Kerberos ou NTLM : jeton de la requête (première requête de la connexion), sinon session Windows.
    static string Method(HttpContext ctx)
    {{
        string auth = ctx.Request.Headers["Authorization"];
        if (auth != null)
        {{
            int space = auth.IndexOf(' ');
            if (space > 0)
            {{
                try
                {{
                    byte[] token = Convert.FromBase64String(auth.Substring(space + 1).Trim());
                    if (Contains(token, Encoding.ASCII.GetBytes("NTLMSSP"))) return "NTLM";
                    if (Contains(token, KerberosOid)) return "Kerberos";
                }}
                catch (FormatException) {{ }}
            }}
        }}
        try
        {{
            WindowsIdentity id = ctx.User.Identity as WindowsIdentity;
            if (id != null)
            {{
                string type = new WindowsIdentity(id.Token).AuthenticationType;
                if (type == "Kerberos" || type == "NTLM") return type;
            }}
        }}
        catch (Exception) {{ }}
        return null;
    }}

    static bool Contains(byte[] data, byte[] pattern)
    {{
        for (int i = 0; i + pattern.Length <= data.Length; i++)
        {{
            int j = 0;
            while (j < pattern.Length && data[i + j] == pattern[j]) j++;
            if (j == pattern.Length) return true;
        }}
        return false;
    }}

    public void Dispose() {{ }}
}}
"""


def ps_quote(value) -> str:
    """Chaîne PowerShell littérale (apostrophes doublées) : aucune interprétation possible."""
    return "'" + str(value).replace("'", "''") + "'"


def web_config(a: Answers) -> str:
    """web.config du site : autorisation par groupe, module d'identité et relais vers HeptagonesLM."""
    auth = ""
    if a.access_group:
        auth = (
            "    <security>\n      <authorization>\n        <clear />\n"
            f"        <add accessType=\"Allow\" roles={quoteattr(a.access_group)} />\n"
            "      </authorization>\n    </security>\n")
    target = f"http://127.0.0.1:{a.web_port}/{{R:1}}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!-- Généré par l'assistant d'installation HeptagonesLM : IIS authentifie (Windows),\n"
        "     puis relaie vers HeptagonesLM qui n'écoute que sur la boucle locale. -->\n"
        "<configuration>\n  <system.webServer>\n"
        f"{auth}"
        "    <modules>\n"
        f"      <!-- App_Code\\{MODULE_NAME}.cs : en-tête {HEADER_NAME} posé après l'authentification -->\n"
        f'      <remove name="{MODULE_NAME}" />\n'
        f'      <add name="{MODULE_NAME}" type="{MODULE_NAME}" />\n'
        "    </modules>\n"
        "    <rewrite>\n      <rules>\n"
        '        <rule name="HeptagonesLM" stopProcessing="true">\n'
        '          <match url="(.*)" />\n'
        f'          <action type="Rewrite" url={quoteattr(target)} />\n'
        "        </rule>\n      </rules>\n    </rewrite>\n"
        '    <httpProtocol>\n      <customHeaders>\n        <remove name="X-Powered-By" />\n'
        "      </customHeaders>\n    </httpProtocol>\n"
        "  </system.webServer>\n</configuration>\n")


def state_script() -> str:
    """État des rôles et modules IIS (JSON)."""
    server_names = ",".join(ps_quote(f[0]) for f in FEATURES)
    client_names = ",".join(ps_quote(f[1]) for f in FEATURES)
    modules = ",".join(f"@{{key={ps_quote(k)}; name={ps_quote(m['module'])}}}" for k, m in MODULES.items())
    return f"""
$features = @{{}}
if (Get-Command Get-WindowsFeature -ErrorAction SilentlyContinue) {{
    foreach ($f in Get-WindowsFeature -Name @({server_names})) {{ $features[$f.Name] = [bool]$f.Installed }}
    $kind = 'server'
}} else {{
    $map = @{{}}; $i = 0
    $server = @({server_names}); $client = @({client_names})
    foreach ($c in $client) {{
        $st = Get-WindowsOptionalFeature -Online -FeatureName $c -ErrorAction SilentlyContinue
        $features[$server[$i]] = [bool]($st -and $st.State -eq 'Enabled'); $i++
    }}
    $kind = 'client'
}}
$config = Join-Path $env:windir 'System32\\inetsrv\\config\\applicationHost.config'
$text = if (Test-Path $config) {{ Get-Content $config -Raw }} else {{ '' }}
$mods = @{{}}
foreach ($m in @({modules})) {{ $mods[$m.key] = $text -match ('name="' + $m.name + '"') }}
[pscustomobject]@{{ kind = $kind; features = $features; modules = $mods }} | ConvertTo-Json -Compress -Depth 4
"""


def install_features_script(names: list[str]) -> str:
    server = ",".join(ps_quote(n) for n in names)
    client = ",".join(ps_quote(f[1]) for f in FEATURES if f[0] in names)
    return f"""
if (Get-Command Install-WindowsFeature -ErrorAction SilentlyContinue) {{
    $r = Install-WindowsFeature -Name @({server}) -IncludeManagementTools
    if (-not $r.Success) {{ throw "Install-WindowsFeature a échoué : $($r.ExitCode)" }}
    [pscustomobject]@{{ restart = [bool]($r.RestartNeeded -eq 'Yes') }} | ConvertTo-Json -Compress
}} else {{
    $restart = $false
    foreach ($n in @({client})) {{
        $r = Enable-WindowsOptionalFeature -Online -FeatureName $n -All -NoRestart
        if ($r.RestartNeeded) {{ $restart = $true }}
    }}
    [pscustomobject]@{{ restart = $restart }} | ConvertTo-Json -Compress
}}
"""


def install_msi_script(local_path: str | None, url: str, msi_name: str) -> str:
    """Installe un MSI (fichier local si présent, sinon téléchargement Microsoft)."""
    source = ps_quote(local_path) if local_path else "$null"
    return f"""
$local = {source}
if ($local -and (Test-Path $local)) {{ $msi = $local }} else {{
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $msi = Join-Path $env:TEMP {ps_quote(msi_name)}
    Invoke-WebRequest -Uri {ps_quote(url)} -OutFile $msi -UseBasicParsing
}}
$p = Start-Process -FilePath msiexec.exe -ArgumentList @('/i', ('"' + $msi + '"'), '/qn', '/norestart') -Wait -PassThru
if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) {{ throw "msiexec a renvoyé le code $($p.ExitCode)" }}
[pscustomobject]@{{ code = $p.ExitCode; source = $msi }} | ConvertTo-Json -Compress
"""


def configure_site_script(a: Answers, physical_path: str) -> str:
    """Crée ou met à jour le pool, le site, la liaison HTTPS, l'authentification et le proxy."""
    site, pool = ps_quote(a.iis_site), ps_quote(a.iis_site)
    host, port = ps_quote(a.public_host), int(a.iis_port)
    thumb = ps_quote(a.iis_cert_thumbprint.upper())
    path = ps_quote(physical_path)
    apphost = "'MACHINE/WEBROOT/APPHOST'"
    return f"""
Import-Module WebAdministration
$site = {site}; $pool = {pool}; $hostName = {host}; $port = {port}; $thumb = {thumb}; $path = {path}
$store = New-Object System.Security.Cryptography.X509Certificates.X509Store('My', 'LocalMachine'); $store.Open('ReadOnly')
$found = $store.Certificates.Find('FindByThumbprint', $thumb, $false).Count; $store.Close()
if ($found -eq 0) {{ throw "Certificat $thumb introuvable dans LocalMachine\\My" }}
if (-not (Test-Path "IIS:\\AppPools\\$pool")) {{ New-WebAppPool -Name $pool | Out-Null }}
Set-ItemProperty "IIS:\\AppPools\\$pool" -Name managedRuntimeVersion -Value 'v4.0'
Set-ItemProperty "IIS:\\AppPools\\$pool" -Name managedPipelineMode -Value 'Integrated'
Set-ItemProperty "IIS:\\AppPools\\$pool" -Name startMode -Value 'AlwaysRunning'
if (-not (Get-Website -Name $site)) {{
    New-Website -Name $site -PhysicalPath $path -ApplicationPool $pool -Port $port -HostHeader $hostName -Ssl -SslFlags 1 | Out-Null
}} else {{
    Set-ItemProperty "IIS:\\Sites\\$site" -Name physicalPath -Value $path
    Set-ItemProperty "IIS:\\Sites\\$site" -Name applicationPool -Value $pool
}}
$info = "*:" + $port + ":" + $hostName
$binding = Get-WebBinding -Name $site -Protocol https | Where-Object {{ $_.bindingInformation -eq $info }}
if (-not $binding) {{
    New-WebBinding -Name $site -Protocol https -Port $port -HostHeader $hostName -SslFlags 1
    $binding = Get-WebBinding -Name $site -Protocol https | Where-Object {{ $_.bindingInformation -eq $info }}
}}
$sslPath = "IIS:\\SslBindings\\!$port!$hostName"
if (Test-Path $sslPath) {{ Remove-Item $sslPath -Force }}
$binding.AddSslCertificate($thumb, 'my')
# Authentification : Windows uniquement (sections verrouillées : écrites dans applicationHost)
Set-WebConfigurationProperty -PSPath {apphost} -Location $site -Filter 'system.webServer/security/authentication/anonymousAuthentication' -Name enabled -Value $false
Set-WebConfigurationProperty -PSPath {apphost} -Location $site -Filter 'system.webServer/security/authentication/windowsAuthentication' -Name enabled -Value $true
# ARR en mode proxy, en-tête Host conservé (contrôle anti « DNS rebinding » de HeptagonesLM)
Set-WebConfigurationProperty -PSPath {apphost} -Filter 'system.webServer/proxy' -Name enabled -Value $true
Set-WebConfigurationProperty -PSPath {apphost} -Filter 'system.webServer/proxy' -Name preserveHostHeader -Value $true
Start-WebAppPool -Name $pool -ErrorAction SilentlyContinue
Start-Website -Name $site -ErrorAction SilentlyContinue
[pscustomobject]@{{ state = (Get-Website -Name $site).State }} | ConvertTo-Json -Compress
"""


def site_state_script(site: str) -> str:
    return f"""
Import-Module WebAdministration
$s = Get-Website -Name {ps_quote(site)}
if (-not $s) {{ [pscustomobject]@{{ exists = $false }} | ConvertTo-Json -Compress; return }}
$pool = Get-WebAppPoolState -Name $s.applicationPool -ErrorAction SilentlyContinue
[pscustomobject]@{{ exists = $true; state = [string]$s.State; pool = [string]$pool.Value;
    bindings = @($s.bindings.Collection | ForEach-Object {{ $_.protocol + ' ' + $_.bindingInformation }}) }} | ConvertTo-Json -Compress -Depth 3
"""
