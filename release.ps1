<#
.SYNOPSIS
    Build + Release NamaChan Account Manager sur GitHub.
.DESCRIPTION
    1. Demande le numéro de version (ou garde l'actuel)
    2. Met à jour __version__ dans app_ui.py
    3. Build l'exe avec PyInstaller
    4. Git add + commit + tag
    5. Push commit + tag
    6. Crée une GitHub Release avec l'exe en asset
.USAGE
    .\release.ps1
    .\release.ps1 -Version "1.2.0"
#>
param(
    [string]$Version
)

$ErrorActionPreference = "Stop"
$Repo = "NamaGoat/NamaChanTaskManager"
$ExeName = "NamaChanAccountManager.exe"
$SpecFile = "NamaChanAccountManager.spec"
$Py = "C:\Users\namaz\AppData\Local\Programs\Python\Python310\python.exe"

# --- 1. Déterminer la version ---
$current = (Select-String -Path "app_ui.py" -Pattern '__version__\s*=\s*"(.+?)"').Matches[0].Groups[1].Value
Write-Host "`n  Version actuelle : v$current" -ForegroundColor Cyan

if (-not $Version) {
    $Version = Read-Host "  Nouvelle version (laisser vide = garder v$current)"
    if (-not $Version) { $Version = $current }
}
$tag = "v$Version"
Write-Host "  Tag : $tag`n" -ForegroundColor Green

# --- 2. Mettre à jour __version__ dans app_ui.py ---
$content = Get-Content "app_ui.py" -Raw
$content = $content -replace "(?<=__version__\s*=\s*"")[^""]+(?="")", $Version
Set-Content "app_ui.py" -Value $content -NoNewline -Encoding UTF8
Write-Host "[OK] __version__ = $Version dans app_ui.py" -ForegroundColor Green

# --- 3. Build PyInstaller ---
Write-Host "`n[Build] Lancement de PyInstaller..." -ForegroundColor Yellow
& $Py -m PyInstaller $SpecFile --noconfirm 2>&1 | ForEach-Object { Write-Host "  $_" }
$exePath = Join-Path "dist" $ExeName
if (-not (Test-Path $exePath)) {
    Write-Host "[ERREUR] $exePath introuvable apres le build !" -ForegroundColor Red
    exit 1
}
$sizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
Write-Host "[OK] Build reussi : $exePath ($sizeMB Mo)`n" -ForegroundColor Green

# --- 4. Git commit + tag ---
git add app_ui.py
git add -A
$changes = git status --porcelain
if ($changes) {
    git commit -m "release: $tag"
    Write-Host "[OK] Commit cree" -ForegroundColor Green
} else {
    Write-Host "[~] Rien a commiter (version deja set ?)" -ForegroundColor Yellow
}

# Tag
$existingTag = git tag -l $tag
if ($existingTag) {
    git tag -d $tag
    Write-Host "[~] Ancien tag $tag supprime" -ForegroundColor Yellow
}
git tag -a $tag -m "Release $tag"
Write-Host "[OK] Tag $tag cree`n" -ForegroundColor Green

# --- 5. Push ---
Write-Host "[Push] Envoi vers GitHub..." -ForegroundColor Yellow
git push origin master
git push origin $tag --force
Write-Host "[OK] Push termine`n" -ForegroundColor Green

# --- 6. GitHub Release ---
Write-Host "[Release] Creation de la release GitHub..." -ForegroundColor Yellow
$notes = Read-Host "  Notes de release (description)"
if (-not $notes) { $notes = "Mise a jour $tag" }

# Supprimer release existante si elle existe
$existing = gh release view $tag --repo $Repo 2>&1
if ($LASTEXITCODE -eq 0) {
    gh release delete $tag --repo $Repo --yes 2>&1 | Out-Null
    Write-Host "[~] Ancienne release $tag supprimee" -ForegroundColor Yellow
}

gh release create $tag $exePath `
    --repo $Repo `
    --title "NamaChan Account Manager $tag" `
    --notes $notes `
    --latest

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n  Release publiee !" -ForegroundColor Green
    Write-Host "  https://github.com/$Repo/releases/tag/$tag" -ForegroundColor Cyan
    Write-Host ""
} else {
    Write-Host "[ERREUR] Echec de la creation de release" -ForegroundColor Red
    exit 1
}
