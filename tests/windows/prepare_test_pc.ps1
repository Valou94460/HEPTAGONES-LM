# Préparation d'un poste de test pour l'installation réelle filmée (runner GitHub auto-hébergé).
# Windows PowerShell 5.1, administrateur. Tout ce qui est créé est noté dans C:\HeptagonesLM-test\etat.json
# pour que cleanup_test_pc.ps1 ne retire que cela.
#   -Comptes local   : compte de service, lecteur de test et groupe LOCAUX créés (mot de passe aléatoire)
#   -Comptes domaine : comptes et groupe du domaine fournis par les secrets du dépôt (rien n'est créé)
#   -Lmutil test     : lmutil.exe de test dans C:\Flexlm (réponses lmstat de la démonstration)
#   -Lmutil reel     : aucun outil de test ; la recherche doit trouver le vrai lmutil.exe
# Variables exportées vers les étapes suivantes (GITHUB_ENV) : SVC_ACCOUNT, SVC_PWD, ACCESS_GROUP,
# TOUR_USER, TOUR_PASSWORD, TEST_USER, TEST_PASSWORD, OUTSIDER_USER, OUTSIDER_PASSWORD, CERT_THUMB,
# EXPECT_LMUTIL (mode test).
param(
  [ValidateSet("local", "domaine")] [string]$Comptes = "local",
  [ValidateSet("test", "reel")] [string]$Lmutil = "test",
  [string]$PublicHost = "heptagoneslm.test.local",
  [string]$Exe = "dist\HeptagonesLM.exe"
)
$ErrorActionPreference = "Stop"
$stateDir = "C:\HeptagonesLM-test"
New-Item -ItemType Directory $stateDir -Force | Out-Null
$state = [ordered]@{ created = (Get-Date).ToString("s"); users = @(); groups = @(); cert = $null; hosts = $null;
                     loopback = $false; files = @(); dirs = @() }
function Save-State { $state | ConvertTo-Json -Depth 4 | Set-Content "$stateDir\etat.json" -Encoding utf8 }
function Export([string]$name, [string]$value, [switch]$Secret) {
  if ($Secret -and $value) { Write-Host "::add-mask::$value" }
  if ($env:GITHUB_ENV) { Add-Content $env:GITHUB_ENV "$name=$value" }
}
$marker = "HeptagonesLM-test"

# -- Système ------------------------------------------------------------------------------
$os = Get-CimInstance Win32_OperatingSystem
Write-Host "Poste : $env:COMPUTERNAME · $($os.Caption) $($os.Version) · domaine : $((Get-CimInstance Win32_ComputerSystem).Domain)"
if ($os.Caption -match "Famille|Home") { throw "Windows Famille/Home : l'authentification Windows d'IIS n'existe pas. Utiliser Windows Pro, Entreprise ou Server." }
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole("Administrators")) {
  throw "Le runner doit tourner avec des droits d'administrateur."
}

