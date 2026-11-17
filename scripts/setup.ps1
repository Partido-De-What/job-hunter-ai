# Job Hunter AI - one-time installer (started by Setup.bat). Safe to run again: it only adds
# what is missing. Asks before installing other programs. Never changes system settings.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "    [OK] $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    [!]  $text" -ForegroundColor Yellow }
function Ask($question) {
    $a = Read-Host "    $question (y/n)"
    return $a -match '^(y|yes|j|ja|s|si)$'
}

Write-Host "Job Hunter AI - setup" -ForegroundColor White
Write-Host "This installs the program in this folder and then opens the setup wizard in your browser."

# 1. Python 3.12+
Step "Checking Python"
$python = $null
foreach ($cand in @("py -3", "python")) {
    try {
        $v = & ([scriptblock]::Create("$cand -c `"import sys; print('%d.%d' % sys.version_info[:2])`"")) 2>$null
        if ($v -and [version]$v -ge [version]"3.12") { $python = $cand; Ok "Python $v"; break }
    } catch { }
}
if (-not $python) {
    Warn "Python 3.12 or newer is not installed."
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask "Install Python 3.13 now with winget?")) {
        winget install -e --id Python.Python.3.13 --accept-package-agreements
        Warn "Python was installed. Close this window and double-click Setup.bat again."
    } else {
        Warn "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run Setup.bat again."
    }
    exit 1
}

# 2. Program and its libraries in .venv
Step "Installing Job Hunter (a few minutes the first time)"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & ([scriptblock]::Create("$python -m venv .venv"))
}
& .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .venv\Scripts\python.exe -m pip install --quiet -e .
if ($LASTEXITCODE -ne 0) { Warn "Installation failed (see the messages above)."; exit 1 }
Ok "installed"

# 3. Claude Code (uses your Claude subscription)
Step "Checking Claude Code"
if (Get-Command claude -ErrorAction SilentlyContinue) {
    Ok "Claude Code found"
} else {
    Warn "Claude Code is not installed. Job Hunter uses it to reach your Claude subscription."
    if (Ask "Install Claude Code now with Anthropic's official installer?") {
        Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
    } else {
        Warn "Install it later from https://claude.com/claude-code - the wizard will remind you."
    }
}

# 4. Microsoft Word (only for PDFs)
Step "Checking Microsoft Word"
if (Test-Path "Registry::HKEY_CLASSES_ROOT\Word.Application") { Ok "Word found (CVs and letters also as PDF)" }
else { Warn "Word not found: CVs and letters are created as Word files only (no PDF)." }

# 5. Desktop icon and the wizard
Step "Creating the Desktop icon"
& .venv\Scripts\vjh.exe shortcut
Step "Opening the setup wizard in your browser"
Write-Host "    Keep this window open while you use the wizard. Close it to stop the dashboard."
& .venv\Scripts\vjh.exe setup
