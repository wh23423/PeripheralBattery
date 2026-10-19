<#
.SYNOPSIS
  验证 cleanup_residual 的 packaged collector 路径比较（`\\?\` 前缀规范化修复）。

.DESCRIPTION
  临时把 target\debug\collector\collector.exe 替换成 ping.exe 的副本（真正的 collector 备份为
  collector.exe.real），以「已在运行、路径等于资源目录 collector.exe」的进程冒充残留实例。
  启动 app 时应命中 cleanup_residual 的路径匹配分支，杀掉该进程并打日志。
  测试结束无论成败都恢复原 collector.exe。
#>
param(
    [string]$Root = "D:\desktop\电量显示插件"
)

$ErrorActionPreference = "Stop"
$exe = Join-Path $Root "src-tauri\target\debug\peripheral-battery.exe"
$collectorDir = Join-Path $Root "src-tauri\target\debug\collector"
$real = Join-Path $collectorDir "collector.exe"
$backup = Join-Path $collectorDir "collector.exe.real"
$ping = Join-Path $env:SystemRoot "System32\ping.exe"
$log = Join-Path $env:LOCALAPPDATA "PeripheralBattery\logs\app.log"

Get-Process peripheral-battery -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400

if (-not (Test-Path $real)) { throw "collector.exe 不存在: $real（请先完成 packaged collector 构建）" }
$residual = $null
try {
    Move-Item $real $backup -Force
    Copy-Item $ping $real -Force

    # 冒充残留 collector（路径 = resource_dir\collector\collector.exe）
    $residual = Start-Process -FilePath $real -ArgumentList "-n","60","127.0.0.1" -PassThru
    Start-Sleep -Milliseconds 600
    $r = Get-Process -Id $residual.Id -ErrorAction SilentlyContinue
    Write-Output ("fake residual collector pid=$($residual.Id) alive=$([bool]$r) exe=$real")

    $env:PB_QUIT_AFTER_MS = "6000"
    $env:PB_LOG_LEVEL = "debug"
    Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
    Remove-Item Env:PB_FPS_PROBE -ErrorAction SilentlyContinue
    Remove-Item Env:PB_TEST_TRAY_ACTION -ErrorAction SilentlyContinue
    Remove-Item Env:PB_COLLECTOR -ErrorAction SilentlyContinue
    $env:PB_SINGLE_INSTANCE_NAME = "Global\PBResidualTest"

    $app = Start-Process -FilePath $exe -PassThru
    Write-Output "app pid=$($app.Id) starting (should cleanup residual then spawn)"
    Start-Sleep -Seconds 3

    $lines = Get-Content $log
    $idx = ($lines | Select-String -Pattern ("app start version=.*pid=" + $app.Id)).LineNumber
    $hit = $null
    if ($idx) {
        $hit = $lines[($idx - 1)..($lines.Count - 1)] |
            Select-String -Pattern "cleanup residual packaged collector|sidecar spawn" |
            Select-Object -Last 4
        $hit | ForEach-Object { Write-Output ("LOG " + $_.Line) }
    }
    Start-Sleep -Seconds 5
    $residualAlive = [bool](Get-Process -Id $residual.Id -ErrorAction SilentlyContinue)
    Write-Output ("residual alive after app start: $residualAlive")
    if (-not ($hit | Where-Object { $_.Line -match "cleanup residual packaged collector" })) {
        throw "PASS 条件未满足：日志中没有 cleanup residual packaged collector"
    }
    if ($residualAlive) { throw "PASS 条件未满足：残留进程仍存活" }
    Write-Output "PASS: packaged collector 残留被路径规范化匹配并清理"
}
finally {
    Get-Process -Id $residual.Id -ErrorAction SilentlyContinue | Stop-Process -Force
    Get-Process peripheral-battery -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Milliseconds 500
    if (Test-Path $backup) {
        Move-Item $backup $real -Force
        Write-Output "restored: $real"
    }
    Get-Process cmd,ping -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -eq "" } | Out-Null
}
