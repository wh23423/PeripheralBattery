# Runtime audit -- hidden tray mirror + single-instance resume (release build)
# Writes evidence to design/audit/evidence/runtime/test2-hidden-tray.log
param(
  [string]$Root = "D:\desktop\电量显示插件"
)
$ErrorActionPreference = "Stop"
$exe = Join-Path $Root "src-tauri\target\release\peripheral-battery.exe"
$py  = Join-Path $Root ".venv\Scripts\python.exe"
$mock = Join-Path $Root "scripts\mock-collector.py"
$log = Join-Path $env:LOCALAPPDATA "PeripheralBattery\logs\app.log"
$outDir = Join-Path $Root "design\audit\evidence\runtime"
$shotDir = Join-Path $Root "design\audit\runtime-screens"
New-Item -ItemType Directory -Force -Path $outDir, $shotDir | Out-Null
$report = Join-Path $outDir "test2-hidden-tray.log"
$shot = Join-Path $shotDir "test2-tray-resume.png"

function Read-From($path, $offset) {
  if (-not (Test-Path $path)) { return "" }
  $fs = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
  try {
    if ($offset -gt $fs.Length) { $offset = 0 }
    $fs.Position = $offset
    $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
    return $sr.ReadToEnd()
  } finally { $fs.Dispose() }
}
function Log-Lines($text) {
  return @($text -split "`r?`n" | Where-Object { $_ })
}
function Line-Time([string]$line) {
  if ($line -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})') {
    return [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss.fff', $null)
  }
  return $null
}

Add-Type @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class W {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder sb, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr h, StringBuilder sb, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern IntPtr SendMessageTimeoutW(IntPtr h, uint msg, IntPtr w, IntPtr l, uint flags, uint timeout, out IntPtr result);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static string Title(IntPtr h) { var sb = new StringBuilder(512); GetWindowTextW(h, sb, 512); return sb.ToString(); }
  public static string Cls(IntPtr h) { var sb = new StringBuilder(256); GetClassNameW(h, sb, 256); return sb.ToString(); }
}
'@
function Get-AppWindows([int]$appPid) {
  $list = New-Object System.Collections.Generic.List[object]
  $cb = [W+EnumProc]{
    param($h, $l)
    $pid2 = 0
    [void][W]::GetWindowThreadProcessId($h, [ref]$pid2)
    if ($pid2 -eq $appPid) {
      $r = New-Object W+RECT
      [void][W]::GetWindowRect($h, [ref]$r)
      $t = [W]::Title($h); $c = [W]::Cls($h)
      $list.Add([pscustomobject]@{ hwnd = $h; title = $t; cls = $c; visible = [W]::IsWindowVisible($h); iconic = [W]::IsIconic($h); rect = "$($r.Left),$($r.Top) $($r.Right-$r.Left)x$($r.Bottom-$r.Top)" })
    }
    return $true
  }
  [void][W]::EnumWindows($cb, [IntPtr]::Zero)
  return $list
}

$out = New-Object System.Collections.Generic.List[string]
function Say($s) { $out.Add($s); Write-Output $s }

Get-Process peripheral-battery -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400
$offset = (Get-Item $log).Length

$env:PB_LOG_LEVEL = "debug"
$env:PB_MOCK_MUTATE_MS = "1500"
$env:PB_COLLECTOR = '"' + $py + '" "' + $mock + '"'
$env:PB_SINGLE_INSTANCE_NAME = "Global\PBAuditRuntime-tray2"
$env:PB_QUIT_AFTER_MS = "45000"
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
Remove-Item Env:PB_FPS_PROBE -ErrorAction SilentlyContinue
Remove-Item Env:PB_HIDE_AFTER_MS -ErrorAction SilentlyContinue

$t0 = Get-Date
$app = Start-Process -FilePath $exe -PassThru
Say ("APP pid={0} start={1:HH:mm:ss.fff}" -f $app.Id, $t0)
Start-Sleep -Seconds 14
$seg1 = Read-From $log $offset
$lines1 = Log-Lines $seg1
Say "=== phase A: hidden 14s ==="
$lines1 | Where-Object { $_ -match 'tray-state source=rust|TrySuspend|window hidden|window startup|hello protocol|ERROR|WARN|panic' } | ForEach-Object { Say ("LOG " + $_) }
$rust = @($lines1 | Where-Object { $_ -match 'tray-state source=rust' })
$levels = @($rust | ForEach-Object { if ($_ -match 'level=Some\((\d+)\)') { [int]$Matches[1] } })
$distinct = @($levels | Select-Object -Unique)
$suspend = @($lines1 | Where-Object { $_ -match 'TrySuspend completed suspended=true' })
$hiddenOk = ($rust.Count -ge 2) -and ($distinct.Count -ge 2) -and ($suspend.Count -ge 1)
Say ("hidden-phase: rust_tray_updates={0} distinct_levels=[{1}] suspend_true={2} -> {3}" -f $rust.Count, ($distinct -join ','), $suspend.Count, $(if ($hiddenOk) { 'PASS' } else { 'FAIL' }))
$winHidden = Get-AppWindows $app.Id
Say ("hidden EnumWindows: " + (($winHidden | ForEach-Object { "title='$($_.title)' cls=$($_.cls) visible=$($_.visible) iconic=$($_.iconic) rect=$($_.rect)" }) -join ' | '))
$errs1 = @($lines1 | Where-Object { $_ -match '\[ERROR\]|\[WARN\]' })
Say ("hidden-phase ERROR/WARN count=" + $errs1.Count); $errs1 | ForEach-Object { Say ("  BAD " + $_) }

