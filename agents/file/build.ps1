#Requires -Version 7.0 -PSEdition Core

# Build the File Agent executable, and optionally the Windows installer.
#
#   ./build.ps1                              # exe only, version from git
#   ./build.ps1 -Version v1.4.0 -Installer   # stamped exe + installer (CI)
#
# -Installer requires Inno Setup 6 (preinstalled on GitHub windows runners;
# locally: winget install JRSoftware.InnoSetup).

param(
    [string]$Version,
    [switch]$Installer
)

$ErrorActionPreference = 'Stop'

# resolve the version: parameter > git describe > 'dev'
if (-not $Version) {
    $Version = (git describe --tags --always 2>$null) ?? 'dev'
}
Write-Host "Building Mascope File Agent $Version"

# bake the version into the package so the frozen exe can report it
Set-Content -Path './src/mascope_file_agent/_version.py' `
    -Value "__version__ = `"$Version`""

# create the binary in the virtual env
uv run pyinstaller @(
    './src/mascope_file_agent/main.py'
    '--onefile', '--name', 'Mascope-File-Agent'   # make one executable file
    '--noconfirm'                                 # replace dist w/o confirming
    '--console'                                   # open the console for logs
    '--icon=assets/icon.ico'                      # use the Mascope icon
    '--collect-all', 'mascope_runtime'            # bundle runtime lib
    '--collect-all', 'mascope_sdk'                # bundle mascope api wrapper
    '--collect-all', 'tzlocal'                    # name this machine's IANA zone
)
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }

if ($Installer) {
    # locate ISCC.exe (Inno Setup 6)
    $iscc = (Get-Command iscc -ErrorAction SilentlyContinue)?.Source
    if (-not $iscc) {
        $iscc = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $iscc) {
        throw 'Inno Setup 6 (ISCC.exe) not found - install it or drop -Installer'
    }

    # VersionInfoVersion must be numeric a.b.c.d; derive it from a vX.Y.Z
    # release tag, fall back to zeros for dev/dated builds
    $numericVersion = if ($Version -match '^v?(\d+)\.(\d+)\.(\d+)$') {
        "$($Matches[1]).$($Matches[2]).$($Matches[3]).0"
    } else {
        '0.0.0.0'
    }

    & $iscc "/DAppVersion=$Version" "/DFileVersion=$numericVersion" 'installer.iss'
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
    Write-Host "Installer built: dist\Mascope-File-Agent-Setup.exe"
}
