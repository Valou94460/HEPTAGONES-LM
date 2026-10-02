<#
  Signature de code (Authenticode) de HeptagonesLM.exe et HeptagonesLM-Setup.exe.

  - Certificat réel : variables d'environnement SIGN_PFX_BASE64 (fichier .pfx encodé en base64) et
    SIGN_PFX_PASSWORD (secrets GitHub du même nom). L'alerte SmartScreen « éditeur inconnu » disparaît
    (immédiatement avec un certificat EV, après quelques téléchargements avec un certificat OV).
  - Sans certificat (développement, CI des branches) : certificat de TEST auto-signé, approuvé sur la
    seule machine de construction, pour valider la procédure. Les exécutables ainsi signés restent
    « éditeur inconnu » sur les autres postes.

  Horodatage RFC 3161 : la signature reste valide après l'expiration du certificat.
  Usage : packaging\sign.ps1 dist\HeptagonesLM.exe [dist\HeptagonesLM-Setup.exe ...]
#>
param([Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)][string[]]$Files)
$ErrorActionPreference = "Stop"
$timestamp = if ($env:SIGN_TIMESTAMP_URL) { $env:SIGN_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }

if ($env:SIGN_PFX_BASE64) {
  $pfx = Join-Path $env:RUNNER_TEMP "heptagoneslm-signature.pfx"
  [IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:SIGN_PFX_BASE64))
  try {
    $secure = New-Object System.Security.SecureString
    foreach ($ch in [string]$env:SIGN_PFX_PASSWORD -split "") { if ($ch) { $secure.AppendChar([char]$ch) } }
    $cert = (Import-PfxCertificate -FilePath $pfx -CertStoreLocation Cert:\CurrentUser\My -Password $secure |
      Where-Object { $_.HasPrivateKey } | Select-Object -First 1)
  } finally { Remove-Item $pfx -Force -ErrorAction SilentlyContinue }
  $kind = "certificat de l'éditeur"
} else {
  $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=HeptagonesLM (certificat de test)" `
    -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddDays(30)
  # Approuvé uniquement sur cette machine, pour que la vérification ci-dessous soit complète
  $tmp = Join-Path $env:TEMP "heptagoneslm-test.cer"
  Export-Certificate -Cert $cert -FilePath $tmp | Out-Null
  Import-Certificate -FilePath $tmp -CertStoreLocation Cert:\LocalMachine\Root | Out-Null
  Import-Certificate -FilePath $tmp -CertStoreLocation Cert:\LocalMachine\TrustedPublisher | Out-Null
  $kind = "certificat de TEST (aucun secret SIGN_PFX_BASE64)"
}
if (-not $cert) { throw "Certificat de signature introuvable" }
Write-Host "Signature avec le $kind : $($cert.Subject) · empreinte $($cert.Thumbprint) · valide jusqu'au $($cert.NotAfter.ToString('dd/MM/yyyy'))"

foreach ($file in $Files) {
  $sig = $null
  for ($i = 1; $i -le 3; $i++) {        # serveur d'horodatage parfois indisponible : nouvelle tentative
    $sig = Set-AuthenticodeSignature -FilePath $file -Certificate $cert -HashAlgorithm SHA256 -TimestampServer $timestamp
    if ($sig.Status -eq "Valid") { break }
    Start-Sleep -Seconds (5 * $i)
  }
  $check = Get-AuthenticodeSignature -FilePath $file
  if ($check.Status -ne "Valid") { throw "$file : signature $($check.Status) — $($check.StatusMessage)" }
  if (-not $check.TimeStamperCertificate) { throw "$file : signature non horodatée" }
  Write-Host ("{0} : signé ({1}), horodaté par {2}" -f (Split-Path $file -Leaf), $check.SignerCertificate.Subject,
    $check.TimeStamperCertificate.Subject)
}
