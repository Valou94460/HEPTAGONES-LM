@echo off
REM Rapport Excel HeptagonesLM du mois précédent - à planifier chaque jour (généré une fois par mois)
setlocal
if "%HEPTAGONESLM_HOME%"=="" set HEPTAGONESLM_HOME=C:\HeptagonesLM
cd /d "%HEPTAGONESLM_HOME%"
"%HEPTAGONESLM_HOME%\venv\Scripts\python.exe" -m heptagoneslm -c "%HEPTAGONESLM_HOME%\heptagoneslm.yaml" report --period last-month --skip-existing %*
exit /b %ERRORLEVEL%
