$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Runtime = Join-Path $Root "RUNTIME"
$ThirdParty = Join-Path $Runtime "third_party\musashi"
$Build = Join-Path $Runtime "build"
$Bridge = Join-Path $Runtime "native_cpu\dms1_m68k_bridge.c"
$ReportDir = Join-Path $Root "DOCS_REPORTS\current"
$Report = Join-Path $ReportDir "FULL_68000_CORE_REPORT.txt"
$Commit = "2158f7081001f89145283d291ee501321e8dc26d"
$ZipUrl = "https://github.com/kstenerud/Musashi/archive/${Commit}.zip"
$Temp = Join-Path $env:TEMP ("dms_musashi_" + [guid]::NewGuid().ToString("N"))
$Zip = Join-Path $Temp "musashi.zip"

New-Item -ItemType Directory -Force -Path $Temp,$ThirdParty,$Build,$ReportDir | Out-Null

function Add-Report([string]$Text) {
  # IMPORTANT P1.2.5: a logging helper must not emit objects on PowerShell's
  # success pipeline. Tee-Object did exactly that, so callers capturing the
  # return value of Invoke-LoggedNative received log strings + the numeric
  # exit code. That made a successful GCC exit code 0 look like a failure.
  Add-Content -Path $Report -Value $Text -Encoding UTF8
  Write-Host $Text
}
function Fail([string]$Text) { Add-Report ("ERREUR : " + $Text); throw $Text }
function Invoke-LoggedNative {
  param(
    [Parameter(Mandatory=$true)][string]$Exe,
    [string[]]$Arguments = @(),
    [Parameter(Mandatory=$true)][string]$Label
  )
  Add-Report ("Commande " + $Label + " : " + $Exe + " " + ($Arguments -join " "))

  # Windows PowerShell 5.1 can promote a native program's stderr to a
  # NativeCommandError when the global ErrorActionPreference is Stop.  That
  # prevents us from inspecting LASTEXITCODE and is not how native compilers
  # should be handled.  Capture stdout/stderr normally, then decide from the
  # process exit code ourselves.
  $SavedErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $nativeOutput = & $Exe @Arguments 2>&1
    $nativeCode = $LASTEXITCODE
  }
  catch {
    Add-Report ("Exception native " + $Label + " : " + $_.Exception.Message)
    return 9009
  }
  finally {
    $ErrorActionPreference = $SavedErrorActionPreference
  }

  if ($null -ne $nativeOutput) {
    foreach ($line in $nativeOutput) { Add-Report ([string]$line) }
  }
  Add-Report ("Code retour " + $Label + " : " + $nativeCode)
  return $nativeCode
}

