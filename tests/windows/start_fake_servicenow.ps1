# Démarre le faux ServiceNow HTTPS (tests/fake_servicenow.py) s'il ne répond pas, puis attend qu'il réponde.
# Appelé dans chaque étape qui en a besoin : sur les machines GitHub Windows, un processus lancé par une
# étape est arrêté à la fin de celle-ci (d'où le refus de connexion constaté à l'étape suivante).
# Variable : ITSM_URL (https://127.0.0.1:<port>). Journaux : fake-snow.log / fake-snow.err (dossier courant).
$ErrorActionPreference = "Stop"
$port = ([Uri]$env:ITSM_URL).Port
function Test-Snow { try { Invoke-RestMethod "$env:ITSM_URL/incidents.json" -TimeoutSec 5 | Out-Null; $true } catch { $false } }
if (Test-Snow) { Write-Host "Faux ServiceNow : déjà joignable ($env:ITSM_URL)"; return }
if (Test-Path fake-snow.err) { Write-Host "Faux ServiceNow injoignable ; journal précédent :"; Get-Content fake-snow.err -Tail 30 }
$n = @(Get-ChildItem fake-snow*.err -ErrorAction SilentlyContinue).Count
$p = Start-Process python -ArgumentList "-m", "tests.fake_servicenow", "$port" -PassThru `
     -RedirectStandardOutput "fake-snow-$n.log" -RedirectStandardError "fake-snow-$n.err"
for ($i = 0; $i -lt 30; $i++) { if (Test-Snow) { break }; Start-Sleep 1 }
if (-not (Test-Snow)) { Get-Content "fake-snow-$n.err" -ErrorAction SilentlyContinue; throw "faux ServiceNow non démarré" }
Write-Host "Faux ServiceNow démarré (processus $($p.Id)) : $env:ITSM_URL"
