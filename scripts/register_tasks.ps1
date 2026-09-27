<#
.SYNOPSIS
    Enregistre les tâches planifiées HeptagonesLM (collecte, rapport mensuel, envoi par e-mail, purge).

.EXAMPLE
    .\register_tasks.ps1 -HeptagonesLMHome C:\HeptagonesLM -ServiceAccount "DOMAINE\svc_heptagoneslm"
.EXAMPLE
    .\register_tasks.ps1 -HeptagonesLMHome C:\HeptagonesLM -ServiceAccount "DOMAINE\svc_heptagoneslm" -WithWebInterface
    (ajoute l'interface web permanente, démarrée avec le serveur : section web du YAML)
    (le mot de passe du compte est demandé ; utiliser un gMSA "DOMAINE\gmsa_heptagoneslm$" pour s'en passer)
#>
param(
    [string]$HeptagonesLMHome = "C:\HeptagonesLM",
    # Compte dédié et non administrateur (idéalement un gMSA « DOMAINE\gmsa_heptagoneslm$ »).
    [Parameter(Mandatory = $true)]
    [string]$ServiceAccount,
    [switch]$AllowSystem,
    [int]$CollectIntervalMinutes = 5,
    [switch]$WithWebInterface
)

$ErrorActionPreference = "Stop"

# Moindre privilège : SYSTEM donnerait le contrôle total de la machine à quiconque
# exploiterait une faille de HeptagonesLM. Refusé pour l'interface web, et sur demande
# explicite (-AllowSystem) seulement pour les tâches sans exposition réseau.
$isSystem = $ServiceAccount -match '^(NT AUTHORITY\\)?SYSTEM$'
if ($isSystem -and $WithWebInterface) {
    throw "L'interface web ne doit pas tourner en SYSTEM : utiliser un compte de service dédié (gMSA recommandé)."
}
if ($isSystem -and -not $AllowSystem) {
    throw "Compte SYSTEM refusé par défaut : indiquer un compte de service dédié, ou -AllowSystem en connaissance de cause."
}
$python = Join-Path $HeptagonesLMHome "venv\Scripts\python.exe"
$config = Join-Path $HeptagonesLMHome "heptagoneslm.yaml"

function New-HeptagonesLMAction([string]$arguments) {
    New-ScheduledTaskAction -Execute $python -Argument "-m heptagoneslm -c `"$config`" $arguments" -WorkingDirectory $HeptagonesLMHome
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

if ($ServiceAccount -like "*$") {
    $principal = New-ScheduledTaskPrincipal -UserId $ServiceAccount -LogonType Password
    $register = @{ Principal = $principal }
} elseif ($isSystem) {
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Limited
    $register = @{ Principal = $principal }
} else {
    $cred = Get-Credential -UserName $ServiceAccount -Message "Mot de passe du compte de service HeptagonesLM"
    $register = @{ User = $cred.UserName; Password = $cred.GetNetworkCredential().Password }
}

# 1. Collecte toutes les N minutes, en continu
$collectTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes $CollectIntervalMinutes)
Register-ScheduledTask -TaskName "HeptagonesLM - Collecte" -TaskPath "\HeptagonesLM\" -Force @register `
    -Action (New-HeptagonesLMAction "collect") -Trigger $collectTrigger -Settings $settings `
    -Description "Sondage lmstat / lecture des logs FlexLM"

# 2. Rapport du mois précédent : tâche quotidienne, le rapport n'est produit
#    qu'une fois par mois (--skip-existing), même si le 1er a été manqué.
Register-ScheduledTask -TaskName "HeptagonesLM - Rapport mensuel" -TaskPath "\HeptagonesLM\" -Force @register `
    -Action (New-HeptagonesLMAction "report --period last-month --skip-existing") `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 6am) -Settings $settings `
    -Description "Rapport Excel du mois précédent (généré une seule fois par mois)"

# 3. Envoi des rapports par e-mail aux abonnés (section email du YAML) : tâche
#    quotidienne, chaque abonnement n'est envoyé qu'une fois par période (--skip-sent).
Register-ScheduledTask -TaskName "HeptagonesLM - Envoi des rapports" -TaskPath "\HeptagonesLM\" -Force @register `
    -Action (New-HeptagonesLMAction "send-report --skip-sent") `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 7am) -Settings $settings `
    -Description "Rapports mensuels par e-mail (une fois par période)"

# 4. Purge hebdomadaire des données au-delà de la rétention
Register-ScheduledTask -TaskName "HeptagonesLM - Purge" -TaskPath "\HeptagonesLM\" -Force @register `
    -Action (New-HeptagonesLMAction "purge") `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am) -Settings $settings `
    -Description "Purge selon la politique de rétention"

# 5. (option) Interface web permanente : démarrée avec le serveur, relancée en cas d'arrêt
if ($WithWebInterface) {
    $webSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName "HeptagonesLM - Interface web" -TaskPath "\HeptagonesLM\" -Force @register `
        -Action (New-HeptagonesLMAction "web") -Trigger (New-ScheduledTaskTrigger -AtStartup) -Settings $webSettings `
        -Description "Interface web HeptagonesLM (adresse, HTTPS et authentification : section web du YAML)"
    Start-ScheduledTask -TaskName "HeptagonesLM - Interface web" -TaskPath "\HeptagonesLM\"
}

Write-Host "Tâches HeptagonesLM enregistrées sous \HeptagonesLM\ dans le Planificateur de tâches."
