# Runtime audit -- visible FPS probe on release build (fake static collector)
param([string]$Root = "D:\desktop\电量显示插件")
$ErrorActionPreference = "Stop"
$exe = Join-Path $Root "src-tauri\target\release\peripheral-battery.exe"
$py  = Join-Path $Root ".venv\Scripts\python.exe"
$log = Join-Path $env:LOCALAPPDATA "PeripheralBattery\logs\app.log"
$outDir = Join-Path $Root "design\audit\evidence\runtime"
$shotDir = Join-Path $Root "design\audit\runtime-screens"
New-Item -ItemType Directory -Force -Path $outDir, $shotDir | Out-Null
$report = Join-Path $outDir "test3-visible-fps.log"
$shot = Join-Path $shotDir "test3-visible-fps.png"

function Read-From($path, $offset) {
  $fs = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
  try { $fs.Position = $offset; $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8); return $sr.ReadToEnd() } finally { $fs.Dispose() }
}
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public static class Dpi3 { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
public static class W2 {
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
  $cb = [W2+EnumProc]{
    param($h, $l)
    $p = 0; [void][W2]::GetWindowThreadProcessId($h, [ref]$p)
    if ($p -eq $appPid) {
      $r = New-Object W2+RECT; [void][W2]::GetWindowRect($h, [ref]$r)
      $list.Add([pscustomobject]@{ title=[W2]::Title($h); visible=[W2]::IsWindowVisible($h); iconic=[W2]::IsIconic($h); rect="$($r.Left),$($r.Top) $($r.Right-$r.Left)x$($r.Bottom-$r.Top)" })
    }
    return $true
  }
  [void][W2]::EnumWindows($cb, [IntPtr]::Zero)
  return $list
}
[void][Dpi3]::SetProcessDPIAware()
$out = New-Object System.Collections.Generic.List[string]
function Say($s) { $out.Add($s); Write-Output $s }

Get-Process peripheral-battery -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400
$offset = (Get-Item $log).Length
$env:PB_LOG_LEVEL = "debug"
$env:PB_COLLECTOR = '"' + $py + '" -m collector --stdio --fake --scenario static --speed 1'
$env:PB_SINGLE_INSTANCE_NAME = "Global\PBAuditRuntime-fps3"
$env:PB_FPS_PROBE = "3000"
$env:PB_SHOW_ON_START = "1"
$env:PB_QUIT_AFTER_MS = "16000"
Remove-Item Env:PB_HIDE_AFTER_MS -ErrorAction SilentlyContinue
Remove-Item Env:PB_MOCK_MUTATE_MS -ErrorAction SilentlyContinue

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
    Say ("parsed: fps={0} p50={1} p95={2} p99={3} max={4} frames={5}" -f $j.fps, $j.p50_ms, $j.p95_ms, $j.p99_ms, $j.max_ms, $j.frames)
    Say ("parsed: panel_rect={0}x{1} at ({2},{3}) dpr={4} backdrop_filter='{5}' tauri={6} rows={7} hidden={8} visibility={9}" -f `
      $j.panel_rect.w, $j.panel_rect.h, $j.panel_rect.x, $j.panel_rect.y, $j.dpr, $j.backdrop_filter, $j.tauri, $j.rows, $j.hidden, $j.visibility)
    Say ("parsed: inner={0}x{1} avg_ms={2}" -f $j.inner.w, $j.inner.h, $j.avg_ms)
  } catch { Say ("FPS parse error: " + $_) }
} else { Say "FPS_PROBE line MISSING" }

$segAll = Read-From $log $offset
$lines = @($segAll -split "`r?`n" | Where-Object { $_ })
Say "=== window / log lines ==="
$lines | Where-Object { $_ -match 'window shown|window created|panel position|FPS_PROBE|collector-state|hello protocol|ERROR|WARN|panic|auto-hide|window hidden' } | ForEach-Object { Say ("LOG " + $_) }
$wins = WinList $app.Id
Say ("visible EnumWindows: " + (($wins | ForEach-Object { "title='$($_.title)' visible=$($_.visible) iconic=$($_.iconic) rect=$($_.rect)" }) -join ' | '))

$shotOk = $false
try { & (Join-Path $Root "tools\capture_screen.ps1") -Out $shot | ForEach-Object { Say $_ }; $shotOk = Test-Path $shot } catch { Say ("screenshot FAILED: " + $_) }
$app.WaitForExit(30000) | Out-Null
$exitCode = $(if ($app.HasExited) { $app.ExitCode } else { 'TIMEOUT' })
Start-Sleep -Milliseconds 700
$left = @(Get-Process peripheral-battery,collector -ErrorAction SilentlyContinue | Select-Object Id,ProcessName)
$errs = @($lines | Where-Object { $_ -match '\[ERROR\]|\[WARN\]|panic' })
Say ("app exit code=" + $exitCode)
Say ("leftover processes=" + $(if ($left.Count -eq 0) { 'none' } else { ($left | ForEach-Object { "$($_.ProcessName):$($_.Id)" }) -join ',' }))
Say ("ERROR/WARN count=" + $errs.Count); $errs | ForEach-Object { Say ("  BAD " + $_) }
Say ("screenshot=$shot exists=" + $shotOk)
$out | Set-Content -Encoding utf8 $report
Say ("report=" + $report)
