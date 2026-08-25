#Requires -Version 7.0 -PSEdition Core

# Build the File Agent executable, and optionally the Windows installer.
#
#   ./build.ps1                              # exe only, version from git
#   ./build.ps1 -Version v1.4.0 -Installer   # stamped exe + installer (CI)
#
#   # signed release build against a production certificate profile - what
#   # the release workflow runs, and the only form that may be shipped
#   ./build.ps1 -Version v1.4.0 -Installer -Sign `
#       -SigningAccount <account> -SigningProfile <profile>
#
#   # local rehearsal against a Public Trust *Test* profile, which needs
#   # -AllowTestCertificate: without it the Lifetime Signing EKU those
#   # certificates carry fails the build, which is what keeps a 72-hour
#   # installer out of a release
#   ./build.ps1 -Version v0.0.0-rehearsal -Installer -Sign `
#       -SigningAccount <account> -SigningProfile <profile>-test `
#       -AllowTestCertificate
#
# -Installer requires Inno Setup 6 (preinstalled on GitHub windows runners;
# locally: winget install JRSoftware.InnoSetup). Signing additionally needs
# Inno Setup 6.3+, for the signcheck flag installer.iss relies on.
#
# -Sign is opt-in Authenticode signing through Azure Artifact Signing (the
# service formerly called Trusted Signing). Leave it off and the build is
# exactly what it always was, so a local dev build needs no Azure account
# and no signing tooling. Switch it on and three files get signed, in this
# order: the PyInstaller exe (before Inno Setup compresses it into the
# installer, after which it can no longer be signed), the uninstaller stub,
# and the finished setup exe. Signing needs an Azure identity already
# logged in - `az login` locally, azure/login OIDC in CI - plus signtool.exe
# from a recent Windows SDK and the Artifact Signing dlib. See
# docs/dev/developer_guide.md, "Signing the File Agent installer".

param(
    [string]$Version,
    [switch]$Installer,
    [switch]$Sign,
    # Empty counts as "not given" and falls back to $DefaultSigningEndpoint
    # below. It cannot be a parameter default: CI passes the
    # AZURE_SIGNING_ENDPOINT repository variable straight through and that
    # variable is normally unset, and PowerShell applies a default only when
    # the parameter is unbound - an empty string still binds and would win.
    [string]$SigningEndpoint,
    [string]$SigningAccount,
    [string]$SigningProfile,
    # Rehearsal escape hatch, for local runs against a Public Trust *Test*
    # certificate profile. Without it a test signature fails the build, which
    # is what keeps a 72-hour installer out of a release; see the verification
    # block at the bottom.
    [switch]$AllowTestCertificate
)

$ErrorActionPreference = 'Stop'

# North Europe, the region holding the Artifact Signing account. Override
# with -SigningEndpoint when the account and its certificate profile live
# somewhere else.
$DefaultSigningEndpoint = 'https://neu.codesigning.azure.net'

# resolve the version: parameter > git describe > 'dev'
if (-not $Version) {
    $Version = (git describe --tags --always 2>$null) ?? 'dev'
}
Write-Host "Building Mascope File Agent $Version"

# --------------------------------------------------------------------------
# Preflight. Everything external this build depends on is resolved here,
# before PyInstaller runs, because PyInstaller takes minutes and none of
# these checks need its output. Discovering a missing Inno Setup or an
# unusable signing setup afterwards throws that time away, and in a release
# run it leaves the release with no installer asset at all.
# --------------------------------------------------------------------------

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
}

