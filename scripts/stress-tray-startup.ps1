<#
.SYNOPSIS
  托盘更新死锁回归压测：连续可见启动 N 次 + 隐藏启动 M 次，要求全部正常退出。

.DESCRIPTION
  每次运行：
    - 唯一 PB_SINGLE_INSTANCE_NAME（避免单实例互相干扰）
    - PB_COLLECTOR = scripts/mock-collector.py（快速产生多次 tray-state 更新）
    - PB_MOCK_MUTATE_MS=400（运行期间持续改电量，制造侧车线程↔主线程并发托盘更新）
    - 可见：PB_SHOW_ON_START=1 + PB_QUIT_AFTER_MS=5000
    - 隐藏：PB_QUIT_AFTER_MS=4000
  断言：进程在超时内自行退出、exit code=0、app.log 有该 pid 的 `app exit`；
  超时即判定 hang（taskkill /T /F 清理），记录到失败列表。

.EXAMPLE
  .\scripts\stress-tray-startup.ps1 -Exe src-tauri\target\release\peripheral-battery.exe
#>
param(
    [string]$Exe = "D:\desktop\电量显示插件\src-tauri\target\release\peripheral-battery.exe",
    [int]$Visible = 40,
    [int]$Hidden = 10,
    [int]$VisibleQuitMs = 5000,
    [int]$HiddenQuitMs = 4000,
    [int]$TimeoutMs = 25000,
    [string]$OutJson = "D:\desktop\电量显示插件\design\verify\tray-stress-result.json"
)

$ErrorActionPreference = "Stop"
$root = "D:\desktop\电量显示插件"
$mock = Join-Path $root "scripts\mock-collector.py"
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $Exe)) { throw "exe 不存在: $Exe" }
if (-not (Test-Path $py)) { throw "python 不存在: $py" }

Get-Process peripheral-battery -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

$log = Join-Path $env:LOCALAPPDATA "PeripheralBattery\logs\app.log"
$env:PB_LOG_LEVEL = "info"
$env:PB_MOCK_MUTATE_MS = "400"
$env:PB_COLLECTOR = "$py $mock"
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
Remove-Item Env:PB_FPS_PROBE -ErrorAction SilentlyContinue
Remove-Item Env:PB_HIDE_AFTER_MS -ErrorAction SilentlyContinue
Remove-Item Env:PB_TEST_TRAY_ACTION -ErrorAction SilentlyContinue

$startedAt = Get-Date
$failures = New-Object System.Collections.ArrayList
$durations = New-Object System.Collections.ArrayList

