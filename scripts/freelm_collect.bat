@echo off
REM Collecte FreeLM - à planifier toutes les 5 minutes (voir register_tasks.ps1)
setlocal
if "%FREELM_HOME%"=="" set FREELM_HOME=C:\FreeLM
cd /d "%FREELM_HOME%"
"%FREELM_HOME%\venv\Scripts\python.exe" -m freelm -c "%FREELM_HOME%\freelm.yaml" collect
exit /b %ERRORLEVEL%
