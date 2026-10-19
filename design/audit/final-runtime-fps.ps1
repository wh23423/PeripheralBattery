# Final independent audit - test 2: visible FPS probe + DPI-aware screenshot.
param(
  [string]$Root = "D:\desktop\电量显示插件",
  [string]$Instance = "Global\PBAuditFinal-fps",
  [int]$FpsMs = 2000,
  [int]$QuitMs = 12000
)
$ErrorActionPreference = "Stop"
$exe = Join-Path $Root "src-tauri\target\release\peripheral-battery.exe"
$py  = Join-Path $Root ".venv\Scripts\python.exe"
$log = Join-Path $env:LOCALAPPDATA "PeripheralBattery\logs\app.log"
$ev  = Join-Path $Root "design\audit\final-runtime-evidence"
$scr = Join-Path $Root "design\audit\final-runtime-screens"
New-Item -ItemType Directory -Force -Path $ev, $scr | Out-Null
$report = Join-Path $ev "test2-visible-fps.log"
$shot = Join-Path $scr "test2-visible-fps.png"

function Read-From($path, $offset) {
  $fs = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
  try { $fs.Position = $offset; $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8); return $sr.ReadToEnd() } finally { $fs.Dispose() }
}
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public static class DpiF { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
public static class WF {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder sb, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  public static string Title(IntPtr h) { var sb = new StringBuilder(512); GetWindowTextW(h, sb, 512); return sb.ToString(); }
}
'@
function WinList([int]$appPid) {
  $list = New-Object System.Collections.Generic.List[object]
  $cb = [WF+EnumProc]{
    param($h, $l)
    $p = 0; [void][WF]::GetWindowThreadProcessId($h, [ref]$p)
    if ($p -eq $appPid) {
      $r = New-Object WF+RECT; [void][WF]::GetWindowRect($h, [ref]$r)
      $list.Add([pscustomobject]@{ title=[WF]::Title($h); visible=[WF]::IsWindowVisible($h); iconic=[WF]::IsIconic($h); rect="$($r.Left),$($r.Top) $($r.Right-$r.Left)x$($r.Bottom-$r.Top)" })
    }
    return $true
  }
  [void][WF]::EnumWindows($cb, [IntPtr]::Zero)
  return $list
}
[void][DpiF]::SetProcessDPIAware()
$out = New-Object System.Collections.Generic.List[string]
function Say($s) { $out.Add([string]$s); Write-Output $s }

$offset = (Get-Item $log).Length
$env:PB_LOG_LEVEL = "debug"
$env:PB_COLLECTOR = '"' + $py + '" -m collector --stdio --fake --scenario static --speed 1'
$env:PB_SINGLE_INSTANCE_NAME = $Instance
$env:PB_FPS_PROBE = "$FpsMs"
$env:PB_SHOW_ON_START = "1"
$env:PB_QUIT_AFTER_MS = "$QuitMs"
Remove-Item Env:PB_HIDE_AFTER_MS -ErrorAction SilentlyContinue
Remove-Item Env:PB_MOCK_MUTATE_MS -ErrorAction SilentlyContinue

# pinned=true is set by caller before launch
$app = Start-Process -FilePath $exe -PassThru
Say ("APP pid={0} start={1:HH:mm:ss.fff}" -f $app.Id, (Get-Date))

$fpsLine = $null
$deadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 200
  $seg = Read-From $log $offset
  $m = [regex]::Match($seg, 'FPS_PROBE (\{"duration_ms".*\})')
  if ($m.Success) { $fpsLine = $m.Value; break }
}
Say "=== FPS_PROBE ==="
if ($fpsLine) {
  Say $fpsLine
  try {
    $j = $fpsLine.Substring('FPS_PROBE '.Length) | ConvertFrom-Json
    Say ("parsed: fps={0} avg={1} p50={2} p95={3} p99={4} max={5} frames={6}" -f $j.fps, $j.avg_ms, $j.p50_ms, $j.p95_ms, $j.p99_ms, $j.max_ms, $j.frames)
    Say ("parsed: panel_rect={0}x{1} dpr={2} backdrop_filter='{3}' tauri={4} rows={5} hidden={6} visibility={7} inner={8}x{9}" -f `
      $j.panel_rect.w, $j.panel_rect.h, $j.dpr, $j.backdrop_filter, $j.tauri, $j.rows, $j.hidden, $j.visibility, $j.inner.w, $j.inner.h)
  } catch { Say ("FPS parse error: " + $_) }
} else { Say "FPS_PROBE line MISSING" }

# wait a moment so the whole fake snapshot has rendered, then screenshot while visible
Start-Sleep -Milliseconds 600
$wins = WinList $app.Id
Say ("visible windows: " + (($wins | ForEach-Object { "title='$($_.title)' visible=$($_.visible) iconic=$($_.iconic) rect=$($_.rect)" }) -join ' | '))
$shotOk = $false
try { & (Join-Path $Root "tools\capture_screen.ps1") -Out $shot | ForEach-Object { Say $_ }; $shotOk = Test-Path $shot } catch { Say ("screenshot FAILED: " + $_) }

$app.WaitForExit(30000) | Out-Null
$exitCode = $(if ($app.HasExited) { $app.ExitCode } else { 'TIMEOUT' })
Start-Sleep -Milliseconds 800
$left = @(Get-Process peripheral-battery,collector -ErrorAction SilentlyContinue | Select-Object Id,ProcessName)
$segAll = Read-From $log $offset
[System.IO.File]::WriteAllText((Join-Path $ev "test2-visible-fps.appsegment.log"), $segAll, (New-Object System.Text.UTF8Encoding($false)))
$lines = @($segAll -split "`r?`n" | Where-Object { $_ })
Say "=== selected log lines ==="
$lines | Where-Object { $_ -match 'app start|window created|window shown|window hidden|panel position|FPS_PROBE|collector-state|hello protocol|collector stat|tray-state|ERROR|WARN|panic|auto-hide' } | ForEach-Object { Say ("LOG " + $_) }
Say ("app exit code=" + $exitCode)
Say ("leftover processes=" + $(if ($left.Count -eq 0) { 'none' } else { ($left | ForEach-Object { "$($_.ProcessName):$($_.Id)" }) -join ',' }))
Say ("screenshot=$shot exists=" + $shotOk)
$out | Set-Content -Encoding utf8 $report
Write-Output ("report=" + $report)
