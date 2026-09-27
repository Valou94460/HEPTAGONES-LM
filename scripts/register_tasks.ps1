<#
.SYNOPSIS
    Enregistre les tâches planifiées FreeLM (collecte, rapport mensuel, purge).

.EXAMPLE
    .\register_tasks.ps1 -FreeLMHome C:\FreeLM -ServiceAccount "DOMAINE\svc_freelm"
    (le mot de passe du compte est demandé ; utiliser un gMSA "DOMAINE\gmsa_freelm$" pour s'en passer)
#>
param(
    [string]$FreeLMHome = "C:\FreeLM",
    [string]$ServiceAccount = "NT AUTHORITY\SYSTEM",
    [int]$CollectIntervalMinutes = 5
)

$ErrorActionPreference = "Stop"
$python = Join-Path $FreeLMHome "venv\Scripts\python.exe"
$config = Join-Path $FreeLMHome "freelm.yaml"

function New-FreeLMAction([string]$arguments) {
    New-ScheduledTaskAction -Execute $python -Argument "-m freelm -c `"$config`" $arguments" -WorkingDirectory $FreeLMHome
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

if ($ServiceAccount -like "*$") {
    $principal = New-ScheduledTaskPrincipal -UserId $ServiceAccount -LogonType Password
    $register = @{ Principal = $principal }
} elseif ($ServiceAccount -eq "NT AUTHORITY\SYSTEM") {
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $register = @{ Principal = $principal }
} else {
    $cred = Get-Credential -UserName $ServiceAccount -Message "Mot de passe du compte de service FreeLM"
    $register = @{ User = $cred.UserName; Password = $cred.GetNetworkCredential().Password }
}

# 1. Collecte toutes les N minutes, en continu
$collectTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes $CollectIntervalMinutes)
Register-ScheduledTask -TaskName "FreeLM - Collecte" -TaskPath "\FreeLM\" -Force @register `
    -Action (New-FreeLMAction "collect") -Trigger $collectTrigger -Settings $settings `
    -Description "Sondage lmstat / lecture des logs FlexLM"

# 2. Rapport du mois précédent : tâche quotidienne, le rapport n'est produit
#    qu'une fois par mois (--skip-existing), même si le 1er a été manqué.
Register-ScheduledTask -TaskName "FreeLM - Rapport mensuel" -TaskPath "\FreeLM\" -Force @register `
    -Action (New-FreeLMAction "report --period last-month --skip-existing") `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 6am) -Settings $settings `
    -Description "Rapport Excel du mois précédent (généré une seule fois par mois)"

# 3. Purge hebdomadaire des données au-delà de la rétention
Register-ScheduledTask -TaskName "FreeLM - Purge" -TaskPath "\FreeLM\" -Force @register `
    -Action (New-FreeLMAction "purge") `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am) -Settings $settings `
    -Description "Purge selon la politique de rétention"

Write-Host "Tâches FreeLM enregistrées sous \FreeLM\ dans le Planificateur de tâches."
