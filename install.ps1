# nekoro-browser 安装脚本
# 用法: .\install.ps1
# 1. pip install  2. 引导加载 Chrome 扩展

param([switch]$SkipExtension)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  nekoro-browser 安装" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan

Write-Host "[1/2] pip install" -ForegroundColor Yellow
pip install -e $ProjectRoot 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "  FAIL" -ForegroundColor Red; exit 1 }
Write-Host "  OK" -ForegroundColor Green

if (-not $SkipExtension) {
    Write-Host "[2/2] Chrome 扩展" -ForegroundColor Yellow
    $extPath = Join-Path $ProjectRoot "extension"
    Write-Host "  1. 打开 chrome://extensions/  2. 开启开发者模式" -ForegroundColor White
    Write-Host "  3. 加载已解压的扩展 → $extPath" -ForegroundColor White
    Write-Host "  如之前加载过，先移除再重新加载" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  完成！" -ForegroundColor Green
Write-Host "  启动: nekoro-browser（前台，保持打开）" -ForegroundColor Gray
Write-Host "  命令: echo 'page_info()' | nekoro-browser（新终端）" -ForegroundColor Gray
