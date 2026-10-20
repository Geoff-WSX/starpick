# STARPICK 拾星 Windows 构建脚本（CI 用；本地手动构建同样适用）
# 前置：Python 3.10+ 已装并激活、Node 20+ 已装。用法：
#   powershell -ExecutionPolicy Bypass -File packaging/windows/build_windows.ps1
# 产物：packaging/dist/windows/（StarPick onedir + selftest-report.txt + setup.exe）
param(
    [string]$Version = "1.0.0"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSCommandPath))
Set-Location $Root

Write-Host "==> [1/5] 前端构建"
Push-Location frontend
npm ci
if ($LASTEXITCODE -ne 0) { throw "npm ci 失败" }
npm run build
if ($LASTEXITCODE -ne 0) { throw "前端构建失败" }
Pop-Location

Write-Host "==> [2/5] Python 依赖"
Push-Location backend
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-desktop.txt "pyinstaller==6.22.3"
if ($LASTEXITCODE -ne 0) { throw "pip 安装失败" }
Pop-Location

Write-Host "==> [3/5] PyInstaller 打包"
pyinstaller packaging/starpick.spec --noconfirm --clean `
    --distpath packaging/dist/windows --workpath packaging/build/windows
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

Write-Host "==> [4/5] 冻结包自检（含 webview GUI 栈检查）"
$env:STARPICK_SELFTEST_WEBVIEW = "1"
$env:STARPICK_SELFTEST_REPORT  = "packaging/dist/windows/selftest-report.txt"
& packaging/dist/windows/StarPick/StarPick.exe --selftest --port 8960
$selftestRc = $LASTEXITCODE
Remove-Item Env:STARPICK_SELFTEST_REPORT -ErrorAction SilentlyContinue
Remove-Item Env:STARPICK_SELFTEST_WEBVIEW  -ErrorAction SilentlyContinue
if ($selftestRc -ne 0) {
    Get-Content packaging/dist/windows/selftest-report.txt
    throw "冻结包自检失败（rc=$selftestRc）"
}
Get-Content packaging/dist/windows/selftest-report.txt | Select-Object -Last 3

Write-Host "==> [5/5] Inno Setup 安装包"
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if ($iscc) { $IsccExe = $iscc.Source } else {
    $IsccExe = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $IsccExe)) { throw "未找到 Inno Setup（iscc）：$IsccExe" }
& $IsccExe "packaging/windows/installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 失败" }
Get-ChildItem packaging/dist/windows | Format-Table Name, Length
Write-Host "完成：packaging/dist/windows/StarPick-$Version-setup.exe"
