# Contrôles après une installation réelle (CI Windows) : tâches, site IIS, authentification unique
# Windows (NTLM) de bout en bout, en-têtes forgés refusés, test d'accès, refus hors du groupe et sans
# identifiants, collecte planifiée exécutée par le compte de service.
# Variables : SVC_PWD (comptes locaux jetables de la CI) ; sur un poste de test (comptes du domaine) :
#   PUBLIC_HOST, TEST_USER / TEST_PASSWORD (membre du groupe autorisé),
#   OUTSIDER_USER / OUTSIDER_PASSWORD (compte authentifié hors du groupe).
$ErrorActionPreference = "Stop"
function Get-Setting([string]$name, [string]$default) { $v = [Environment]::GetEnvironmentVariable($name); if ($v) { $v } else { $default } }
$hostName = Get-Setting PUBLIC_HOST "heptagoneslm.ci.local"
$user = Get-Setting TEST_USER "ci_lecteur"
$cred = "${user}:$(Get-Setting TEST_PASSWORD $env:SVC_PWD)"
$short = ($user -split "\\")[-1].ToLower()          # nom transmis par IIS, sans le domaine
$outsider = "$(Get-Setting OUTSIDER_USER 'svc_heptagoneslm'):$(Get-Setting OUTSIDER_PASSWORD $env:SVC_PWD)"
Get-ScheduledTask -TaskPath "\HeptagonesLM\" | Format-Table TaskName, State, @{n='Compte';e={$_.Principal.UserId}}
& "$env:windir\system32\inetsrv\appcmd.exe" list site
# Authentification unique : IIS (Windows) -> module HeptagonesLMUserHeader -> HeptagonesLM (en-tête X-Remote-User)
$url = "https://$hostName/api/me"
function Get-Me([string[]]$extra) {
  $code = curl.exe -sk --ntlm -u $cred @extra -o me.json -w "%{http_code}" $url
  $body = Get-Content me.json -Raw
  Write-Host "[$code] $body"
  if ($code -ne "200") { throw "GET /api/me via IIS : $code" }
  return $body | ConvertFrom-Json
}
$me = Get-Me @()
if ($me.auth -ne "header" -or $me.user -ne $short) { throw "Authentification unique non transmise" }
# En-tête forgé par le client : ignoré (remplacé par l'utilisateur authentifié)
$me = Get-Me @("-H", "X-Remote-User: administrateur")
if ($me.user -ne $short) { throw "en-tête X-Remote-User du client accepté" }
# Groupes Windows transmis par le module (rôles) ; groupes forgés par le client ignorés
$base = (Get-Me @()).groups
if ($base -lt 1) { throw "groupes Windows non transmis (X-Remote-Groups)" }
$forged = (1..40 | ForEach-Object { "G$_" }) -join ";"
$me = Get-Me @("-H", "X-Remote-Groups: $forged")
if ($me.groups -ne $base) { throw "en-tête X-Remote-Groups du client accepté ($($me.groups) au lieu de $base)" }
# Test d'accès depuis un poste (/acces) : méthode transmise par le module, résultat enregistré
$code = curl.exe -sk --ntlm -u $cred -o acces.html -w "%{http_code}" https://$hostName/acces
if ($code -ne "200") { throw "page /acces : $code" }
$code = curl.exe -sk --ntlm -u $cred -o test.json -w "%{http_code}" https://$hostName/api/access-test
Write-Host "[$code] $(Get-Content test.json -Raw)"
$t = Get-Content test.json -Raw | ConvertFrom-Json
if ($code -ne "200" -or $t.method -ne "NTLM" -or $t.user -ne $short) { throw "test d'accès : méthode ou utilisateur non transmis" }
Set-Content body.json '{"prompted": false}' -Encoding ascii
$code = curl.exe -sk --ntlm -u $cred -H "Content-Type: application/json" -H "X-HeptagonesLM: 1" `
  --data-binary "@body.json" -o rec.json -w "%{http_code}" https://$hostName/api/access-test
Write-Host "[$code] $(Get-Content rec.json -Raw)"
if ($code -ne "200") { throw "enregistrement du test d'accès : $code" }
# Authentifié mais hors du groupe autorisé : refus par IIS
$code = curl.exe -sk --ntlm -u $outsider -o NUL -w "%{http_code}" $url
Write-Host "Hors du groupe : $code"
if ($code -ne "401") { throw "accès accordé hors du groupe ($code)" }
# Sans identifiants : refus par IIS
try { Invoke-WebRequest "https://$hostName/" -SkipCertificateCheck -ErrorAction Stop; throw "accès anonyme accepté" }
catch { if ($_.Exception.Response.StatusCode.value__ -ne 401) { throw $_ } }
# Collecte planifiée exécutée par le compte de service
Start-ScheduledTask -TaskPath "\HeptagonesLM\" -TaskName "HeptagonesLM - Collecte"
# Attente de la fin réelle de la tâche (démarrage de l'exécutable variable selon l'antivirus)
$task = Get-ScheduledTask -TaskPath "\HeptagonesLM\" -TaskName "HeptagonesLM - Collecte"
for ($i = 0; $i -lt 90; $i++) {
  Start-Sleep 2
  $info = $task | Get-ScheduledTaskInfo
  if ((Get-ScheduledTask -TaskPath "\HeptagonesLM\" -TaskName "HeptagonesLM - Collecte").State -ne "Running" -and $info.LastTaskResult -ne 267009) { break }
}
Write-Host "Collecte planifiée : dernier résultat $($info.LastTaskResult)"
if ($info.LastTaskResult -ne 0) { Get-Content C:\HeptagonesLM\logs\heptagoneslm.log -Tail 40; throw "collecte planifiée en échec" }
# Tickets ITSM (si configurés par l'assistant) : secret chiffré par Windows (DPAPI), dossier réservé à
# SYSTEM, aux administrateurs et au compte de service, déchiffré par l'application installée, y compris
# sous le compte de service. Variables : ITSM_URL, ITSM_PASSWORD ; SVC_ACCOUNT / SVC_PASSWORD (ou SVC_PWD).
if ($env:ITSM_URL) {
  $exe, $cfg = "C:\HeptagonesLM\HeptagonesLM.exe", "C:\HeptagonesLM\heptagoneslm.yaml"
  $secretDir = "C:\HeptagonesLM\secrets"; $secret = Join-Path $secretDir "itsm_password.dpapi"
  if (-not (Test-Path $secret)) { throw "secret ITSM chiffré absent : $secret" }
  $hex = [BitConverter]::ToString([IO.File]::ReadAllBytes($secret))
  foreach ($enc in [Text.Encoding]::UTF8, [Text.Encoding]::Unicode) {
    if ($hex.Contains([BitConverter]::ToString($enc.GetBytes($env:ITSM_PASSWORD)))) { throw "mot de passe ITSM en clair dans $secret" }
  }
  if ((Get-Content $cfg -Raw).Contains($env:ITSM_PASSWORD)) { throw "mot de passe ITSM dans heptagoneslm.yaml" }
  Write-Host "heptagoneslm.yaml : $((Select-String -Path $cfg -Pattern 'dpapi:').Line.Trim())"
  $svc = Get-Setting SVC_ACCOUNT "$env:COMPUTERNAME\svc_heptagoneslm"
  $sid = { param($n) (New-Object Security.Principal.NTAccount($n)).Translate([Security.Principal.SecurityIdentifier]).Value }
  $acl = Get-Acl $secretDir
  $acl.Access | ForEach-Object { Write-Host "  secrets : $($_.IdentityReference) $($_.FileSystemRights) (hérité : $($_.IsInherited))" }
  if (-not $acl.AreAccessRulesProtected) { throw "héritage des droits non coupé sur $secretDir" }
  $ids = @($acl.Access | ForEach-Object { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } | Sort-Object -Unique)
  $want = @("S-1-5-18", "S-1-5-32-544", (& $sid $svc)) | Sort-Object -Unique
  if (Compare-Object $want $ids) { throw "droits inattendus sur $secretDir : $($ids -join ', ')" }
  # PowerShell 7 renvoie un tableau JSON comme un seul objet : le dérouler avant de compter
  $incidents = { @((Invoke-RestMethod "$env:ITSM_URL/incidents.json") | ForEach-Object { $_ }) }
  $count = { (& $incidents).Count }
  $before = & $count
  # Déchiffrement par l'application (administrateur) : ticket de test ouvert, commenté, résolu
  & $exe -c $cfg test-itsm
  if ($LASTEXITCODE -ne 0) { throw "test-itsm (administrateur) : code $LASTEXITCODE" }
  # Déchiffrement sous le compte de service (tâche planifiée temporaire, comme la collecte)
  $name = "HeptagonesLM - Test ITSM (contrôle)"
  $action = New-ScheduledTaskAction -Execute $exe -Argument "-c `"$cfg`" test-itsm"
  Register-ScheduledTask -TaskName $name -Action $action -User $svc -Password (Get-Setting SVC_PASSWORD $env:SVC_PWD) -RunLevel Limited | Out-Null
  try {
    Start-ScheduledTask -TaskName $name
    for ($i = 0; $i -lt 60; $i++) {
      Start-Sleep 2
      $t = Get-ScheduledTask -TaskName $name; $info = $t | Get-ScheduledTaskInfo
      if ($t.State -ne "Running" -and $info.LastTaskResult -ne 267009 -and $info.LastTaskResult -ne 267011) { break }
    }
    Write-Host "test-itsm sous $svc : dernier résultat $($info.LastTaskResult)"
    if ($info.LastTaskResult -ne 0) { Get-Content C:\HeptagonesLM\logs\heptagoneslm.log -Tail 20; throw "test-itsm sous le compte de service en échec" }
  } finally { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
  $after = & $count
  Write-Host "Incidents reçus par l'outil ITSM : $before -> $after"
  if ($after -lt $before + 2) { throw "tickets de test non reçus par l'outil ITSM ($before -> $after)" }
  & $incidents | Select-Object -Last 2 number, state, short_description, caller_id | Format-Table
}
