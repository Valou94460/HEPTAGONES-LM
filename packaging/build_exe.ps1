<#
.SYNOPSIS
    Construit HeptagonesLM.exe (application autonome) et HeptagonesLM-Setup.exe (assistant d'installation).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
    -> dist\HeptagonesLM.exe, dist\HeptagonesLM-Setup.exe
#>
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

py -3 -m venv .build-venv
.\.build-venv\Scripts\python -m pip install --upgrade pip
.\.build-venv\Scripts\pip install . pyinstaller

.\.build-venv\Scripts\pyinstaller --noconfirm --clean --onefile --console `
    --name HeptagonesLM --icon packaging/heptagoneslm.ico `
    --collect-submodules heptagoneslm `
    --add-data "heptagoneslm/web/static:heptagoneslm/web/static" `
    --add-data "heptagoneslm/setup/static:heptagoneslm/setup/static" `
    heptagoneslm/launcher.py

# Assistant d'installation : demande l'élévation administrateur, embarque HeptagonesLM.exe
.\.build-venv\Scripts\pyinstaller --noconfirm --onefile --windowed --uac-admin `
    --name HeptagonesLM-Setup --icon packaging/heptagoneslm.ico `
    --collect-submodules heptagoneslm `
    --add-data "heptagoneslm/web/static:heptagoneslm/web/static" `
    --add-data "heptagoneslm/setup/static:heptagoneslm/setup/static" `
    --add-data "dist/HeptagonesLM.exe:." `
    heptagoneslm/launcher.py

.\dist\HeptagonesLM.exe --version
.\dist\HeptagonesLM-Setup.exe --version
Write-Host "Exécutables prêts : dist\HeptagonesLM.exe (application) et dist\HeptagonesLM-Setup.exe (assistant d'installation)"
