# FFCapture Project Setup Script
# This script sets up the virtual environment and installs all dependencies

param(
    [switch]$Clean,
    [switch]$SkipDependencies
)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPath = Join-Path $projectRoot "venv"

Write-Host "FFCapture Project Setup" -ForegroundColor Green
Write-Host "======================" -ForegroundColor Green

# Clean existing venv if requested
if ($Clean -and (Test-Path $venvPath)) {
    Write-Host "`nRemoving existing virtual environment..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $venvPath
}

# Create virtual environment
if (-not (Test-Path $venvPath)) {
    Write-Host "`nCreating virtual environment..." -ForegroundColor Cyan
    python -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to create virtual environment" -ForegroundColor Red
        exit 1
    }
    Write-Host "Virtual environment created at: $venvPath" -ForegroundColor Green
} else {
    Write-Host "Virtual environment already exists" -ForegroundColor Yellow
}

# Activate virtual environment
$activateScript = Join-Path $venvPath "Scripts\Activate.ps1"
Write-Host "`nActivating virtual environment..." -ForegroundColor Cyan
& $activateScript

# Upgrade pip
Write-Host "`nUpgrading pip..." -ForegroundColor Cyan
python -m pip install --upgrade pip

# Install dependencies
if (-not $SkipDependencies) {
    $requirementsFile = Join-Path $projectRoot "requirements.txt"
    $vendorDir = Join-Path $projectRoot "vendor"
    Write-Host "`nInstalling dependencies from requirements.txt..." -ForegroundColor Cyan
    Write-Host "Using custom PyAV from vendor/" -ForegroundColor Yellow
    pip install --find-links="$vendorDir" -r $requirementsFile
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to install dependencies" -ForegroundColor Red
        exit 1
    }
    Write-Host "Dependencies installed successfully" -ForegroundColor Green
}

Write-Host "`n" -ForegroundColor Green
Write-Host "Setup complete!" -ForegroundColor Green
Write-Host "To use the project, run: .\venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host "Then run: python src\main.py" -ForegroundColor Cyan
