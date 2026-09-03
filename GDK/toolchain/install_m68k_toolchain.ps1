$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$GdkDir = Split-Path -Parent $ScriptDir
$Root = Split-Path -Parent $GdkDir
$ToolchainDir = Join-Path $Root 'TOOLCHAIN'
$Dest = Join-Path $ToolchainDir 'm68k-elf'
$ReportDir = Join-Path $Root 'DOCS_REPORTS\current'
$Report = Join-Path $ReportDir 'TOOLCHAIN_REPORT.txt'
$Url = 'https://github.com/iratahack/m68k-elf-gcc/releases/download/v14.2.0_latest/i686-w64-mingw32.zip'
$ReleasePage = 'https://github.com/iratahack/m68k-elf-gcc/releases/tag/v14.2.0_latest'
$TempRoot = Join-Path $env:TEMP ('dms_m68k_toolchain_' + [guid]::NewGuid().ToString('N'))
$Zip = Join-Path $TempRoot 'i686-w64-mingw32.zip'
$Extract = Join-Path $TempRoot 'extract'

New-Item -ItemType Directory -Force -Path $ToolchainDir, $ReportDir, $TempRoot, $Extract | Out-Null
$lines = New-Object System.Collections.Generic.List[string]
function Log([string]$s) { Write-Host $s; $lines.Add($s) }
function Save-Report { $lines | Set-Content -Encoding UTF8 $Report }

try {
    Log 'DMS-GDK - RAPPORT TOOLCHAIN 68000'
    Log ('Date : ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
    Log ('Source : ' + $ReleasePage)
    Log ('Archive Windows : ' + $Url)
    Log ''

    $existing = Join-Path $Dest 'bin\m68k-elf-gcc.exe'
    if (Test-Path $existing) {
        Log 'Toolchain locale deja presente : verification sans retelechargement.'
    } else {
        Log 'Telechargement du ZIP Windows GCC 14.2.0...'
        try {
            Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
        } catch {
            Log ('ERREUR telechargement : ' + $_.Exception.Message)
            Log 'La page de release va etre ouverte pour telechargement manuel.'
            Start-Process $ReleasePage
            Log 'Telecharge i686-w64-mingw32.zip puis relance ce script apres l avoir place dans TOOLCHAIN\DOWNLOAD\.'
            throw
        }
        Log ('Archive recue : ' + ((Get-Item $Zip).Length) + ' octets')
        Log 'Extraction...'
        Expand-Archive -LiteralPath $Zip -DestinationPath $Extract -Force
        $gcc = Get-ChildItem -Path $Extract -Recurse -Filter 'm68k-elf-gcc.exe' | Select-Object -First 1
        if (-not $gcc) { throw 'm68k-elf-gcc.exe absent de l archive telechargee.' }
        $binDir = Split-Path -Parent $gcc.FullName
        $srcRoot = Split-Path -Parent $binDir
        if (Test-Path $Dest) { Remove-Item -Recurse -Force $Dest }
        Copy-Item -Recurse -Force $srcRoot $Dest
        Log ('Installe dans : ' + $Dest)
    }

    $bin = Join-Path $Dest 'bin'
    $required = @('m68k-elf-gcc.exe','m68k-elf-as.exe','m68k-elf-ld.exe','m68k-elf-objcopy.exe','m68k-elf-objdump.exe','m68k-elf-ar.exe')
    foreach ($name in $required) {
        $p = Join-Path $bin $name
        if (-not (Test-Path $p)) { throw ('Outil obligatoire absent : ' + $name) }
        Log ('OK : ' + $name)
    }

    $gccExe = Join-Path $bin 'm68k-elf-gcc.exe'
    $ldExe = Join-Path $bin 'm68k-elf-ld.exe'
    $objdumpExe = Join-Path $bin 'm68k-elf-objdump.exe'
    Log ''
    Log 'Version GCC :'
    (& $gccExe --version | Select-Object -First 1) | ForEach-Object { Log ('  ' + $_) }
    Log 'Version LD :'
    (& $ldExe --version | Select-Object -First 1) | ForEach-Object { Log ('  ' + $_) }

    $smokeC = Join-Path $TempRoot 'dms_gcc_smoke.c'
    $smokeO = Join-Path $TempRoot 'dms_gcc_smoke.o'
    @'
#include <stdint.h>
static volatile uint8_t * const DMS_VDP_MODE = (volatile uint8_t*)0x300002u;
void dms_gcc_smoke(void) { *DMS_VDP_MODE = 0; }
'@ | Set-Content -Encoding ASCII $smokeC

    Log ''
    Log 'Test C -> objet Motorola 68000...'
    & $gccExe -m68000 -Os -ffreestanding -fno-builtin -fomit-frame-pointer -nostdlib -c $smokeC -o $smokeO
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $smokeO)) { throw 'Compilation smoke test echouee.' }
    $objInfo = & $objdumpExe -f $smokeO
    $objInfo | ForEach-Object { if ($_ -match 'file format|architecture') { Log ('  ' + $_.Trim()) } }
    Log 'PASS : GCC produit bien un objet m68k.'
    Log ''
    Log 'IMPORTANT : ce test valide la toolchain. Le runtime CPU DMS-1 doit encore passer du subset bootstrap a un coeur 68000 complet avant de lancer librement tout code GCC.'
    Save-Report
    exit 0
}
catch {
    Log ''
    Log ('ECHEC : ' + $_.Exception.Message)
    Save-Report
    exit 2
}
finally {
    if (Test-Path $TempRoot) { Remove-Item -Recurse -Force $TempRoot -ErrorAction SilentlyContinue }
}
