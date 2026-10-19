# Runtime audit -- minimize (WM_SYSCOMMAND/SC_MINIMIZE) then restore via second instance
param([string]$Root = "D:\desktop\电量显示插件")
$ErrorActionPreference = "Stop"
$exe = Join-Path $Root "src-tauri\target\release\peripheral-battery.exe"
$py  = Join-Path $Root ".venv\Scripts\python.exe"
$log = Join-Path $env:LOCALAPPDATA "PeripheralBattery\logs\app.log"
$outDir = Join-Path $Root "design\audit\evidence\runtime"
$shotDir = Join-Path $Root "design\audit\runtime-screens"
New-Item -ItemType Directory -Force -Path $outDir, $shotDir | Out-Null
$report = Join-Path $outDir "test4-minimize-restore.log"
$shot = Join-Path $shotDir "test4-minimize-restore.png"

Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public static class W4 {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder sb, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr h, StringBuilder sb, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr h, uint msg, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static string Title(IntPtr h) { var sb = new StringBuilder(512); GetWindowTextW(h, sb, 512); return sb.ToString(); }
  public static string Cls(IntPtr h) { var sb = new StringBuilder(256); GetClassNameW(h, sb, 256); return sb.ToString(); }
}
'@
[void][W4]::SetProcessDPIAware()
function Read-From($path, $offset) {
  $fs = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
  try { $fs.Position = $offset; $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8); return $sr.ReadToEnd() } finally { $fs.Dispose() }
}
function Find-Main([int]$appPid) {
  $script:result = @()
  $cb = [W4+EnumProc]{
    param($h, $l)
    $p = 0; [void][W4]::GetWindowThreadProcessId($h, [ref]$p)
    if ($p -eq $appPid) {
      $r = New-Object W4+RECT; [void][W4]::GetWindowRect($h, [ref]$r)
      $script:result += [pscustomobject]@{ hwnd=$h; title=[W4]::Title($h); cls=[W4]::Cls($h); visible=[W4]::IsWindowVisible($h); iconic=[W4]::IsIconic($h); rect="$($r.Left),$($r.Top) $($r.Right-$r.Left)x$($r.Bottom-$r.Top)" }
    }
    return $true
  }
  [void][W4]::EnumWindows($cb, [IntPtr]::Zero)
  return $script:result
}
$out = New-Object System.Collections.Generic.List[string]
function Say($s) { $out.Add($s); Write-Output $s }

# 临时 pinned=true，避免自动化期间失焦自动隐藏干扰「最小化恢复」路径；结束时恢复。
$appJson = Join-Path $env:LOCALAPPDATA "PeripheralBattery\app.json"
$cfgBackup = if (Test-Path $appJson) { Get-Content -Raw $appJson } else { $null }
'{"pinned":true}' | Set-Content -Encoding utf8 -NoNewline $appJson
Say ("app.json temporarily pinned=true (backup=" + $cfgBackup + ")")

Get-Process peripheral-battery -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400
$offset = (Get-Item $log).Length
$env:PB_LOG_LEVEL = "debug"
$env:PB_COLLECTOR = '"' + $py + '" -m collector --stdio --fake --scenario static --speed 1'
$env:PB_SINGLE_INSTANCE_NAME = "Global\PBAuditRuntime-min4"
$env:PB_SHOW_ON_START = "1"
$env:PB_QUIT_AFTER_MS = "40000"
Remove-Item Env:PB_FPS_PROBE -ErrorAction SilentlyContinue
Remove-Item Env:PB_HIDE_AFTER_MS -ErrorAction SilentlyContinue

$app = Start-Process -FilePath $exe -PassThru
Say ("APP pid={0} start={1:HH:mm:ss.fff}" -f $app.Id, (Get-Date))
# wait for first show
$deadline = (Get-Date).AddSeconds(12)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 150
  if ((Read-From $log $offset) -match 'window shown reason=ui-ready') { break }
}
Start-Sleep -Milliseconds 500
# 让启动 2s 兜底（ui-ready-fallback）先过去，避免它把最小化后的面板重新拉起干扰本测试
Start-Sleep -Seconds 3
$wins = Find-Main $app.Id
$main = $wins | Where-Object { $_.cls -eq 'Tauri Window' } | Select-Object -First 1
Say ("before minimize: " + (($wins | ForEach-Object { "title='$($_.title)' cls=$($_.cls) visible=$($_.visible) iconic=$($_.iconic) rect=$($_.rect)" }) -join ' | '))
if (-not $main) { Say "FAIL: main window not found"; exit 2 }
$marker = (Get-Item $log).Length
$posted = [W4]::PostMessageW($main.hwnd, 0x0112, [IntPtr]0xF020, [IntPtr]::Zero)   # WM_SYSCOMMAND / SC_MINIMIZE
Say ("PostMessage WM_SYSCOMMAND SC_MINIMIZE posted={0} hwnd={1}" -f $posted, $main.hwnd)
Start-Sleep -Milliseconds 250
$wins2 = Find-Main $app.Id
$main2 = $wins2 | Where-Object { $_.cls -eq 'Tauri Window' } | Select-Object -First 1
Say ("after minimize +250ms: " + (($wins2 | ForEach-Object { "title='$($_.title)' cls=$($_.cls) visible=$($_.visible) iconic=$($_.iconic) rect=$($_.rect)" }) -join ' | '))
Start-Sleep -Milliseconds 1200
$wins2b = Find-Main $app.Id
$main2b = $wins2b | Where-Object { $_.cls -eq 'Tauri Window' } | Select-Object -First 1
Say ("after minimize +1450ms: " + (($wins2b | ForEach-Object { "title='$($_.title)' cls=$($_.cls) visible=$($_.visible) iconic=$($_.iconic) rect=$($_.rect)" }) -join ' | '))
$segMin = Read-From $log $marker
Say "=== minimize-phase logs ==="
@($segMin -split "`r?`n" | Where-Object { $_ -match 'window|auto-hide|focus|minimiz|WARN|ERROR' }) | ForEach-Object { Say ("LOG " + $_) }

