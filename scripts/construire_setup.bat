@echo off
rem Construit HeptagonesLM-Setup.exe sur ce PC, sans GitHub Actions.
rem Double-cliquer ce fichier depuis le dossier du projet (clone du dépôt).
rem Prérequis : Python 3.10 ou plus récent (https://www.python.org/downloads/, cocher « Add python.exe to PATH »).
rem Résultat : dist\HeptagonesLM-Setup.exe
setlocal
chcp 65001 > nul
cd /d "%~dp0.."

where py > nul 2>&1 && (set "PY=py -3") || (set "PY=python")
%PY% --version > nul 2>&1
if errorlevel 1 (
  echo Python est introuvable. Installer Python 3.12 depuis https://www.python.org/downloads/
  echo en cochant « Add python.exe to PATH », puis relancer ce fichier.
  pause
  exit /b 1
)

echo [1/4] Environnement de construction (.venv-build)
if not exist .venv-build ( %PY% -m venv .venv-build || goto :erreur )
call .venv-build\Scripts\activate.bat || goto :erreur

echo [2/4] Dépendances
python -m pip install --quiet --upgrade pip || goto :erreur
pip install --quiet . pyinstaller || goto :erreur

echo [3/4] Tests (quelques minutes)
python -m unittest discover -s tests -q
if errorlevel 1 (
  echo.
  echo ATTENTION : des tests échouent sur ce PC. La construction continue ;
  echo transmettre le message ci-dessus avant d'utiliser le Setup en production.
  echo.
)

echo [4/4] Construction des exécutables
pyinstaller --noconfirm --clean --onefile --console --name HeptagonesLM --icon packaging/heptagoneslm.ico ^
  --collect-submodules heptagoneslm --add-data "heptagoneslm/web/static:heptagoneslm/web/static" ^
  --add-data "heptagoneslm/setup/static:heptagoneslm/setup/static" heptagoneslm/launcher.py || goto :erreur
pyinstaller --noconfirm --onefile --console --uac-admin --name HeptagonesLM-Setup --icon packaging/heptagoneslm.ico ^
  --collect-submodules heptagoneslm --add-data "heptagoneslm/web/static:heptagoneslm/web/static" ^
  --add-data "heptagoneslm/setup/static:heptagoneslm/setup/static" --add-data "dist/HeptagonesLM.exe:." heptagoneslm/launcher.py || goto :erreur

echo.
echo Terminé : %CD%\dist\HeptagonesLM-Setup.exe
echo (seul fichier à utiliser pour installer ; HeptagonesLM.exe est inclus dedans)
explorer /select,"%CD%\dist\HeptagonesLM-Setup.exe"
pause
exit /b 0

:erreur
echo.
echo Échec de la construction : voir le message ci-dessus.
pause
exit /b 1