function Run-One([string]$kind, [int]$index, [int]$quitMs, [bool]$show) {
    $name = "Global\PBStress-$kind-$index-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    $env:PB_SINGLE_INSTANCE_NAME = $name
    if ($show) {
        $env:PB_SHOW_ON_START = "1"
    } else {
        Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
    }
    $env:PB_QUIT_AFTER_MS = "$quitMs"

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process -FilePath $Exe -PassThru
    $exited = $p.WaitForExit($TimeoutMs)
    $sw.Stop()

    $record = [ordered]@{
        kind = $kind
        index = $index
        pid = $p.Id
        exited = $exited
        exit_code = if ($exited) { $p.ExitCode } else { $null }
        duration_ms = [int]$sw.ElapsedMilliseconds
        needs_taskkill = (-not $exited)
    }

    if (-not $exited) {
        # 死锁/卡死：连同子孙进程强制清理
        & taskkill.exe /PID $p.Id /T /F 2>&1 | Out-Null
        [void]$failures.Add($record)
        Write-Output ("[HANG] {0} #{1} pid={2} >{3}ms（已 taskkill /T /F）" -f $kind, $index, $p.Id, $TimeoutMs)
        return
    }
    [void]$durations.Add($sw.ElapsedMilliseconds)

    # 进程退出后校验日志：该 pid 有 app exit（日志可能已轮转，取最后 5MB 窗口）
    $lines = Get-Content $log -ErrorAction SilentlyContinue
    $idxArr = @($lines | Select-String -Pattern ("app start version=.*\bpid=" + $p.Id + "\b")).LineNumber
    $idx = if ($idxArr.Count -gt 0) { $idxArr[-1] } else { 0 }
    $hasExit = $false
    $hasQuitStart = $false
    $trayBegin = 0
    $trayEnd = 0
    if ($idx -gt 0 -and $idx -le $lines.Count) {
        $runLines = $lines[($idx - 1)..($lines.Count - 1)]
        $hasQuitStart = [bool]($runLines | Select-String -Pattern "quit requested reason=PB_QUIT_AFTER_MS")
        $hasExit = [bool]($runLines | Select-String -Pattern "app exit")
        $trayBegin = @($runLines | Select-String -Pattern "tray-update begin").Count
        $trayEnd = @($runLines | Select-String -Pattern "tray-update end").Count
    }
    $record["has_app_exit"] = $hasExit
    $record["has_quit_start"] = $hasQuitStart
    $record["tray_update_begin"] = $trayBegin
    $record["tray_update_end"] = $trayEnd
    $ok = ($p.ExitCode -eq 0) -and $hasExit
    if (-not $ok) {
        [void]$failures.Add($record)
        Write-Output ("[FAIL] {0} #{1} pid={2} exit={3} app_exit={4} tray_begin={5} tray_end={6}" -f $kind, $index, $p.Id, $p.ExitCode, $hasExit, $trayBegin, $trayEnd)
    } elseif (($index % 5) -eq 0) {
        Write-Output ("  ok {0} #{1}: {2}ms" -f $kind, $index, $sw.ElapsedMilliseconds)
    }
    Start-Sleep -Milliseconds 120
}

Write-Output ("=== visible starts: {0} (PB_SHOW_ON_START=1, quit={1}ms) exe={2} ===" -f $Visible, $VisibleQuitMs, $Exe)
for ($i = 1; $i -le $Visible; $i++) { Run-One "visible" $i $VisibleQuitMs $true }
Write-Output ("=== hidden starts: {0} (quit={1}ms) ===" -f $Hidden, $HiddenQuitMs)
for ($i = 1; $i -le $Hidden; $i++) { Run-One "hidden" $i $HiddenQuitMs $false }

$elapsed = (Get-Date) - $startedAt
$exeHash = (Get-FileHash $Exe -Algorithm SHA256).Hash
$result = [ordered]@{
    exe = $Exe
    exe_sha256 = $exeHash
    exe_mtime = (Get-Item $Exe).LastWriteTime.ToString("o")
    started_at = $startedAt.ToString("o")
    elapsed_s = [math]::Round($elapsed.TotalSeconds, 1)
    visible_total = $Visible
    visible_ok = $Visible - @($failures | Where-Object { $_.kind -eq "visible" }).Count
    hidden_total = $Hidden
    hidden_ok = $Hidden - @($failures | Where-Object { $_.kind -eq "hidden" }).Count
    hangs = @($failures | Where-Object { $_.needs_taskkill }).Count
    failures = $failures
}
$json = $result | ConvertTo-Json -Depth 6
$json | Set-Content -Encoding utf8 $OutJson
Write-Output "===== SUMMARY ====="
Write-Output ("visible: {0}/{1} ok" -f $result.visible_ok, $result.visible_total)
Write-Output ("hidden : {0}/{1} ok" -f $result.hidden_ok, $result.hidden_total)
Write-Output ("hangs  : {0}  elapsed={1}s" -f $result.hangs, $result.elapsed_s)
Write-Output ("exe_sha256={0}" -f $exeHash)
Write-Output ("result_json={0}" -f $OutJson)

Remove-Item Env:PB_MOCK_MUTATE_MS -ErrorAction SilentlyContinue
Remove-Item Env:PB_COLLECTOR -ErrorAction SilentlyContinue
Remove-Item Env:PB_QUIT_AFTER_MS -ErrorAction SilentlyContinue
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue

if ($failures.Count -gt 0) {
    Write-Output ("FAILED: {0} run(s)" -f $failures.Count)
    exit 1
}
Write-Output ("PASS: {0}/{0} visible + {1}/{1} hidden 全部自行正常退出" -f $Visible, $Hidden)
