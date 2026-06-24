# nekoro-browser 安装脚本 (Native Messaging)
# 用法: .\install.ps1
# 1. 检查环境  2. pip install  3. 注册 Native Host  4. 加载扩展

param([switch]$SkipExtension)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  nekoro-browser 安装 (Native Messaging)" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan

# ── 1. Python ─────────────────────────────────────────────────────────────

Write-Host "[1/4] Python 检查" -ForegroundColor Yellow
$pyVer = python --version 2>&1
Write-Host "  $pyVer" -ForegroundColor Green

# ── 2. pip install ────────────────────────────────────────────────────────

Write-Host "[2/4] pip install" -ForegroundColor Yellow
pip install -e $ProjectRoot 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL" -ForegroundColor Red; exit 1 }
Write-Host "  OK" -ForegroundColor Green

# ── 3. 注册 Native Messaging Host ─────────────────────────────────────────

Write-Host "[3/4] 注册 Native Messaging Host" -ForegroundColor Yellow
$hostManifest = Join-Path $ProjectRoot "native-host.json"

# Chrome
$chromeReg = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\com.nekoro.browser"
New-Item -Path $chromeReg -Force | Out-Null
Set-ItemProperty -Path $chromeReg -Name "(Default)" -Value $hostManifest
Write-Host "  Chrome: 已注册" -ForegroundColor Green

# Edge
$edgeReg = "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\com.nekoro.browser"
New-Item -Path $edgeReg -Force | Out-Null
Set-ItemProperty -Path $edgeReg -Name "(Default)" -Value $hostManifest
Write-Host "  Edge: 已注册" -ForegroundColor Green

# ── 4. 扩展 ───────────────────────────────────────────────────────────────

if (-not $SkipExtension) {
    Write-Host "[4/4] 扩展" -ForegroundColor Yellow
    $extPath = Join-Path $ProjectRoot "extension"
    Write-Host "  1. 打开 chrome://extensions/  2. 开启开发者模式" -ForegroundColor White
    Write-Host "  3. 加载已解压的扩展 → $extPath" -ForegroundColor White
    Write-Host "  4. 固定 ID: iagnlmkdkbaffdkeefakfpkmjlebgpgb" -ForegroundColor Gray
    Write-Host "  ⚠ 如之前加载过，先移除再重新加载" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  完成！" -ForegroundColor Green
Write-Host "  echo 'page_info()' | nekoro-browser" -ForegroundColor Gray