"DMS-GDK P1.2.10 - RAPPORT COEUR 68000 COMPLET" | Set-Content -Encoding UTF8 $Report
Add-Report ("Date : " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
Add-Report ("Musashi commit : " + $Commit)
Add-Report ("Source : https://github.com/kstenerud/Musashi")

$Candidates = @(
  "C:\msys64\ucrt64\bin\gcc.exe",
  "C:\msys64\mingw64\bin\gcc.exe",
  "C:\msys64\clang64\bin\clang.exe",
  "C:\ucrt64\bin\gcc.exe"
)
$HostCC = $null
foreach ($c in $Candidates) { if (Test-Path $c) { $HostCC = $c; break } }
if (-not $HostCC) {
  $cmd = Get-Command gcc.exe -ErrorAction SilentlyContinue
  if ($cmd) { $HostCC = $cmd.Source }
}
if (-not $HostCC) { Fail "compilateur Windows GCC/MinGW introuvable. Vérifie l'installation MSYS2." }

# IMPORTANT P1.2.2:
# Calling C:\msys64\...\gcc.exe directly from PowerShell is not enough on every
# MSYS2 installation. GCC launches helpers (cc1/as/ld) that can require DLLs from
# the selected MinGW environment. Recreate the essential environment PATH before
# invoking any native compiler tool.
$HostBin = Split-Path -Parent $HostCC
$MsysUsrBin = "C:\msys64\usr\bin"
$OldPath = $env:PATH
$PathParts = @($HostBin)
if (Test-Path $MsysUsrBin) { $PathParts += $MsysUsrBin }
$PathParts += $OldPath
$env:PATH = ($PathParts -join ";")
Add-Report ("Compilateur hôte : " + $HostCC)
Add-Report ("Environnement hôte ajouté au PATH : " + $HostBin)
if (Test-Path $MsysUsrBin) { Add-Report ("Outils MSYS2 ajoutés au PATH : " + $MsysUsrBin) }

try {
  $ccVersionCode = Invoke-LoggedNative -Exe $HostCC -Arguments @("--version") -Label "HOST_CC_VERSION"
  if ($ccVersionCode -ne 0) { Fail "le compilateur hôte existe mais ne démarre pas correctement" }

  # Reuse a previously downloaded source tree after a failed P1.2 attempt.
  $Pinned = Join-Path $ThirdParty ("Musashi-" + $Commit)
  $HavePinned = (Test-Path (Join-Path $Pinned "m68kmake.c")) -and (Test-Path (Join-Path $Pinned "m68k_in.c"))
  if ($HavePinned) {
    Write-Host "[1/5] Sources Musashi déjà présentes - réutilisation..."
    Add-Report ("Sources réutilisées : " + $Pinned)
  } else {
    Write-Host "[1/5] Telechargement du coeur Motorola 68000 Musashi..."
    Invoke-WebRequest -Uri $ZipUrl -OutFile $Zip -UseBasicParsing
    Add-Report ("Archive reçue : " + (Get-Item $Zip).Length + " octets")

    Write-Host "[2/5] Extraction..."
    $Extract = Join-Path $Temp "extract"
    Expand-Archive -Path $Zip -DestinationPath $Extract -Force
    $SourceDir = Get-ChildItem $Extract -Directory | Select-Object -First 1
    if (-not $SourceDir) { Fail "archive Musashi extraite mais dossier source introuvable" }
    if (Test-Path $Pinned) { Remove-Item $Pinned -Recurse -Force }
    Copy-Item $SourceDir.FullName $Pinned -Recurse -Force
    Add-Report ("Sources : " + $Pinned)
  }
  if ($HavePinned) { Write-Host "[2/5] Extraction déjà effectuée." }

  Write-Host "[3/5] Generation de la table complete des opcodes 68000..."
  $GenExe = Join-Path $Pinned "m68kmake.exe"
  if (Test-Path $GenExe) { Remove-Item $GenExe -Force }
  $genCode = Invoke-LoggedNative -Exe $HostCC -Arguments @("-O2", (Join-Path $Pinned "m68kmake.c"), "-o", $GenExe) -Label "M68KMAKE_COMPILE"
  if ($genCode -ne 0 -or -not (Test-Path $GenExe)) {
    Fail "compilation de m68kmake.exe impossible. Le détail GCC est maintenant enregistré juste au-dessus dans FULL_68000_CORE_REPORT.txt"
  }

  Push-Location $Pinned
  try {
    $genRunCode = Invoke-LoggedNative -Exe $GenExe -Arguments @() -Label "M68KMAKE_RUN"
  } finally { Pop-Location }
  if ($genRunCode -ne 0 -or -not (Test-Path (Join-Path $Pinned "m68kops.c")) -or -not (Test-Path (Join-Path $Pinned "m68kops.h"))) {
    Fail "génération m68kops.c/m68kops.h impossible"
  }
  Add-Report ("m68kops.c : " + (Get-Item (Join-Path $Pinned "m68kops.c")).Length + " octets")
  Add-Report ("m68kops.h : " + (Get-Item (Join-Path $Pinned "m68kops.h")).Length + " octets")

  Write-Host "[4/5] Compilation de dms1_m68k.dll..."
  $Dll = Join-Path $Build "dms1_m68k.dll"
  if (Test-Path $Dll) { Remove-Item $Dll -Force }
  $DllArgs = @(
    "-O3", "-DNDEBUG", "-shared", "-static-libgcc", "-std=c99",
    ("-I" + $Pinned),
    $Bridge,
    (Join-Path $Pinned "m68kcpu.c"),
    (Join-Path $Pinned "m68kops.c"),
    (Join-Path $Pinned "softfloat\softfloat.c"),
    "-o", $Dll,
    "-lm"
  )
  $dllCode = Invoke-LoggedNative -Exe $HostCC -Arguments $DllArgs -Label "MUSASHI_DLL_COMPILE"
  if ($dllCode -ne 0 -or -not (Test-Path $Dll)) {
    Fail "compilation de dms1_m68k.dll impossible. Consulte les diagnostics GCC dans FULL_68000_CORE_REPORT.txt"
  }
  Add-Report ("DLL : " + $Dll)
  Add-Report ("DLL taille : " + (Get-Item $Dll).Length + " octets")

  Write-Host "[5/5] Test du pont Python <-> 68000..."
  $Py = "python"
  & $Py -c "import sys; raise SystemExit(0 if sys.version_info.major==3 else 1)" *> $null
  if ($LASTEXITCODE -ne 0) { $Py = "py" }
  if ($Py -eq "py") { & $Py -3 (Join-Path $Root "GDK\tools\dms_full68000_check.py") }
  else { & $Py (Join-Path $Root "GDK\tools\dms_full68000_check.py") }
  if ($LASTEXITCODE -ne 0) { Fail "le test fonctionnel du coeur 68000 a échoué" }

  Add-Report "PASS : coeur Motorola 68000 complet installé et chargeable par DMS-1."
  Write-Host ""
  Write-Host "PASS : coeur 68000 complet installe." -ForegroundColor Green
  Write-Host "Tu peux maintenant lancer TEST_GCC_68000.bat"
}
finally {
  $env:PATH = $OldPath
  if (Test-Path $Temp) { Remove-Item $Temp -Recurse -Force -ErrorAction SilentlyContinue }
}
