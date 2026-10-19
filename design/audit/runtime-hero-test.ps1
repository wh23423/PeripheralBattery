# Runtime audit -- hero/main card selection on visible cold start (mock emits device-by-device)
param([string]$Root = "D:\desktop\电量显示插件")
$ErrorActionPreference = "Stop"
$exe = Join-Path $Root "src-tauri\target\release\peripheral-battery.exe"
$py  = Join-Path $Root ".venv\Scripts\python.exe"
$mock = Join-Path $Root "scripts\mock-collector.py"
$log = Join-Path $env:LOCALAPPDATA "PeripheralBattery\logs\app.log"
$outDir = Join-Path $Root "design\audit\evidence\runtime"
$shotDir = Join-Path $Root "design\audit\runtime-screens"
New-Item -ItemType Directory -Force -Path $outDir, $shotDir | Out-Null
$report = Join-Path $outDir "test10-hero.log"
$shot = Join-Path $shotDir "test10-hero-visible-start.png"
function Read-From($path, $offset) {
  $fs = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
  try { $fs.Position = $offset; $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8); return $sr.ReadToEnd() } finally { $fs.Dispose() }
}
$out = New-Object System.Collections.Generic.List[string]
function Say($s) { $out.Add($s); Write-Output $s }
Get-Process peripheral-battery -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400
$offset = (Get-Item $log).Length
$env:PB_LOG_LEVEL = "debug"
$env:PB_COLLECTOR = '"' + $py + '" "' + $mock + '"'
$env:PB_SINGLE_INSTANCE_NAME = "Global\PBAuditRuntime-hero10"
$env:PB_SHOW_ON_START = "1"
$env:PB_QUIT_AFTER_MS = "14000"
Remove-Item Env:PB_FPS_PROBE -ErrorAction SilentlyContinue
Remove-Item Env:PB_MOCK_MUTATE_MS -ErrorAction SilentlyContinue
Remove-Item Env:PB_HIDE_AFTER_MS -ErrorAction SilentlyContinue
$app = Start-Process -FilePath $exe -PassThru
Say ("APP pid={0}" -f $app.Id)
$deadline = (Get-Date).AddSeconds(10)
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 100
  if ((Read-From $log $offset) -match 'window shown reason=ui-ready') { break }
}
Start-Sleep -Milliseconds 900   # 4 个 device 事件到达后、失焦自动隐藏(约 3.2s)之前
Say ("screenshot at " + (Get-Date).ToString('HH:mm:ss.fff'))
& (Join-Path $Root "tools\capture_screen.ps1") -Out $shot | ForEach-Object { Say $_ }
$seg = Read-From $log $offset
$lines = @($seg -split "`r?`n" | Where-Object { $_ })
Say "=== relevant logs ==="
$lines | Where-Object { $_ -match 'window shown reason=ui-ready|collector device|tray-state source=frontend|hello protocol' } | Select-Object -First 30 | ForEach-Object { Say ("LOG " + $_) }
$app.WaitForExit(25000) | Out-Null
Say ("app exit=" + $(if ($app.HasExited) { $app.ExitCode } else { 'TIMEOUT' }))
Say ("screenshot=" + $shot)
$out | Set-Content -Encoding utf8 $report
Say ("report=" + $report)
