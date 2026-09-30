<#
.SYNOPSIS
  WorkReport Windows 실행 파일 빌드.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1            # 단일 exe (dist\WorkReport.exe)
  powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -OneDir    # 폴더형 (dist\WorkReport\WorkReport.exe, 시작이 빠름)
  powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -NoWhisper # 로컬 Whisper 제외 (용량↓, 클라우드 STT 만 사용)
#>
param(
    [switch]$OneDir,
    [switch]$NoWhisper,
    [switch]$SkipInstall
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $SkipInstall) {
    python -m pip install --upgrade pip
    if ($NoWhisper) { python -m pip install -e ".[dev]" } else { python -m pip install -e ".[whisper,dev]" }
}

python packaging\make_icon.py packaging\workreport.ico
if ($OneDir) { $env:WORKREPORT_ONEDIR = "1" } else { Remove-Item Env:WORKREPORT_ONEDIR -ErrorAction SilentlyContinue }

python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\workreport.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 빌드 실패" }

if ($OneDir) { $out = "dist\WorkReport\WorkReport.exe" } else { $out = "dist\WorkReport.exe" }
Write-Host "빌드 완료: $out"
