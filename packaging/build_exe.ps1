<#
.SYNOPSIS
    Construit FreeLM.exe (application autonome) et FreeLM-Setup.exe (assistant d'installation).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
    -> dist\FreeLM.exe, dist\FreeLM-Setup.exe
#>
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

py -3 -m venv .build-venv
.\.build-venv\Scripts\python -m pip install --upgrade pip
.\.build-venv\Scripts\pip install . pyinstaller

.\.build-venv\Scripts\pyinstaller --noconfirm --clean --onefile --console `
    --name FreeLM `
    --collect-submodules freelm `
    --add-data "freelm/web/static:freelm/web/static" `
    --add-data "freelm/setup/static:freelm/setup/static" `
    freelm/launcher.py

# Assistant d'installation : demande l'élévation administrateur, embarque FreeLM.exe
.\.build-venv\Scripts\pyinstaller --noconfirm --onefile --console --uac-admin `
    --name FreeLM-Setup `
    --collect-submodules freelm `
    --add-data "freelm/web/static:freelm/web/static" `
    --add-data "freelm/setup/static:freelm/setup/static" `
    --add-data "dist/FreeLM.exe:." `
    freelm/launcher.py

.\dist\FreeLM.exe --version
.\dist\FreeLM-Setup.exe --version
Write-Host "Exécutables prêts : dist\FreeLM.exe (application) et dist\FreeLM-Setup.exe (assistant d'installation)"