# -- Comptes --------------------------------------------------------------------------------
if ($Comptes -eq "local") {
  # Mot de passe aléatoire à chaque exécution (lettres, chiffres, - _ !), masqué dans les journaux
  $chars = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_!"
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create(); $bytes = New-Object byte[] 20; $rng.GetBytes($bytes)
  $pwd = (-join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })) + "a1A!"
  $secure = ConvertTo-SecureString $pwd -AsPlainText -Force
  foreach ($u in "svc_heptagoneslm", "hlm_lecteur") {
    $existing = Get-LocalUser -Name $u -ErrorAction SilentlyContinue
    if ($existing -and $existing.Description -ne $marker) { throw "Le compte local $u existe déjà et n'a pas été créé par ce test : arrêt." }
    if ($existing) { Remove-LocalUser -Name $u }
    New-LocalUser -Name $u -Password $secure -PasswordNeverExpires -UserMayNotChangePassword -Description $marker | Out-Null
    $state.users += $u
  }
  if (-not (Get-LocalGroup -Name HeptagonesLM_Lecteurs -ErrorAction SilentlyContinue)) {
    New-LocalGroup -Name HeptagonesLM_Lecteurs -Description $marker | Out-Null
    $state.groups += "HeptagonesLM_Lecteurs"
  }
  Add-LocalGroupMember -Group HeptagonesLM_Lecteurs -Member hlm_lecteur -ErrorAction SilentlyContinue
  Save-State
  Export SVC_ACCOUNT "$env:COMPUTERNAME\svc_heptagoneslm"; Export SVC_PWD $pwd -Secret
  Export ACCESS_GROUP "$env:COMPUTERNAME\HeptagonesLM_Lecteurs"
  Export TEST_USER "hlm_lecteur"; Export TEST_PASSWORD $pwd -Secret
  Export TOUR_USER "hlm_lecteur"; Export TOUR_PASSWORD $pwd -Secret
  Export OUTSIDER_USER "svc_heptagoneslm"; Export OUTSIDER_PASSWORD $pwd -Secret
} else {
  foreach ($k in "HLM_SVC_ACCOUNT", "HLM_SVC_PASSWORD", "HLM_ACCESS_GROUP", "HLM_TEST_USER", "HLM_TEST_PASSWORD") {
    if (-not [Environment]::GetEnvironmentVariable($k)) { throw "Secret $k manquant (Settings > Secrets and variables > Actions)" }
  }
  Export SVC_ACCOUNT $env:HLM_SVC_ACCOUNT; Export SVC_PWD $env:HLM_SVC_PASSWORD -Secret
  Export ACCESS_GROUP $env:HLM_ACCESS_GROUP
  Export TEST_USER $env:HLM_TEST_USER; Export TEST_PASSWORD $env:HLM_TEST_PASSWORD -Secret
  Export TOUR_USER $env:HLM_TEST_USER; Export TOUR_PASSWORD $env:HLM_TEST_PASSWORD -Secret
  # Compte authentifié hors du groupe autorisé : le compte de service (il ne doit pas être dans le groupe)
  Export OUTSIDER_USER $env:HLM_SVC_ACCOUNT; Export OUTSIDER_PASSWORD $env:HLM_SVC_PASSWORD -Secret
}

# -- Certificat HTTPS de test, nom DNS local ------------------------------------------------
$cert = New-SelfSignedCertificate -DnsName $PublicHost -CertStoreLocation Cert:\LocalMachine\My -FriendlyName $marker
$state.cert = $cert.Thumbprint
Export CERT_THUMB $cert.Thumbprint
$hostsFile = "C:\Windows\System32\drivers\etc\hosts"
$line = "127.0.0.1 $PublicHost # $marker"
if (-not (Select-String -Path $hostsFile -SimpleMatch $line -Quiet)) { Add-Content $hostsFile $line; $state.hosts = $line }
# Authentification Windows vers un alias local (poste de test uniquement ; retiré par le nettoyage)
$lsa = "HKLM:\SYSTEM\CurrentControlSet\Control\Lsa"
if (-not (Get-ItemProperty $lsa -Name DisableLoopbackCheck -ErrorAction SilentlyContinue)) {
  New-ItemProperty $lsa -Name DisableLoopbackCheck -Value 1 -PropertyType DWord -Force | Out-Null
  $state.loopback = $true
}
Save-State

# -- lmutil.exe ------------------------------------------------------------------------------
if ($Lmutil -eq "test") {
  if (Test-Path C:\Flexlm\lmutil.exe) { throw "C:\Flexlm\lmutil.exe existe déjà (vrai outil ?) : choisir lmutil = reel ou le déplacer" }
  foreach ($d in "C:\HeptagonesLM-test\bin", "C:\Flexlm") {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory $d | Out-Null; $state.dirs += $d }
  }
  Copy-Item $Exe C:\HeptagonesLM-test\bin\HeptagonesLM.exe
  $src = @'
using System; using System.Diagnostics; using System.Linq;
class LmutilTest {
  static int Main(string[] args) {
    var psi = new ProcessStartInfo(@"C:\HeptagonesLM-test\bin\HeptagonesLM.exe", "fake-lmutil " + string.Join(" ", args.Select(a => "\"" + a + "\"")));
    psi.UseShellExecute = false;
    var p = Process.Start(psi); p.WaitForExit(); return p.ExitCode;
  }
}
'@
  Set-Content "$stateDir\lmutil.cs" $src -Encoding utf8
  & "$env:windir\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /out:C:\Flexlm\lmutil.exe "$stateDir\lmutil.cs"
  if ($LASTEXITCODE -ne 0) { throw "compilation de lmutil.exe" }
  $state.files += "C:\Flexlm\lmutil.exe"
  Save-State
  & C:\Flexlm\lmutil.exe lmstat -a -c 27000@licsrv-adsk | Select-Object -First 4
  Export EXPECT_LMUTIL "C:\Flexlm\lmutil.exe"
}
Write-Host "Poste prêt (comptes : $Comptes, lmutil : $Lmutil). État : $stateDir\etat.json"