# --- phase B: second instance activates (single instance) ---
$beforeAct = (Get-Item $log).Length
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$p2 = Start-Process -FilePath $exe -PassThru
$exited = $p2.WaitForExit(5000)
$sw.Stop()
Say ("second instance pid={0} exited={1} in {2}ms exit={3}" -f $p2.Id, $exited, $sw.ElapsedMilliseconds, $(if ($exited) { $p2.ExitCode } else { 'n/a' }))

$deadline = (Get-Date).AddSeconds(6)
$seg2 = ""
while ((Get-Date) -lt $deadline) {
  $seg2 = Read-From $log $beforeAct
  if ($seg2 -match 'tray-state source=frontend') { break }
  Start-Sleep -Milliseconds 50
}
$lines2 = Log-Lines $seg2
Say "=== phase B: resume logs ==="
$lines2 | Where-Object { $_ -match 'single-instance|window show|window shown|window was minimized|panel position|tray-state source=frontend|webview memory mode normal|ERROR|WARN' } | ForEach-Object { Say ("LOG " + $_) }

$actLine = $lines2 | Where-Object { $_ -match 'single-instance: activation event received' } | Select-Object -First 1
$shownLine = $lines2 | Where-Object { $_ -match 'window shown reason=single-instance' } | Select-Object -First 1
$getLine = $lines2 | Where-Object { $_ -match 'collector << .*"t":"get"' } | Select-Object -First 1
$feLine = $lines2 | Where-Object { $_ -match 'tray-state source=frontend' } | Select-Object -First 1
$tAct = if ($actLine) { Line-Time $actLine } else { $null }
$tShown = if ($shownLine) { Line-Time $shownLine } else { $null }
$tGet = if ($getLine) { Line-Time $getLine } else { $null }
$tFe = if ($feLine) { Line-Time $feLine } else { $null }
if ($tGet -and $tFe) { Say ("snapshot->frontend-update latency = {0} ms" -f ($tFe - $tGet).TotalMilliseconds) }
if ($tShown -and $tFe) { Say ("window shown->frontend-update latency = {0} ms" -f ($tFe - $tShown).TotalMilliseconds) }
Say ("last rust tray-state before resume: " + (($rust | Select-Object -Last 2) -join ' || '))
Say ("frontend tray-state after resume: " + $(if ($feLine) { $feLine } else { 'MISSING' }))
$hiddenRustLast = ($rust | Select-Object -Last 1)
$resumeOk = [bool]$feLine -and [bool]$shownLine -and [bool]$actLine
Say ("resume check: " + $(if ($resumeOk) { 'PASS' } else { 'FAIL' }))

Start-Sleep -Milliseconds 1200
$shotOk = $false
try { & (Join-Path $Root "tools\capture_screen.ps1") -Out $shot | ForEach-Object { Say $_ }; $shotOk = Test-Path $shot } catch { Say ("screenshot FAILED: " + $_) }
$winShown = Get-AppWindows $app.Id
Say ("after-resume EnumWindows: " + (($winShown | ForEach-Object { "title='$($_.title)' visible=$($_.visible) iconic=$($_.iconic) rect=$($_.rect)" }) -join ' | '))

# --- wait for self quit ---
$app.WaitForExit(30000) | Out-Null
$exitCode = $(if ($app.HasExited) { $app.ExitCode } else { 'TIMEOUT' })
Say ("app exit code=" + $exitCode)
Start-Sleep -Milliseconds 800
$left = @(Get-Process peripheral-battery,collector -ErrorAction SilentlyContinue | Select-Object Id,ProcessName)
Say ("leftover processes=" + $(if ($left.Count -eq 0) { 'none' } else { ($left | ForEach-Object { "$($_.ProcessName):$($_.Id)" }) -join ',' }))
$lines3 = Log-Lines (Read-From $log $offset)
$errs = @($lines3 | Where-Object { $_ -match '\[ERROR\]|\[WARN\]|panic' })
Say ("all-run ERROR/WARN count=" + $errs.Count); $errs | ForEach-Object { Say ("  BAD " + $_) }
Say ("screenshot=" + $shot + " exists=" + $shotOk)
Say ("RESULT hidden_tray=" + $(if ($hiddenOk) { 'PASS' } else { 'FAIL' }) + " resume=" + $(if ($resumeOk) { 'PASS' } else { 'FAIL' }) + " exit=" + $exitCode + " leftovers=" + $left.Count)
$out | Set-Content -Encoding utf8 $report
Say ("report=" + $report)
