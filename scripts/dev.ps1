<#
.SYNOPSIS
  开发模式启动 Tauri 外壳（Rust 侧）。

.DESCRIPTION
  - Rust 工具链加入 PATH（本机 bash PATH 不含 ~\.cargo\bin）
  - 默认 PB_LOG_LEVEL=debug、PB_SHOW_ON_START=1（启动即显示面板，方便调试）
  - collector 缺失时 app 会显示错误态而不是崩溃；collector/__main__.py 就绪后可用
    PB_COLLECTOR / PB_PYTHON 指定采集器，或直接重启。

.EXAMPLE
  .\scripts\dev.ps1
  .\scripts\dev.ps1 -Collector "C:\path\to\python.exe -m collector --stdio"
#>
param(
    [string]$Collector = "",
    [switch]$NoShow
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"

if ($Collector) { $env:PB_COLLECTOR = $Collector }
if (-not $env:PB_LOG_LEVEL) { $env:PB_LOG_LEVEL = "debug" }
if (-not $NoShow -and -not $env:PB_SHOW_ON_START) { $env:PB_SHOW_ON_START = "1" }

Write-Host "cargo: $(cargo --version)"
npx tauri dev
