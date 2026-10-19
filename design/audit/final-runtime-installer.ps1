# Runtime audit -- NSIS installer layout + installed run + clean uninstall
param([string]$Root = "D:\desktop\电量显示插件")
$ErrorActionPreference = "Stop"
$setup = Join-Path $Root "src-tauri\target\release\bundle\nsis\PeripheralBattery_0.1.0_x64-setup.exe"
$dest = Join-Path $env:TEMP "PB-audit-final"
$log = Join-Path $env:LOCALAPPDATA "PeripheralBattery\logs\app.log"
$outDir = Join-Path $Root "design\audit\final-runtime-evidence"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$report = Join-Path $outDir "test7-installer.log"
function Read-From($path, $offset) {
  $fs = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
  try { $fs.Position = $offset; $sr = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8); return $sr.ReadToEnd() } finally { $fs.Dispose() }
}
$out = New-Object System.Collections.Generic.List[string]
function Say($s) { $out.Add($s); Write-Output $s }

Get-Process peripheral-battery -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 400
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
Say ("setup=" + $setup + " exists=" + (Test-Path $setup))
Say ("dest=" + $dest)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$ip = Start-Process -FilePath $setup -ArgumentList '/S', ("/D=" + $dest) -PassThru -Wait
$sw.Stop()
Say ("installer exit=" + $ip.ExitCode + " elapsed=" + $sw.ElapsedMilliseconds + "ms")
$files = @(Get-ChildItem -Recurse -File $dest -ErrorAction SilentlyContinue)
Say ("installed file count=" + $files.Count + " totalMB=" + [math]::Round(($files | Measure-Object Length -Sum).Sum / 1MB, 1))
$checks = @(
  (Join-Path $dest "peripheral-battery.exe"),
  (Join-Path $dest "collector\collector.exe"),
  (Join-Path $dest "collector\_internal\python312.dll")
)
foreach ($c in $checks) { Say ("exists " + $c + " => " + (Test-Path $c)) }
Say "=== top-level install dir ==="
Get-ChildItem $dest | ForEach-Object { Say ("  " + $_.Name) }

# --- start menu / desktop shortcuts before ---
$prog = [Environment]::GetFolderPath('Programs')
$desk = [Environment]::GetFolderPath('Desktop')
$smBefore = @(Get-ChildItem -Recurse -File $prog -Filter *.lnk -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'Peripheral' })
$deskBefore = @(Get-ChildItem -File $desk -Filter *.lnk -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'Peripheral' })
Say ("shortcuts before: startmenu=[" + (($smBefore | ForEach-Object { $_.FullName }) -join ';') + "] desktop=[" + (($deskBefore | ForEach-Object { $_.FullName }) -join ';') + "]")
$regKey = Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" -ErrorAction SilentlyContinue | Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -match 'Peripheral' }
Say ("uninstall reg key before: " + ($(if ($regKey) { $regKey.PSChildName } else { 'none' })))

# --- run installed exe 8s visible ---
$offset = (Get-Item $log).Length
$env:PB_LOG_LEVEL = "debug"
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
$env:PB_QUIT_AFTER_MS = "6000"
$env:PB_SINGLE_INSTANCE_NAME = "Global\PBAuditFinal-inst7"
Remove-Item Env:PB_FPS_PROBE -ErrorAction SilentlyContinue
Remove-Item Env:PB_HIDE_AFTER_MS -ErrorAction SilentlyContinue
Remove-Item Env:PB_COLLECTOR -ErrorAction SilentlyContinue
$instExe = Join-Path $dest "peripheral-battery.exe"
$app = Start-Process -FilePath $instExe -PassThru
$app.WaitForExit(30000) | Out-Null
$exitCode = $(if ($app.HasExited) { $app.ExitCode } else { 'TIMEOUT' })
Start-Sleep -Milliseconds 700
$seg = Read-From $log $offset
$lines = @($seg -split "`r?`n" | Where-Object { $_ })
Say "=== installed run logs ==="
$lines | Where-Object { $_ -match 'app start version=|sidecar spawn|sidecar spawned|hello protocol|collector-state state=ready|window shown|quit requested|job object released|app exit|ERROR|WARN' } | ForEach-Object { Say ("LOG " + $_) }
$spawnLine = @($lines | Where-Object { $_ -match 'sidecar spawn path=' }) | Select-Object -First 1
$fromDest = $spawnLine -and ($spawnLine -match [regex]::Escape($dest))
$helloReady = [bool](@($lines | Where-Object { $_ -match 'hello protocol=1' }) | Select-Object -First 1) -and [bool](@($lines | Where-Object { $_ -match 'collector-state state=ready' }) | Select-Object -First 1)
Say ("installed-sidecar-from-dest={0} hello_ready={1} exit={2}" -f $fromDest, $helloReady, $exitCode)
Start-Sleep -Milliseconds 500
$left1 = @(Get-Process peripheral-battery,collector -ErrorAction SilentlyContinue)
Say ("leftover after installed run=" + $(if ($left1.Count -eq 0) { 'none' } else { ($left1 | ForEach-Object { "$($_.ProcessName):$($_.Id)" }) -join ',' }))

# --- uninstall ---
$uninst = Get-ChildItem -File $dest -Filter "uninstall*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
Say ("uninstaller=" + $(if ($uninst) { $uninst.FullName } else { 'NOT FOUND' }))
if ($uninst) {
  $u = Start-Process -FilePath $uninst.FullName -ArgumentList '/S' -PassThru -Wait
  Say ("uninstaller exit=" + $u.ExitCode)
  $deadline = (Get-Date).AddSeconds(20)
  while ((Test-Path $dest) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
  Say ("dest exists after uninstall=" + (Test-Path $dest))
}
$smAfter = @(Get-ChildItem -Recurse -File $prog -Filter *.lnk -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'Peripheral' })
$deskAfter = @(Get-ChildItem -File $desk -Filter *.lnk -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'Peripheral' })
Say ("shortcuts after: startmenu=[" + (($smAfter | ForEach-Object { $_.FullName }) -join ';') + "] desktop=[" + (($deskAfter | ForEach-Object { $_.FullName }) -join ';') + "]")
$regKey2 = Get-ChildItem "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" -ErrorAction SilentlyContinue | Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -match 'Peripheral' }
Say ("uninstall reg key after: " + ($(if ($regKey2) { $regKey2.PSChildName } else { 'none' })))
$left2 = @(Get-Process peripheral-battery,collector -ErrorAction SilentlyContinue)
Say ("leftover after uninstall=" + $(if ($left2.Count -eq 0) { 'none' } else { ($left2 | ForEach-Object { "$($_.ProcessName):$($_.Id)" }) -join ',' }))
$errs = @($lines | Where-Object { $_ -match '\[ERROR\]|\[WARN\]|panic' })
Say ("ERROR/WARN count=" + $errs.Count); $errs | ForEach-Object { Say ("  BAD " + $_) }
$pass = (-not (Test-Path $dest)) -and $fromDest -and $helloReady -and ($exitCode -eq 0) -and ($left1.Count -eq 0) -and ($left2.Count -eq 0) -and ($smAfter.Count -eq 0) -and ($deskAfter.Count -eq 0)
Say ("RESULT installer=" + $(if ($pass) { 'PASS' } else { 'FAIL' }))
$out | Set-Content -Encoding utf8 $report
Say ("report=" + $report)
