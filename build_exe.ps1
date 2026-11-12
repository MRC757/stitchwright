# Build dist\svg2pes.exe (one file, no console window).
# Run from the project folder in PowerShell:   .\build_exe.ps1
# If scripts are blocked:  powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
#
# Uses its own virtual environment (.venv-build) so your normal Python is untouched.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (Get-Process svg2pes -ErrorAction SilentlyContinue) {
    throw "svg2pes is running. Close it first: Windows will not let the build replace dist\svg2pes.exe while it is open."
}

if (-not (Test-Path ".venv-build\Scripts\python.exe")) {
    Write-Host "Creating build environment (.venv-build)..."
    python -m venv .venv-build
}
$py = ".venv-build\Scripts\python.exe"

Write-Host "Installing requirements and PyInstaller..."
& $py -m pip install --quiet --disable-pip-version-check -r requirements.txt pyinstaller pytest pyflakes
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "Running tests..."
& $py -m pytest tests -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "tests failed - not building" }

Write-Host "Building..."
& $py -m PyInstaller --clean --noconfirm svg2pes.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Get-Item "dist\svg2pes.exe"
Write-Host ("Built {0} ({1:N1} MB)" -f $exe.FullName, ($exe.Length / 1MB))
