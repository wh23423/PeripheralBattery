# Runtime audit -- single instance + normal quit / orphan check (release build)
param([string]$Root = "D:\desktop\电量显示插件")
$ErrorActionPreference = "Stop"
$exe = Join-Path $Root "src-tauri\target\release\peripheral-battery.exe"
$log = Join-Path $env:LOCALAPPDATA "PeripheralBattery\logs\app.log"
$outDir = Join-Path $Root "design\audit\evidence\runtime"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$report = Join-Path $outDir "test5-single-instance.log"

function Read-From($path, $offset) {
  $fs = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
  try { $fs.Position = $offset; $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8); return $sr.ReadToEnd() } finally { $fs.Dispose() }
}
$out = New-Object System.Collections.Generic.List[string]
function Say($s) { $out.Add($s); Write-Output $s }
function Count-Proc([string]$name) {
  return @(Get-CimInstance Win32_Process -Filter "Name='$name'" -ErrorAction SilentlyContinue).Count
}

Get-Process peripheral-battery -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400
$offset = (Get-Item $log).Length
$env:PB_LOG_LEVEL = "debug"
$env:PB_SINGLE_INSTANCE_NAME = "Global\PBAuditRuntime-single5"
$env:PB_QUIT_AFTER_MS = "22000"
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
Remove-Item Env:PB_FPS_PROBE -ErrorAction SilentlyContinue
Remove-Item Env:PB_HIDE_AFTER_MS -ErrorAction SilentlyContinue
Remove-Item Env:PB_MOCK_MUTATE_MS -ErrorAction SilentlyContinue
Remove-Item Env:PB_COLLECTOR -ErrorAction SilentlyContinue   # packaged collector

$app = Start-Process -FilePath $exe -PassThru
Say ("APP1 pid={0} start={1:HH:mm:ss.fff}" -f $app.Id, (Get-Date))
Start-Sleep -Seconds 3
$c1 = Count-Proc "peripheral-battery.exe"; $c2 = Count-Proc "collector.exe"
Say ("before 2nd: app_count={0} collector_count={1}" -f $c1, $c2)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$p2 = Start-Process -FilePath $exe -PassThru
$exited = $p2.WaitForExit(5000)
$sw.Stop()
Say ("APP2 pid={0} exited={1} after={2}ms exitcode={3}" -f $p2.Id, $exited, $sw.ElapsedMilliseconds, $(if ($exited) { $p2.ExitCode } else { 'n/a' }))
Start-Sleep -Milliseconds 800
$c1b = Count-Proc "peripheral-battery.exe"; $c2b = Count-Proc "collector.exe"
Say ("after 2nd: app_count={0} collector_count={1}" -f $c1b, $c2b)
$seg = Read-From $log $offset
$lines = @($seg -split "`r?`n" | Where-Object { $_ })
Say "=== single-instance / spawn logs ==="
$lines | Where-Object { $_ -match 'single-instance|sidecar spawn|hello protocol|app start|setup complete|ERROR|WARN' } | ForEach-Object { Say ("LOG " + $_) }
$appStarts = @($lines | Where-Object { $_ -match 'app start version=' }).Count
$spawns = @($lines | Where-Object { $_ -match 'sidecar spawn path=' }).Count
$si = @($lines | Where-Object { $_ -match 'single-instance: existing pid=' }).Count
Say ("app_start_lines={0} sidecar_spawn_lines={1} single_instance_exit_lines={2}" -f $appStarts, $spawns, $si)
$singleOk = ($exited -and $p2.ExitCode -eq 0 -and $sw.ElapsedMilliseconds -lt 1000 -and $c1b -eq 1 -and $c2b -eq 1 -and $si -ge 1 -and $spawns -eq 1)

$app.WaitForExit(30000) | Out-Null
$exitCode = $(if ($app.HasExited) { $app.ExitCode } else { 'TIMEOUT' })
Say ("APP1 exit code=" + $exitCode)
Start-Sleep -Milliseconds 900
$left = @(Get-Process peripheral-battery,collector -ErrorAction SilentlyContinue)
Say ("leftover after quit=" + $(if ($left.Count -eq 0) { 'none' } else { ($left | ForEach-Object { "$($_.ProcessName):$($_.Id)" }) -join ',' }))
$lines2 = @((Read-From $log $offset) -split "`r?`n" | Where-Object { $_ })
$errs = @($lines2 | Where-Object { $_ -match '\[ERROR\]|\[WARN\]|panic' })
Say ("ERROR/WARN count=" + $errs.Count); $errs | ForEach-Object { Say ("  BAD " + $_) }
$quitLines = @($lines2 | Where-Object { $_ -match 'quit requested|shutdown handshake|job object released|app exit' })
Say "=== quit logs ==="; $quitLines | ForEach-Object { Say ("LOG " + $_) }
Say ("RESULT single_instance=" + $(if ($singleOk) { 'PASS' } else { 'FAIL' }) + " quit_exit=" + $exitCode + " leftovers=" + $left.Count)
$out | Set-Content -Encoding utf8 $report
Say ("report=" + $report)
