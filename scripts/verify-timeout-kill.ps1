<#
.SYNOPSIS
  验证 shutdown 超时路径用 TerminateJobObject 清掉 collector 的全部子孙进程。

.DESCRIPTION
  用 `cmd /c ping -n 60` 冒充不响应 shutdown 的 collector；PB_QUIT_AFTER_MS 到点后
  app 发 shutdown、2s 超时 → TerminateJobObject。断言：app 进程树里所有 collector
  子孙（cmd/ping/conhost）在退出后都不存在，且日志有 TerminateJobObject 计数。
#>
param(
    [string]$Exe = "D:\desktop\电量显示插件\src-tauri\target\debug\peripheral-battery.exe",
    [string]$LogPath = "$env:LOCALAPPDATA\PeripheralBattery\logs\app.log"
)

$ErrorActionPreference = "Stop"
Get-Process peripheral-battery -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

$env:PB_QUIT_AFTER_MS = "7000"
$env:PB_LOG_LEVEL = "debug"
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
Remove-Item Env:PB_FPS_PROBE -ErrorAction SilentlyContinue
Remove-Item Env:PB_TEST_TRAY_ACTION -ErrorAction SilentlyContinue
$env:PB_COLLECTOR = "C:\Windows\System32\cmd.exe /c ping -n 60 127.0.0.1"
$env:PB_SINGLE_INSTANCE_NAME = "Global\PBTimeoutKillTest"

$app = Start-Process -FilePath $Exe -PassThru
Write-Output "app pid=$($app.Id); collector = cmd /c ping -n 60 127.0.0.1"
Start-Sleep -Seconds 4

# 收集 app 进程树
$all = Get-CimInstance Win32_Process
$set = [System.Collections.Generic.List[int]]::new()
$set.Add([int]$app.Id)
$changed = $true
while ($changed) {
    $changed = $false
    foreach ($proc in $all) {
        $child = [int]$proc.ProcessId
        $parent = [int]$proc.ParentProcessId
        if ($set.Contains($parent) -and -not $set.Contains($child)) {
            $set.Add($child)
            $changed = $true
        }
    }
}
$tree = @()
foreach ($id in $set) {
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($p) { $tree += [pscustomobject]@{ Id = $id; Name = $p.ProcessName } }
}
Write-Output ("tree before quit: " + (($tree | ForEach-Object { "$($_.Name):$($_.Id)" }) -join ", "))

# 等到 PB_QUIT_AFTER_MS(7s) + shutdown 2s + terminate
Start-Sleep -Seconds 8

$lines = Get-Content $LogPath
$idx = ($lines | Select-String -Pattern ("app start version=.*pid=" + $app.Id)).LineNumber
if ($idx) {
    $lines[($idx - 1)..($lines.Count - 1)] |
        Select-String -Pattern "shutdown handshake|terminate|TerminateJobObject|job object|sidecar exit|quit collector" |
        ForEach-Object { Write-Output ("LOG " + $_.Line) }
}

Write-Output "--- leftover check ---"
$leftover = @()
foreach ($item in $tree) {
    if ($item.Id -eq $app.Id) { continue }
    $p = Get-Process -Id $item.Id -ErrorAction SilentlyContinue
    if ($p) { $leftover += "$($p.ProcessName):$($p.Id)" }
}
Write-Output ("app alive: " + [bool](Get-Process -Id $app.Id -ErrorAction SilentlyContinue))
if ($leftover.Count -gt 0) {
    Write-Output ("FAIL leftover: " + ($leftover -join ", "))
    exit 1
}
Write-Output "PASS: no orphan collector/cmd/ping/conhost"
