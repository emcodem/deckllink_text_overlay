# FFCapture DeckLink Verification Script
# Verify DeckLink drivers and Python environment are properly configured

Write-Host "FFCapture DeckLink Verification" -ForegroundColor Green
Write-Host "==============================`n" -ForegroundColor Green

$errors = @()
$warnings = @()

# 1. Check Python environment
Write-Host "[1/4] Checking Python environment..." -ForegroundColor Cyan
$venv_path = "$PSScriptRoot\venv"
$python_exe = "$PSScriptRoot\venv\Scripts\python.exe"

if (Test-Path $python_exe) {
    Write-Host "  CHECK Virtual environment found" -ForegroundColor Green
} else {
    $errors += "Virtual environment not found - run: .\setup.ps1"
    Write-Host "  ERROR Virtual environment not found" -ForegroundColor Red
}

# 2. Check pywin32
Write-Host "`n[2/4] Checking pywin32..." -ForegroundColor Cyan
if (Test-Path $python_exe) {
    $pywin32_check = & $python_exe -c "import win32com; print('OK')" 2>&1
    if ($pywin32_check -match "OK") {
        Write-Host "  CHECK pywin32 installed" -ForegroundColor Green
    } else {
        $warnings += "pywin32 not working - run: pip install --upgrade pywin32"
        Write-Host "  WARN pywin32 issue detected" -ForegroundColor Yellow
    }
}

# 3. Check DeckLink COM objects
Write-Host "`n[3/4] Checking DeckLink COM registration..." -ForegroundColor Cyan
if (Test-Path $python_exe) {
    $com_script = "import win32com.client; w32 = win32com.client.Dispatch('DeckLinkSDK.DeckLinkIterator_1'); print('OK')"
    $com_check = & $python_exe -c $com_script 2>&1

    if ($com_check -match "OK") {
        Write-Host "  CHECK DeckLink COM objects registered" -ForegroundColor Green
    } else {
        $warnings += "DeckLink COM not found - install DeckLink drivers"
        Write-Host "  WARN DeckLink COM not accessible" -ForegroundColor Yellow
    }
}

# 4. Check dependencies
Write-Host "`n[4/4] Checking Python dependencies..." -ForegroundColor Cyan
$packages = @("numpy", "cv2", "PyQt6", "av", "win32com")
$missing = @()

if (Test-Path $python_exe) {
    foreach ($pkg in $packages) {
        $check = & $python_exe -c "import $pkg" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  CHECK $pkg" -ForegroundColor Green
        } else {
            $missing += $pkg
            Write-Host "  ERROR $pkg" -ForegroundColor Red
        }
    }

    if ($missing) {
        $errors += "Missing: $($missing -join ', ') - run: pip install -r requirements.txt"
    }
}

# Summary
Write-Host "`n" -ForegroundColor Gray
Write-Host "==============================" -ForegroundColor Green
Write-Host "Summary" -ForegroundColor Green
Write-Host "==============================" -ForegroundColor Green

if ($errors) {
    Write-Host "`nERRORS:" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
}

if ($warnings) {
    Write-Host "`nWARNINGS:" -ForegroundColor Yellow
    $warnings | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
}

if (-not $errors -and -not $warnings) {
    Write-Host "`nOK: All checks passed!" -ForegroundColor Green
}

Write-Host "`nNext steps:" -ForegroundColor Cyan
if ($errors) {
    Write-Host "  1. Fix errors above" -ForegroundColor Cyan
} else {
    Write-Host "  1. Install DeckLink drivers (if not done)" -ForegroundColor Cyan
    Write-Host "  2. Edit src/config.py to set CAPTURE_DEVICE_INDEX and PLAYOUT_DEVICE_INDEX" -ForegroundColor Cyan
    Write-Host "  3. Run: python src/main.py" -ForegroundColor Cyan
}

exit ($errors.Count)
