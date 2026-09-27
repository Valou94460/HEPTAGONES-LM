@echo off
REM Collecte HeptagonesLM - à planifier toutes les 5 minutes (voir register_tasks.ps1)
setlocal
if "%HEPTAGONESLM_HOME%"=="" set HEPTAGONESLM_HOME=C:\HeptagonesLM
cd /d "%HEPTAGONESLM_HOME%"
"%HEPTAGONESLM_HOME%\venv\Scripts\python.exe" -m heptagoneslm -c "%HEPTAGONESLM_HOME%\heptagoneslm.yaml" collect
exit /b %ERRORLEVEL%
