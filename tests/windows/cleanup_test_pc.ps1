# Nettoyage d'un poste de test (runner auto-hébergé) : retire l'installation de test de HeptagonesLM
# et ce que prepare_test_pc.ps1 a créé (noté dans C:\HeptagonesLM-test\etat.json), rien d'autre.
# Les rôles et modules IIS installés restent en place (réutilisés par l'essai suivant).
# Windows PowerShell 5.1, administrateur. Sans erreur bloquante : chaque élément absent est ignoré.
param([string]$InstallDir = "C:\HeptagonesLM", [string]$Site = "HeptagonesLM")
$ErrorActionPreference = "Continue"
function Step([string]$label, [scriptblock]$action) {
  try { & $action; Write-Host "  ok  $label" } catch { Write-Host "  --  $label : $($_.Exception.Message)" }
}
Write-Host "Nettoyage du poste de test $env:COMPUTERNAME"

# -- Installation HeptagonesLM ---------------------------------------------------------------
Step "tâches planifiées \HeptagonesLM\" {
  Get-ScheduledTask -TaskPath "\HeptagonesLM\" -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-ScheduledTask -InputObject $_ -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -InputObject $_ -Confirm:$false
  }
}
Step "processus HeptagonesLM" {
  Get-Process HeptagonesLM, HeptagonesLM-Setup -ErrorAction SilentlyContinue | Stop-Process -Force
}
Step "site et pool IIS « $Site »" {
  if (Get-Module -ListAvailable WebAdministration) {
    Import-Module WebAdministration
    if (Test-Path "IIS:\Sites\$Site") { Remove-Website -Name $Site }
    if (Test-Path "IIS:\AppPools\$Site") { Remove-WebAppPool -Name $Site }
  }
}
Step "règles de pare-feu HeptagonesLM" {
  Get-NetFirewallRule -DisplayName "HeptagonesLM*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
}
Step "variables d'environnement machine HEPTAGONESLM_*" {
  foreach ($v in "HEPTAGONESLM_DB_PASSWORD", "HEPTAGONESLM_SMTP_PASSWORD") {
    [Environment]::SetEnvironmentVariable($v, $null, "Machine")
  }
}
Step "dossier $InstallDir" {
  if (Test-Path $InstallDir) { Start-Sleep 2; Remove-Item $InstallDir -Recurse -Force }
}

# -- Éléments de test ------------------------------------------------------------------------
$stateFile = "C:\HeptagonesLM-test\etat.json"
if (Test-Path $stateFile) {
  $state = Get-Content $stateFile -Raw | ConvertFrom-Json
  foreach ($u in @($state.users)) {
    if (-not $u) { continue }
    Step "profil Windows de $u" {
      $sid = (Get-LocalUser -Name $u).SID.Value
      Get-CimInstance Win32_UserProfile | Where-Object { $_.SID -eq $sid } | Remove-CimInstance
    }
    Step "compte local de test $u" { Remove-LocalUser -Name $u }
  }
  foreach ($g in @($state.groups)) { if ($g) { Step "groupe local de test $g" { Remove-LocalGroup -Name $g } } }
  if ($state.cert) {
    Step "certificat de test $($state.cert)" { Remove-Item "Cert:\LocalMachine\My\$($state.cert)" }
  }
  if ($state.hosts) {
    Step "entrée hosts de test" {
      $f = "C:\Windows\System32\drivers\etc\hosts"
      (Get-Content $f) | Where-Object { $_ -ne $state.hosts } | Set-Content $f -Encoding ascii
    }
  }
  if ($state.loopback) {
    Step "DisableLoopbackCheck" { Remove-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\Lsa -Name DisableLoopbackCheck }
  }
  foreach ($f in @($state.files)) { if ($f) { Step "fichier de test $f" { Remove-Item $f -Force } } }
  foreach ($d in @($state.dirs)) {
    if ($d -and $d -ne "C:\HeptagonesLM-test") { Step "dossier de test $d" { if (-not (Get-ChildItem $d -Force)) { Remove-Item $d } } }
  }
  Step "dossier C:\HeptagonesLM-test" { Remove-Item C:\HeptagonesLM-test -Recurse -Force }
} else {
  Write-Host "  (aucun état de test : rien d'autre à retirer)"
}
Write-Host "Nettoyage terminé."
exit 0
