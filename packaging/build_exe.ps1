<#
.SYNOPSIS
    Construit l'exécutable autonome FreeLM.exe (aucun Python requis sur le poste cible).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
    -> dist\FreeLM.exe
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
    freelm/launcher.py

.\dist\FreeLM.exe --version
Write-Host "Exécutable prêt : dist\FreeLM.exe"
