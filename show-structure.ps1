# Run from your project root: .\show-structure.ps1

$skipFolders = @("node_modules",".git","__pycache__",".next","dist","build",".vercel","venv",".venv","env","site-packages")

function Show-Tree {
    param([string]$Path = ".",[string]$Indent = "",[int]$Depth = 0,[int]$Max = 8)
    if ($Depth -ge $Max) { return }

    $items = Get-ChildItem -Path $Path -Force -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -notmatch "^\." -or $_.Name -in @(".env",".env.local",".env.example",".gitignore") } |
             Sort-Object { -not $_.PSIsContainer }, Name

    for ($i = 0; $i -lt $items.Count; $i++) {
        $item      = $items[$i]
        $isLast    = ($i -eq $items.Count - 1)
        $prefix    = if ($isLast) { "--- " } else { "+-- " }
        $nextIndent = if ($isLast) { "$Indent    " } else { "$Indent|   " }

        if ($item.PSIsContainer) {
            if ($skipFolders -contains $item.Name) {
                Write-Host "$Indent$prefix$($item.Name)/ [SKIPPED]" -ForegroundColor DarkGray
                continue
            }
            Write-Host "$Indent$prefix$($item.Name)/" -ForegroundColor Cyan
            Show-Tree -Path $item.FullName -Indent $nextIndent -Depth ($Depth+1) -Max $Max
        }
        else {
            $ext  = $item.Extension.ToLower()
            $size = if ($item.Length -gt 1MB) { "{0:N1}MB" -f ($item.Length/1MB) }
                    elseif ($item.Length -gt 1KB) { "{0:N0}KB" -f ($item.Length/1KB) }
                    else { "$($item.Length)B" }

            $isJunk = $item.Name -match "^fix_|^bust_|^diagnose|^upgrade_|_patch\.py$|^apply_|^copy_button|^dashboard_patch|^fix_imports|^test_scoring"

            $color = if ($isJunk) { "Red" }
                     elseif ($ext -in ".py",".toml",".yaml",".yml",".sh") { "Green" }
                     elseif ($ext -in ".ts",".tsx",".ps1") { "Blue" }
                     elseif ($ext -in ".js",".jsx",".html") { "Yellow" }
                     elseif ($ext -in ".json",".css") { "Magenta" }
                     elseif ($ext -eq ".env") { "Red" }
                     elseif ($ext -eq ".md") { "White" }
                     else { "Gray" }

            $junkLabel = if ($isJunk) { "  <-- JUNK" } else { "" }
            Write-Host "$Indent$prefix$($item.Name)  ($size)$junkLabel" -ForegroundColor $color
        }
    }
}

Write-Host ""
Write-Host "PROJECT STRUCTURE" -ForegroundColor Yellow
Write-Host "Root: $(Get-Location)" -ForegroundColor Gray
Write-Host ""

Show-Tree -Path "." -Max 8

Write-Host ""
Write-Host "=== SUMMARY ===" -ForegroundColor Yellow

$all   = Get-ChildItem -Recurse -File -ErrorAction SilentlyContinue |
         Where-Object { $_.FullName -notmatch "node_modules|\\\.git|__pycache__|\.next|dist\\|build\\|\.vercel|venv|site-packages" }

$py    = @($all | Where-Object { $_.Extension -eq ".py" }).Count
$ts    = @($all | Where-Object { $_.Extension -in ".ts",".tsx" }).Count
$js    = @($all | Where-Object { $_.Extension -in ".js",".jsx" }).Count
$junk  = @($all | Where-Object { $_.Name -match "^fix_|^bust_|^diagnose|^upgrade_|_patch\.py$|^apply_|^copy_button|^dashboard_patch|^fix_imports|^test_scoring" })

Write-Host "  Python files : $py" -ForegroundColor Green
Write-Host "  TypeScript   : $ts" -ForegroundColor Blue
Write-Host "  JavaScript   : $js" -ForegroundColor Yellow
Write-Host "  Total files  : $($all.Count)" -ForegroundColor White

if ($junk.Count -gt 0) {
    Write-Host ""
    Write-Host "  JUNK FILES DETECTED: $($junk.Count)" -ForegroundColor Red
    foreach ($f in $junk) {
        $rel = $f.FullName.Replace((Get-Location).Path + "\", "")
        Write-Host "    JUNK: $rel" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== ROOT .PY FILES ===" -ForegroundColor Yellow
$rootPy = Get-ChildItem -Path "." -Filter "*.py" -File -ErrorAction SilentlyContinue
if ($rootPy) {
    foreach ($f in $rootPy) {
        $isJunk = $f.Name -match "^fix_|^bust_|^diagnose|^upgrade_|_patch|^apply_|^copy_button|^dashboard_patch|^fix_imports|^test_scoring"
        if ($isJunk) {
            Write-Host "  JUNK : $($f.Name)" -ForegroundColor Red
        } else {
            Write-Host "  OK   : $($f.Name)" -ForegroundColor Green
        }
    }
} else {
    Write-Host "  (none at root)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=== DONE - paste output above back to Claude ===" -ForegroundColor Yellow
Write-Host ""