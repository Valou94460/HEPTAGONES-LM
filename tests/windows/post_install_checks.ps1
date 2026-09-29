# Contrôles après une installation réelle (CI Windows) : tâches, site IIS, authentification unique
# Windows (NTLM) de bout en bout, en-têtes forgés refusés, test d'accès, refus hors du groupe et sans
# identifiants, collecte planifiée exécutée par le compte de service.
# Variables : SVC_PWD (mot de passe des comptes locaux jetables de la CI).
$ErrorActionPreference = "Stop"
Get-ScheduledTask -TaskPath "\HeptagonesLM\" | Format-Table TaskName, State, @{n='Compte';e={$_.Principal.UserId}}
& "$env:windir\system32\inetsrv\appcmd.exe" list site
# Authentification unique : IIS (Windows) -> module HeptagonesLMUserHeader -> HeptagonesLM (en-tête X-Remote-User)
$url = "https://heptagoneslm.ci.local/api/me"
function Get-Me([string[]]$extra) {
  $code = curl.exe -sk --ntlm -u "ci_lecteur:$env:SVC_PWD" @extra -o me.json -w "%{http_code}" $url
  $body = Get-Content me.json -Raw
  Write-Host "[$code] $body"
  if ($code -ne "200") { throw "GET /api/me via IIS : $code" }
  return $body | ConvertFrom-Json
}
$me = Get-Me @()
if ($me.auth -ne "header" -or $me.user -ne "ci_lecteur") { throw "Authentification unique non transmise" }
# En-tête forgé par le client : ignoré (remplacé par l'utilisateur authentifié)
$me = Get-Me @("-H", "X-Remote-User: administrateur")
if ($me.user -ne "ci_lecteur") { throw "en-tête X-Remote-User du client accepté" }
# Groupes Windows transmis par le module (rôles) ; groupes forgés par le client ignorés
$base = (Get-Me @()).groups
if ($base -lt 1) { throw "groupes Windows non transmis (X-Remote-Groups)" }
$forged = (1..40 | ForEach-Object { "G$_" }) -join ";"
$me = Get-Me @("-H", "X-Remote-Groups: $forged")
if ($me.groups -ne $base) { throw "en-tête X-Remote-Groups du client accepté ($($me.groups) au lieu de $base)" }
# Test d'accès depuis un poste (/acces) : méthode transmise par le module, résultat enregistré
$code = curl.exe -sk --ntlm -u "ci_lecteur:$env:SVC_PWD" -o acces.html -w "%{http_code}" https://heptagoneslm.ci.local/acces
if ($code -ne "200") { throw "page /acces : $code" }
$code = curl.exe -sk --ntlm -u "ci_lecteur:$env:SVC_PWD" -o test.json -w "%{http_code}" https://heptagoneslm.ci.local/api/access-test
Write-Host "[$code] $(Get-Content test.json -Raw)"
$t = Get-Content test.json -Raw | ConvertFrom-Json
if ($code -ne "200" -or $t.method -ne "NTLM" -or $t.user -ne "ci_lecteur") { throw "test d'accès : méthode ou utilisateur non transmis" }
Set-Content body.json '{"prompted": false}' -Encoding ascii
$code = curl.exe -sk --ntlm -u "ci_lecteur:$env:SVC_PWD" -H "Content-Type: application/json" -H "X-HeptagonesLM: 1" `
  --data-binary "@body.json" -o rec.json -w "%{http_code}" https://heptagoneslm.ci.local/api/access-test
Write-Host "[$code] $(Get-Content rec.json -Raw)"
if ($code -ne "200") { throw "enregistrement du test d'accès : $code" }
# Authentifié mais hors du groupe autorisé : refus par IIS
$code = curl.exe -sk --ntlm -u "svc_heptagoneslm:$env:SVC_PWD" -o NUL -w "%{http_code}" $url
Write-Host "Hors du groupe : $code"
if ($code -ne "401") { throw "accès accordé hors du groupe ($code)" }
# Sans identifiants : refus par IIS
try { Invoke-WebRequest "https://heptagoneslm.ci.local/" -SkipCertificateCheck -ErrorAction Stop; throw "accès anonyme accepté" }
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
