<#
  Lance HeptagonesLM-Setup.exe en ligne de commande et attend la fin.

  Le Setup est une application fenêtrée (aucune console au double-clic) : PowerShell n'attend pas une
  telle application lancée avec « & », d'où Start-Process -Wait. La sortie (rebranchée par le Setup
  sur les canaux redirigés) est affichée et gardée dans setup-last.txt ; le code retour est celui du Setup.
  Usage : ./tests/windows/setup_cli.ps1 setup --answers answers.json
#>
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$SetupArgs)
$exe = (Resolve-Path .\livrables\dist\HeptagonesLM-Setup.exe).Path
$out = Join-Path $env:RUNNER_TEMP "setup-out.txt"
$err = Join-Path $env:RUNNER_TEMP "setup-err.txt"
$p = Start-Process -FilePath $exe -ArgumentList $SetupArgs -Wait -PassThru -NoNewWindow `
  -RedirectStandardOutput $out -RedirectStandardError $err
$lines = @(Get-Content $out -Encoding utf8) + @(Get-Content $err -Encoding utf8)
$lines | ForEach-Object { Write-Host $_ }
$lines | Set-Content setup-last.txt -Encoding utf8
exit $p.ExitCode
