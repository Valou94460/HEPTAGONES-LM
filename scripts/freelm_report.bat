@echo off
REM Rapport Excel FreeLM du mois précédent - à planifier chaque jour (généré une fois par mois)
setlocal
if "%FREELM_HOME%"=="" set FREELM_HOME=C:\FreeLM
cd /d "%FREELM_HOME%"
"%FREELM_HOME%\venv\Scripts\python.exe" -m freelm -c "%FREELM_HOME%\freelm.yaml" report --period last-month --skip-existing %*
exit /b %ERRORLEVEL%