if ($Sign) {
    # Repository variables reach us verbatim - GitHub does not trim them - so
    # normalise before validating. A value copy-pasted out of the Azure portal
    # with a trailing space is otherwise non-empty, passes the check below,
    # and only fails at the sign call minutes later; and "   " would pass as
    # an account name entirely. Quoting makes this safe when a parameter was
    # never bound at all.
    $SigningAccount  = "$SigningAccount".Trim()
    $SigningProfile  = "$SigningProfile".Trim()
    $SigningEndpoint = "$SigningEndpoint".Trim()

    if (-not $SigningAccount -or -not $SigningProfile) {
        throw '-Sign needs -SigningAccount and -SigningProfile'
    }
    if (-not $SigningEndpoint) { $SigningEndpoint = $DefaultSigningEndpoint }

    # signtool.exe must come from Windows SDK 10.0.22621.755 or newer; the
    # Artifact Signing dlib refuses older ones, and the symptom is the
    # misleading "No certificates were found that met all the given
    # criteria" - as if the certificate rather than the tool were wrong.
    # Take an explicit override, else the newest x64 build installed.
    $signtool = $env:MASCOPE_SIGNTOOL
    if (-not $signtool) {
        $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" `
            -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -as [version] } |
            Sort-Object { [version]$_.Name } |
            ForEach-Object { Join-Path $_.FullName 'x64\signtool.exe' } |
            Where-Object { Test-Path $_ } |
            Select-Object -Last 1
    }
    # Test-Path again rather than only in the discovery branch above: an
    # override that points at a path which no longer exists would otherwise
    # sail through the preflight and fail at the sign call, minutes later.
    if (-not $signtool -or -not (Test-Path $signtool)) {
        throw 'signtool.exe not found - install the Windows SDK or set MASCOPE_SIGNTOOL'
    }
    $signtoolVersion = [version](
        (Get-Item $signtool).VersionInfo.ProductVersion -replace '[^0-9.].*$', ''
    )
    if ($signtoolVersion -lt [version]'10.0.22621.755') {
        throw "signtool $signtoolVersion is too old for the Artifact Signing dlib (need 10.0.22621.755+): $signtool"
    }

    # the dlib is the Microsoft.ArtifactSigning.Client NuGet package - CI
    # unpacks a pinned version and points MASCOPE_SIGNING_DLIB at it - or
    # the Artifact Signing Client Tools MSI for a local build
    # (winget install -e --id Microsoft.Azure.ArtifactSigningClientTools)
    $dlib = $env:MASCOPE_SIGNING_DLIB
    if (-not $dlib) {
        $dlib = @(
            "$env:LOCALAPPDATA\Microsoft\ArtifactSigningClientTools\bin\x64\Azure.CodeSigning.Dlib.dll"
            "$env:ProgramFiles\Microsoft\ArtifactSigningClientTools\bin\x64\Azure.CodeSigning.Dlib.dll"
            "${env:ProgramFiles(x86)}\Microsoft\ArtifactSigningClientTools\bin\x64\Azure.CodeSigning.Dlib.dll"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    # as with signtool: the override branch has not been Test-Path'd, and CI
    # is the branch that takes it
    if (-not $dlib -or -not (Test-Path $dlib)) {
        throw 'Azure.CodeSigning.Dlib.dll not found - set MASCOPE_SIGNING_DLIB'
    }

    # the dlib reads the account and profile from a JSON file, not the
    # command line. Endpoint must match the region that holds the account
    # AND the certificate profile, or signing fails with 403 / a
    # SignerSign() error that reads like a permissions problem but is not.
    # Every DefaultAzureCredential source except the Azure CLI is excluded,
    # so a local build and a CI build authenticate the same way and no
    # probe can silently pick up a different identity.
    $metadata = Join-Path (New-Item -ItemType Directory -Force -Path './build').FullName `
        'artifact-signing.json'
    $signingMetadata = [ordered]@{
        Endpoint               = $SigningEndpoint
        CodeSigningAccountName = $SigningAccount
        CertificateProfileName = $SigningProfile
        ExcludeCredentials     = @(
            'EnvironmentCredential', 'ManagedIdentityCredential'
            'WorkloadIdentityCredential', 'SharedTokenCacheCredential'
            'VisualStudioCredential', 'VisualStudioCodeCredential'
            'AzurePowerShellCredential', 'AzureDeveloperCliCredential'
            'InteractiveBrowserCredential'
        )
    }
    # optional; CI passes the workflow run id, so a signature in the
    # account's Sign Transactions log traces back to the build that made it
    if ($env:MASCOPE_SIGNING_CORRELATION_ID) {
        $signingMetadata.CorrelationId = $env:MASCOPE_SIGNING_CORRELATION_ID
    }
    $signingMetadata | ConvertTo-Json | Set-Content -Path $metadata

    # /tr and /td are not optional: an Artifact Signing certificate is valid
    # for 72 hours, so without an RFC 3161 countersignature every installer
    # stops verifying three days after the release was cut
    $signArgs = @(
        'sign', '/v'
        '/fd', 'SHA256'                                # digest of the file
        '/tr', 'http://timestamp.acs.microsoft.com'    # timestamp authority
        '/td', 'SHA256'                                # digest of the stamp
        '/dlib', $dlib                                 # Artifact Signing plugin
        '/dmdf', $metadata                             # account and profile
    )

    # Inno Setup takes a sign tool as a name -> command string defined on the
    # ISCC command line. $q is its placeholder for a double quote and $f for
    # the file being signed (that one ISCC quotes itself). Single quotes stop
    # PowerShell expanding $q and $f as variables of its own, and using $q
    # rather than literal quotes keeps the whole command one argument through
    # PowerShell's native-command re-quoting.
    $signCommand = '$q' + $signtool + '$q sign /v /fd SHA256' +
        ' /tr http://timestamp.acs.microsoft.com /td SHA256' +
        ' /dlib $q' + $dlib + '$q' +
        ' /dmdf $q' + $metadata + '$q $f'
}

# --------------------------------------------------------------------------
# Build.
# --------------------------------------------------------------------------

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

if ($Sign) {
    # Sign the payload exe now, while it is still a standalone PE. Inno
    # Setup stores it verbatim and Authenticode covers the appended
    # PyInstaller archive, so this has to happen before ISCC compresses it -
    # afterwards the bytes are unreachable, and patching them would break
    # the installer's own signature. installer.iss re-asserts the ordering
    # with the signcheck flag.
    Write-Host 'Signing dist\Mascope-File-Agent.exe'
    & $signtool @signArgs './dist/Mascope-File-Agent.exe'
    if ($LASTEXITCODE -ne 0) { throw 'Signing the executable failed' }
}

if ($Installer) {
    $isccArgs = @("/DAppVersion=$Version", "/DFileVersion=$numericVersion")
    if ($Sign) {
        # /S defines the sign tool, /D switches installer.iss onto its
        # signing branch - so an unsigned build never names a tool that was
        # never defined, and only a signing build needs Inno Setup 6.3+
        $isccArgs += @("/Smascope=$signCommand", '/DSignToolName=mascope')
    }
    $isccArgs += 'installer.iss'

    # Splatting an array at a NATIVE command is plain argv passing and is
    # safe. Splatting one at a PowerShell script is not: there the elements
    # bind positionally, so "-Installer" would arrive as a value rather than
    # as a switch. Anything invoking build.ps1 itself must pass named
    # parameters (or splat a hashtable).
    & $iscc @isccArgs
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
    Write-Host "Installer built: dist\Mascope-File-Agent-Setup.exe"
}

if ($Sign) {
    # Verify what is about to ship. Two of these checks hold for every
    # profile: a signature has to be present, and it has to carry an RFC
    # 3161 countersignature - that is the failure mode that otherwise ships
    # green and breaks 72 hours later, in customers' hands.
    $signed = @('./dist/Mascope-File-Agent.exe')
    if ($Installer) { $signed += './dist/Mascope-File-Agent-Setup.exe' }
    foreach ($file in $signed) {
        $sig = Get-AuthenticodeSignature $file
        if (-not $sig.SignerCertificate) { throw "Not signed: $file" }
        if (-not $sig.TimeStamperCertificate) { throw "Not timestamped: $file" }

        # A Public Trust *Test* profile deliberately chains to a root that is
        # not in any trust store, so full Authenticode verification cannot
        # pass on it. Detect it by the Lifetime Signing EKU, which is what
        # marks these certificates as test-only and is also why anything
        # signed with one expires after 72 hours no matter how well it is
        # timestamped. That makes it a build failure by default: the release
        # job never passes -AllowTestCertificate, so a certificate profile
        # variable left pointing at a test profile stops the release instead
        # of shipping customers an installer that dies in three days.
        $ekus = $sig.SignerCertificate.Extensions |
            Where-Object { $_.Oid.Value -eq '2.5.29.37' } |
            ForEach-Object { $_.EnhancedKeyUsages.Value }
        if ($ekus -contains '1.3.6.1.4.1.311.10.3.13') {
            if (-not $AllowTestCertificate) {
                $subject = $sig.SignerCertificate.Subject
                throw "TEST certificate ($subject) signed $file - it expires 72 " +
                    'hours from signing and must never be released. Pass ' +
                    '-AllowTestCertificate to rehearse the pipeline with it.'
            }
            Write-Warning ("TEST certificate ({0}) - skipping chain validation. " -f `
                $sig.SignerCertificate.Subject)
            Write-Warning "$file expires 72 hours from signing and must never be released."
            continue
        }

        # Production: full Authenticode policy. /pa is required - without it
        # signtool applies the *driver* policy and fails a perfectly good
        # installer - and /tw turns a missing countersignature into exit
        # code 2, so anything but 0 is fatal.
        & $signtool verify /pa /v /all /tw $file
        if ($LASTEXITCODE -ne 0) { throw "Signature verification failed: $file" }
    }
}
