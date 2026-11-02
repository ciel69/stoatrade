# Stage 0 of the first run on Windows: install Python 3.9+ if it is missing, then hand over to "tb.cmd setup" (everything after that is Python).
# Called by tb.cmd / tb.ps1 when no usable Python was found and the command is "setup". Can be run directly:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 [--yes] [--dry-run] [--check]
#   --yes      install without questions     --dry-run  only print the commands     --check  only report what is missing (exit 3 if no Python)
# Without an interactive console and without --yes nothing is installed: instructions are printed, exit code 3.
# Install, per user, no admin: winget (Python.Python.3.12, --scope user), else the official python.org installer (InstallAllUsers=0 PrependPath=1).
# Idempotent: with Python already present it goes straight to "tb.cmd setup". Other flags are passed to "tb setup" unchanged.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PyVer = "3.12"
$PyFull = "3.12.10"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Yes = $false; $Dry = $false; $Check = $false; $Pass = @()
foreach ($a in $args) {
    switch ($a) {
        "setup" { }
        { $_ -in "--yes", "-y" } { $Yes = $true; $Pass += $a }
        "--dry-run" { $Dry = $true; $Pass += $a }
        "--check" { $Check = $true }
        default { $Pass += $a }
    }
}

function Say([string]$s) { Write-Host $s }
function Interactive { return ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected) }

function Test-Py([string[]]$cmd) {
    if (-not (Get-Command $cmd[0] -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath $cmd[0])) { return $false }
    $rest = @(); if ($cmd.Count -gt 1) { $rest = $cmd[1..($cmd.Count - 1)] }
    try {
        # the WindowsApps python.exe stub prints "Python was not found..." and exits 9009: it fails this probe and is not treated as Python
        & $cmd[0] @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-Py {
    $c = @()
    if ($env:TB_PYTHON) { $c += , @($env:TB_PYTHON) }
    $c += , @("$Root\.venv\Scripts\python.exe")
    $c += , @("py", "-3")
    $c += , @("python")
    $c += , @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")
    foreach ($x in $c) { if (Test-Py $x) { return , $x } }
    return $null
}

function Refresh-Path {
    $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $u = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = (@($m, $u) | Where-Object { $_ }) -join ";"
}

function Handoff($py) {
    $rest = @(); if ($py.Count -gt 1) { $rest = $py[1..($py.Count - 1)] }
    $v = & $py[0] @rest -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
    Say "Python found: $v. Continuing with tb setup."
    # pass the found interpreter on, so tb.cmd does not search again and come back here
    if ($py.Count -eq 1) { $env:TB_PYTHON = $py[0] }
    $tbArgs = @("setup")
    if ($Check) { $tbArgs += "--check" } else { $tbArgs += $Pass }
    & "$Root\tb.cmd" @tbArgs
    exit $LASTEXITCODE
}

function Manual {
    Say "Install Python 3.9+ yourself, then run tb.cmd setup again:"
    Say "  winget install -e --id Python.Python.$PyVer --scope user"
    Say "  or the installer from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH')"
    Say "  Python installed elsewhere: set TB_PYTHON=C:\path\to\python.exe"
}

$py = Find-Py
if ($py) { Handoff $py }

Say "Stoa: Python 3.9 or newer was not found."
if ($Check) { Manual; exit 3 }

$arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" }
$url = "https://www.python.org/ftp/python/$PyFull/python-$PyFull-$arch.exe"
$exe = Join-Path $env:TEMP "stoa-python-$PyFull-$arch.exe"
$useWinget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
$winget = @("winget", "install", "-e", "--id", "Python.Python.$PyVer", "--scope", "user", "--accept-package-agreements", "--accept-source-agreements")
$silent = @("/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1", "InstallLauncherAllUsers=0", "Include_test=0")

if ($useWinget) {
    Say "Proposed command (per user, no admin rights):"
    Say ("  " + ($winget -join " "))
} else {
    Say "winget not found. Proposed: download the official installer from python.org and install it for the current user (no admin rights):"
    Say "  Invoke-WebRequest $url -OutFile $exe"
    Say ("  $exe " + ($silent -join " "))
}

if ($Dry) { Say "--dry-run: commands printed only, nothing was run."; exit 0 }
if (-not (Interactive) -and -not $Yes) {
    Say "No interactive console: nothing is installed silently. Run the command above yourself or use: tb.cmd setup --yes"
    exit 3
}
if (-not $Yes) {
    $ans = Read-Host "Install Python this way? [Y/n]"
    if ($ans -and $ans.Trim().ToLower() -notin @("y", "yes")){ Say "Cancelled."; Manual; exit 3 }
} else { Say "Install Python this way? [Y/n]: y (--yes)" }

try {
    if ($useWinget) {
        & $winget[0] $winget[1..($winget.Count - 1)]
        if ($LASTEXITCODE -ne 0) { throw "winget exit code $LASTEXITCODE" }
    } else {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $exe
        $p = Start-Process -FilePath $exe -ArgumentList $silent -Wait -PassThru
        Remove-Item -LiteralPath $exe -ErrorAction SilentlyContinue
        if ($p.ExitCode -ne 0) { throw "installer exit code $($p.ExitCode)" }
    }
} catch {
    Say "Installation failed: $($_.Exception.Message)"
    Manual
    exit 3
}

Refresh-Path
$py = Find-Py
if ($py) { Handoff $py }
Say "Python is installed but not visible in this terminal yet (PATH). Open a new terminal and run tb.cmd setup again."
exit 3
