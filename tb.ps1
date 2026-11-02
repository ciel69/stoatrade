# Windows (PowerShell) entry point: .\tb.ps1 <group> <command> [args]   (list: .\tb.ps1 help)
# Thin wrapper over src\tb\launcher.py: the Bybit key file and proxy removal are done there, same as ./tb on macOS.
# If script execution is blocked: powershell -ExecutionPolicy Bypass -File .\tb.ps1 ...   (or use tb.cmd)
# Interpreter: $env:TB_PYTHON, else .venv\Scripts\python.exe, else "py -3", else python; only Python 3.9+ counts (the WindowsApps stub is skipped).
# No usable Python: ".\tb.ps1 setup" runs scripts\bootstrap.ps1 (offers to install it); other commands suggest setup (exit code 3).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root\src;$($env:PYTHONPATH)" } else { "$Root\src" }
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

function Test-Py([string[]]$cmd) {
    if (-not (Get-Command $cmd[0] -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath $cmd[0])) { return $false }
    $rest = @(); if ($cmd.Count -gt 1) { $rest = $cmd[1..($cmd.Count - 1)] }
    try {
        & $cmd[0] @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

$candidates = @()
if ($env:TB_PYTHON) { $candidates += , @($env:TB_PYTHON) }
$candidates += , @("$Root\.venv\Scripts\python.exe")
$candidates += , @("py", "-3")
$candidates += , @("python")
$py = $null
foreach ($c in $candidates) { if (Test-Py $c) { $py = $c; break } }

if (-not $py) {
    if ($args.Count -gt 0 -and $args[0] -eq "setup") {
        & powershell -NoProfile -ExecutionPolicy Bypass -File "$Root\scripts\bootstrap.ps1" @args
        exit $LASTEXITCODE
    }
    [Console]::Error.WriteLine("Python 3.9 or newer was not found (needed by tb).")
    [Console]::Error.WriteLine("Run: .\tb.ps1 setup   (checks the environment and offers to install Python; nothing is installed without your consent)")
    exit 3
}
$rest = @()
if ($py.Count -gt 1) { $rest = $py[1..($py.Count - 1)] }
& $py[0] @rest -m tb.launcher @args
exit $LASTEXITCODE
