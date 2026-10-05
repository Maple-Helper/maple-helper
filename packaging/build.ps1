<#
.SYNOPSIS
  Builds Maple Helper: PyInstaller app -> self-test -> portable zip -> installer (-> installer round trip).
  CI runs exactly this script; it works locally too.

.EXAMPLE
  pwsh packaging/build.ps1                              # uses data/kb; skips the installer if Inno Setup is missing
  pwsh packaging/build.ps1 -KbDir tests/fixtures/kb     # build without a real knowledge base
  pwsh packaging/build.ps1 -RequireKb -TestInstaller    # what releases run (CI adds -FastInstaller)
#>
[CmdletBinding()]
param(
    [string]$KbDir = "data/kb",
    [switch]$RequireKb,        # the self-test fails when the bundled KB is empty
    [switch]$SkipInstaller,
    [switch]$TestInstaller,    # silent install -> self-test the installed exe -> silent uninstall
    [switch]$FastInstaller,    # lighter installer compression: same installer, bigger file, much quicker (CI)
    [switch]$Force             # allow -TestInstaller outside CI (it installs/uninstalls "Maple Helper" for real)
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = if ($env:PYTHON) { $env:PYTHON } elseif (Test-Path ".venv/Scripts/python.exe") { ".venv/Scripts/python.exe" } else { "python" }
$Version = (& $Python -c "import maplehelper; print(maplehelper.__version__)").Trim()
$AppDir = Join-Path $Root "dist/Maple Helper"
$AppExe = "Maple Helper.exe"
$Release = Join-Path $Root "dist/release"
Write-Host "== Maple Helper $Version (python: $Python)"

if ($TestInstaller -and -not $env:CI -and -not $Force) {
    throw "-TestInstaller installs and uninstalls Maple Helper on this PC (same AppId as a real install). Use -Force to do it anyway."
}

# Code signing stays off until $env:MAPLEHELPER_SIGN holds a command with a {file} placeholder, e.g.
#   signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /f cert.pfx /p secret "{file}"
# (any provider works: Azure Trusted Signing, SignPath, a .pfx). See docs/RELEASING.md.
function Invoke-Sign([string]$File) {
    if (-not $env:MAPLEHELPER_SIGN) { return }
    Write-Host "== Signing $(Split-Path -Leaf $File)"
    cmd /c $env:MAPLEHELPER_SIGN.Replace("{file}", $File)
    if ($LASTEXITCODE -ne 0) { throw "Signing failed for $File" }
}

function Invoke-SelfTest([string]$Exe) {
    $report = Join-Path ([IO.Path]::GetTempPath()) "maplehelper-selftest-$([guid]::NewGuid()).txt"
    $argv = @("--selftest", "`"$report`"")
    if ($RequireKb) { $argv += "--require-kb" }
    # windowed exe: no console output; the verdict is the exit code plus the report file
    $p = Start-Process -FilePath $Exe -ArgumentList $argv -PassThru -WindowStyle Hidden
    $null = $p.Handle
    if (-not $p.WaitForExit(300000)) { $p | Stop-Process -Force; throw "Self-test hung for $Exe" }
    if (Test-Path $report) { Get-Content $report | Write-Host } else { Write-Host "(no self-test report written)" }
    if ($p.ExitCode -ne 0) { throw "Self-test failed for $Exe (exit code $($p.ExitCode))" }
}

function Stop-InstalledApp([string]$Dir) {
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Dir) } |
        ForEach-Object { Write-Host "   stopping $($_.Path)"; $_ | Stop-Process -Force }
}

# ---------------------------------------------------------------- 1. freeze
if (Test-Path (Join-Path $KbDir "index.json")) {
    $env:MAPLEHELPER_KB = (Resolve-Path $KbDir).Path
} elseif ($RequireKb) {
    throw "No knowledge base at $KbDir (index.json missing)"
} else {
    Write-Warning "No knowledge base at $KbDir; building with an empty KB"
    $env:MAPLEHELPER_KB = Join-Path $Root "build/no-kb"
}
# the exe's Windows version resource (packaging/version_info.txt) follows __version__
& $Python -c "import sys; sys.path.insert(0, 'tools'); import release; release.set_version(sys.argv[1])" $Version
if ($LASTEXITCODE -ne 0) { throw "Could not sync packaging/version_info.txt" }

Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue
& $Python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging/maplehelper.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Invoke-Sign (Join-Path $AppDir $AppExe)

# ---------------------------------------------------------------- 2. smoke test the frozen app
Invoke-SelfTest (Join-Path $AppDir $AppExe)

# ---------------------------------------------------------------- 3. portable zip
New-Item -ItemType Directory -Force $Release | Out-Null
& $Python -c "import shutil, sys; shutil.make_archive(sys.argv[1], 'zip', 'dist', 'Maple Helper')" (Join-Path $Release "MapleHelper-$Version-portable")
if ($LASTEXITCODE -ne 0) { throw "Zipping failed" }

# ---------------------------------------------------------------- 4. installer
if ($SkipInstaller) { Write-Host "== Skipping installer"; Get-ChildItem $Release | Format-Table Name, Length; return }
$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    # any major version's folder ("Inno Setup 6", "Inno Setup 7")
    $iscc = @("${env:ProgramFiles(x86)}", "$env:ProgramFiles", "$env:LOCALAPPDATA\Programs") |
        ForEach-Object { Get-ChildItem "$_\Inno Setup *\ISCC.exe" -ErrorAction SilentlyContinue } |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $iscc) { throw "Inno Setup not found. Install it (choco install innosetup) or pass -SkipInstaller." }
$isccArgs = @("/Q", "/DAppVersion=$Version")
if ($FastInstaller) { $isccArgs += "/DCompression=lzma2/fast" }
# lets an update skip the KB's ~8,000 files when the PC already has this KB (KbNeedsInstall in installer.iss)
$KbMeta = Join-Path $AppDir "_internal/data/kb/meta.json"
$KbVersion = if (Test-Path $KbMeta) { (Get-Content $KbMeta -Raw | ConvertFrom-Json).version } else { "" }
if ($KbVersion) { $isccArgs += "/DKbVersion=$KbVersion" }
& $iscc @isccArgs packaging/installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
# keep the unversioned name: the in-app updater downloads exactly "MapleHelper-Setup.exe"
$setup = Join-Path $Release "MapleHelper-Setup.exe"
Move-Item (Join-Path $Root "dist/MapleHelper-Setup.exe") $setup -Force
Invoke-Sign $setup

# ---------------------------------------------------------------- 5. installer round trip
if ($TestInstaller) {
    $target = Join-Path ([IO.Path]::GetTempPath()) "MapleHelper-install-test"
    Remove-Item -Recurse -Force $target -ErrorAction SilentlyContinue
    function Install-Silently {
        # not -Wait: in PowerShell 7 it also waits for child processes, and setup relaunches the app, so it never returns
        $p = Start-Process $setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=`"$target`"" -PassThru
        $null = $p.Handle   # keep the handle, or ExitCode reads empty after the exit
        if (-not $p.WaitForExit(300000)) { throw "Installer did not finish within 5 minutes" }
        if ($p.ExitCode -ne 0) { throw "Installer exited with $($p.ExitCode)" }
        # a silent install relaunches the app (that is how self-updates restart it); stop it for the test.
        # Setup starts it before exiting ([Run] nowait), so it is usually there already: wait for it, not a fixed 5 s
        $deadline = (Get-Date).AddSeconds(15)
        while (-not (Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($target) }) -and
               (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
        Stop-InstalledApp $target
    }
    Write-Host "== Installing silently into $target"
    Install-Silently
    Invoke-SelfTest (Join-Path $target $AppExe)

    if ($KbVersion) {
        # an update carrying the KB that is already installed must leave the KB's files alone
        Write-Host "== Updating over it (same KB $KbVersion)"
        $sentinel = Join-Path $target "_internal/data/kb/untouched-by-update"
        Set-Content $sentinel "x"
        Install-Silently
        if (-not (Test-Path $sentinel)) { throw "The update reinstalled a KB that was already installed" }
        Remove-Item $sentinel
        Invoke-SelfTest (Join-Path $target $AppExe)
    }
    $programs = [Environment]::GetFolderPath("Programs")
    $shortcut = Get-ChildItem $programs -Recurse -Filter "Maple Helper.lnk" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $shortcut) { throw "Start Menu shortcut missing under $programs" }

    Write-Host "== Uninstalling"
    $u = Start-Process (Join-Path $target "unins000.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -PassThru
    $null = $u.WaitForExit(120000)
    # the uninstaller re-launches itself from %TEMP%, so wait for the files to disappear
    $deadline = (Get-Date).AddSeconds(90)
    while ((Test-Path (Join-Path $target $AppExe)) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
    if (Test-Path (Join-Path $target $AppExe)) { throw "Uninstall left $AppExe behind" }
    if (Test-Path $shortcut.FullName) { throw "Uninstall left the Start Menu shortcut behind" }
    Write-Host "== Installer round trip OK"
}

Get-ChildItem $Release | Format-Table Name, @{ n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
