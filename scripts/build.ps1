<#
.SYNOPSIS
  完整构建：collector sidecar + Tauri app（默认 release + NSIS 安装包）。

.DESCRIPTION
  顺序（BUILD_PLAN §6）：
    1. scripts\build-collector.ps1 → dist\collector\（PyInstaller onedir）
    2. npx tauri build          → 打包 dist\collector 到安装包 collector\

.PARAMETER Debug
  debug 构建（验收用），默认 release。
.PARAMETER NoBundle
  只编译可执行文件，不生成 NSIS 安装包。
.PARAMETER SkipCollector
  跳过 PyInstaller（直接用现有 dist\collector；collector 未就绪时用）。

.EXAMPLE
  .\scripts\build.ps1 -Debug -NoBundle -SkipCollector
#>
param(
    [switch]$Debug,
    [switch]$NoBundle,
    [switch]$SkipCollector
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"

if ($SkipCollector) {
    Write-Host "跳过 collector 构建（使用现有 dist\collector）"
} else {
    $collectorMain = Join-Path $root "collector\__main__.py"
    $collectorEntry = Join-Path $root "collector_entry.py"
    if (-not (Test-Path $collectorMain) -or -not (Test-Path $collectorEntry)) {
        throw "collector 未就绪（缺 collector\__main__.py 或 collector_entry.py），无法打包 sidecar"
    }
    & (Join-Path $PSScriptRoot "build-collector.ps1")
    if ($LASTEXITCODE -ne 0) { throw "collector 构建失败" }
}

# 不允许把占位/空目录打进安装包：PyInstaller onedir 必须有 collector.exe + _internal。
$collectorExe = Join-Path $root "dist\collector\collector.exe"
$collectorInternal = Join-Path $root "dist\collector\_internal"
if (-not (Test-Path $collectorExe)) {
    throw "dist\collector\collector.exe 不存在；先运行 scripts\build-collector.ps1 再打包"
}
if (-not (Test-Path $collectorInternal)) {
    throw "dist\collector\_internal 不存在；PyInstaller onedir 产物不完整，拒绝打包"
}

$tauriArgs = @("tauri", "build")
if ($Debug) { $tauriArgs += "--debug" }
if ($NoBundle) { $tauriArgs += "--no-bundle" }

Write-Host "npx $($tauriArgs -join ' ')"
& npx.cmd @tauriArgs
if ($LASTEXITCODE -ne 0) { throw "tauri build 失败（exit=$LASTEXITCODE）" }

$outDir = if ($Debug) { "src-tauri\target\debug" } else { "src-tauri\target\release" }
Write-Host ("构建完成：{0}\peripheral-battery.exe" -f $outDir)
if (-not $NoBundle -and -not $Debug) {
    Get-ChildItem -Recurse -File "src-tauri\target\release\bundle" -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in ".exe", ".msi", ".zip" } |
        ForEach-Object { Write-Host ("安装包: {0}" -f $_.FullName) }
}