$beforeAct = (Get-Item $log).Length
$p2 = Start-Process -FilePath $exe -PassThru
$exited = $p2.WaitForExit(5000)
Say ("second instance pid={0} exited={1} exit={2}" -f $p2.Id, $exited, $(if ($exited) { $p2.ExitCode } else { 'n/a' }))
$deadline = (Get-Date).AddSeconds(8)
$seg2 = ""
while ((Get-Date) -lt $deadline) {
  $seg2 = Read-From $log $beforeAct
  if ($seg2 -match 'window shown reason=single-instance') { Start-Sleep -Milliseconds 700; break }
  Start-Sleep -Milliseconds 100
}
$seg2 = Read-From $log $beforeAct
Say "=== restore-phase logs ==="
@($seg2 -split "`r?`n" | Where-Object { $_ -match 'single-instance|window was minimized|window show|window shown|panel position|webview memory|WARN|ERROR' }) | ForEach-Object { Say ("LOG " + $_) }
$wins3 = Find-Main $app.Id
$main3 = $wins3 | Where-Object { $_.cls -eq 'Tauri Window' } | Select-Object -First 1
Say ("after restore: " + (($wins3 | ForEach-Object { "title='$($_.title)' cls=$($_.cls) visible=$($_.visible) iconic=$($_.iconic) rect=$($_.rect)" }) -join ' | '))
$shotOk = $false
try { & (Join-Path $Root "tools\capture_screen.ps1") -Out $shot | ForEach-Object { Say $_ }; $shotOk = Test-Path $shot } catch { Say ("screenshot FAILED: " + $_) }

$minLine = @($seg2 -split "`r?`n") | Where-Object { $_ -match 'window was minimized -> unminimize before show' } | Select-Object -First 1
$shownLine = @($seg2 -split "`r?`n") | Where-Object { $_ -match 'window shown origin=' } | Select-Object -First 1
$originBad = $shownLine -match 'origin=\(-32000'
$unmin = [bool]$minLine
$iconic = $(if ($main3) { $main3.iconic } else { 'n/a' })
$dipOk = $shownLine -match 'dip=\(780\.0x440\.0\)'
Say ("checks: unminimize_logged={0} shown_origin={1} origin_not_-32000={2} dip780x440={3} IsIconic={4} visible={5}" -f $unmin, $shownLine, (-not $originBad), $dipOk, $iconic, $(if ($main3) { $main3.visible } else { 'n/a' }))
Say ("RESULT minimize_restore=" + $(if ($unmin -and -not $originBad -and $dipOk -and ($iconic -eq $false) -and $main3.visible) { 'PASS' } else { 'FAIL' }))

$app.WaitForExit(45000) | Out-Null
$exitCode = $(if ($app.HasExited) { $app.ExitCode } else { 'TIMEOUT' })
Start-Sleep -Milliseconds 700
$left = @(Get-Process peripheral-battery,collector -ErrorAction SilentlyContinue)
Say ("app exit code=" + $exitCode)
Say ("leftover processes=" + $(if ($left.Count -eq 0) { 'none' } else { ($left | ForEach-Object { "$($_.ProcessName):$($_.Id)" }) -join ',' }))
$allLog = Read-From $log $offset
$errs = @($allLog -split "`r?`n" | Where-Object { $_ -match '\[ERROR\]|\[WARN\]|panic' })
Say ("ERROR/WARN count=" + $errs.Count); $errs | ForEach-Object { Say ("  BAD " + $_) }
Say ("screenshot=$shot exists=" + $shotOk)
$out | Set-Content -Encoding utf8 $report
if ($cfgBackup -ne $null) { [System.IO.File]::WriteAllText($appJson, $cfgBackup) } else { [System.IO.File]::WriteAllText($appJson, '{"pinned":false}') }
Say ("app.json restored = " + (Get-Content -Raw $appJson))
Say ("report=" + $report)
