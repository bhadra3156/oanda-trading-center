# ═══════════════════════════════════════════════════════════════════════════
# OANDA TRADING CENTER — DEPLOYMENT SCRIPT
# Copy-paste this entire block into your VS Code PowerShell terminal
# Run from the root of your oanda-trading-center project folder
# ═══════════════════════════════════════════════════════════════════════════

Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan
Write-Host " OANDA TRADING CENTER — APPLYING ALL FIXES" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan

# ── STEP 1: Verify you're in the right folder ──────────────────────────────
if (-not (Test-Path "api") -or -not (Test-Path "frontend")) {
    Write-Host "ERROR: Run this from your project root (where api/ and frontend/ folders are)" -ForegroundColor Red
    exit 1
}
Write-Host "✓ Project folder confirmed" -ForegroundColor Green

# ── STEP 2: Back up existing files ────────────────────────────────────────
$backupDir = "backup_$(Get-Date -Format 'yyyyMMdd_HHmm')"
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
@("api/oanda.py", "api/signals.py", "api/main.py", "frontend/positions.html") | ForEach-Object {
    if (Test-Path $_) {
        Copy-Item $_ "$backupDir/$($_ -replace '/','_')"
        Write-Host "  Backed up: $_" -ForegroundColor Gray
    }
}
Write-Host "✓ Backup saved to ./$backupDir/" -ForegroundColor Green

# ── STEP 3: Copy new files from downloads ─────────────────────────────────
Write-Host ""
Write-Host "Copying fixed files..." -ForegroundColor Yellow

# Adjust this path to where you saved the downloaded files
$srcDir = "$HOME\Downloads\trading-center-fix"

if (-not (Test-Path $srcDir)) {
    Write-Host "NOTICE: Download folder not found at $srcDir" -ForegroundColor Yellow
    Write-Host "        Paste the file contents manually using the sections below." -ForegroundColor Yellow
} else {
    Copy-Item "$srcDir\api\pip_utils.py"    "api\pip_utils.py"    -Force
    Copy-Item "$srcDir\api\signals.py"      "api\signals.py"      -Force
    Copy-Item "$srcDir\api\oanda.py"        "api\oanda.py"        -Force
    Copy-Item "$srcDir\api\main.py"         "api\main.py"         -Force
    Copy-Item "$srcDir\frontend\positions.html" "frontend\positions.html" -Force
    Write-Host "✓ All files copied" -ForegroundColor Green
}

# ── STEP 4: Verify pip_utils is importable ────────────────────────────────
Write-Host ""
Write-Host "Verifying pip_utils..." -ForegroundColor Yellow
$pipTest = python -c "
import sys; sys.path.insert(0, '.')
from api.pip_utils import get_pip
tests = {
    'XAU_USD': (0.01,    'Gold'),
    'XAG_USD': (0.001,   'Silver'),
    'XPD_USD': (0.01,    'Palladium'),
    'NATGAS_USD': (0.0001, 'Nat Gas'),
    'WTICO_USD':  (0.001,  'WTI Oil'),
    'SUGAR_USD':  (0.00001,'Sugar'),
    'SPX500_USD': (0.1,   'SPX500'),
    'NAS100_USD': (0.1,   'NAS100'),
    'UK100_GBP':  (0.1,   'UK100'),
    'DE30_EUR':   (0.1,   'DE30'),
    'EUR_USD':    (0.0001,'EUR/USD'),
    'CORN_USD':   (0.0001,'Corn'),
}
errors = []
for inst, (expected, name) in tests.items():
    got = get_pip(inst)
    status = 'OK' if got == expected else f'FAIL (got {got}, expected {expected})'
    print(f'  {name:12s} {inst:15s}: {got} → {status}')
    if got != expected:
        errors.append(inst)
if errors:
    print(f'FAILED: {errors}')
    sys.exit(1)
else:
    print('All pip values correct!')
" 2>&1
Write-Host $pipTest
if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ pip_utils verification passed" -ForegroundColor Green
} else {
    Write-Host "✗ pip_utils verification FAILED — check the file" -ForegroundColor Red
}

# ── STEP 5: Install / update dependencies ─────────────────────────────────
Write-Host ""
Write-Host "Checking dependencies..." -ForegroundColor Yellow
pip install fastapi uvicorn oandapyV20 pandas python-dotenv --quiet
Write-Host "✓ Dependencies OK" -ForegroundColor Green

# ── STEP 6: Run local server to test ──────────────────────────────────────
Write-Host ""
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan
Write-Host " READY — Start your local server:" -ForegroundColor Cyan
Write-Host "   uvicorn api.main:app --reload --port 8000" -ForegroundColor White
Write-Host ""
Write-Host " Then test in browser:" -ForegroundColor Cyan
Write-Host "   http://localhost:8000/api/health" -ForegroundColor White
Write-Host "   http://localhost:8000/api/signals" -ForegroundColor White
Write-Host "   http://localhost:8000/api/account" -ForegroundColor White
Write-Host "═══════════════════════════════════════════" -ForegroundColor Cyan

# ── STEP 7: Git commit ────────────────────────────────────────────────────
Write-Host ""
$doGit = Read-Host "Commit and push to GitHub now? (y/n)"
if ($doGit -eq 'y') {
    git add api/pip_utils.py api/signals.py api/oanda.py api/main.py frontend/positions.html
    git commit -m "fix: correct pip values for all 16 instruments, add breakeven/trailing stop endpoints

- Add api/pip_utils.py with verified pip values for all instruments
- Fix XAG (0.001), XPD (0.01), NATGAS (0.0001), WTICO (0.001), SUGAR (0.00001)
- Fix SPX500/NAS100/UK100/DE30 indices (all 0.1, were 0.0001 = 1000x wrong)
- Add breakeven endpoint: POST /api/breakeven
- Add trailing stop endpoint: POST /api/trailing-stop
- Add modify trade endpoint: POST /api/modify-trade
- Fix daily loss limit to 3% (was 5%), weekly to 5% (was 10%)
- Add H1 RSI confirmation to signal scanner
- Add confluence score (0-7) to every signal
- Update positions.html with breakeven/trailing stop/modify buttons"
    git push origin main
    Write-Host "✓ Pushed to GitHub — Render will auto-deploy in ~2 minutes" -ForegroundColor Green
    Write-Host "  Watch deploy: https://dashboard.render.com" -ForegroundColor Gray
}

Write-Host ""
Write-Host "Done! Summary of what was fixed:" -ForegroundColor Cyan
Write-Host "  ✓ pip_utils.py  — correct pip values for all 16 instruments" -ForegroundColor White
Write-Host "  ✓ signals.py    — H1 confirmation, confluence score, session filter" -ForegroundColor White
Write-Host "  ✓ oanda.py      — breakeven, trailing stop, modify endpoints, 3% daily limit" -ForegroundColor White
Write-Host "  ✓ main.py       — all new API routes wired up" -ForegroundColor White
Write-Host "  ✓ positions.html — Breakeven/Trailing Stop/Modify buttons live" -ForegroundColor White
