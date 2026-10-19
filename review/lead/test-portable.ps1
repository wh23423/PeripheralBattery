$ErrorActionPreference='Stop'
$src = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'src-tauri\target\release'
$dst = Join-Path $env:TEMP '电量 显示 测试\pb app'
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
New-Item -ItemType Directory -Path $dst -Force | Out-Null
Copy-Item (Join-Path $src 'peripheral-battery.exe') $dst
Copy-Item -Recurse (Join-Path $src 'collector') (Join-Path $dst 'collector')
Write-Output "SRC=$src"
Write-Output "DST=$dst"
$env:PB_SHOW_ON_START='1'
$env:PB_QUIT_AFTER_MS='9000'
$env:PB_SINGLE_INSTANCE_NAME='lead-portable'
$env:PB_LOG_LEVEL='debug'
$sw=[Diagnostics.Stopwatch]::StartNew()
$p=Start-Process -FilePath (Join-Path $dst 'peripheral-battery.exe') -PassThru
$exited=$p.WaitForExit(30000)
$sw.Stop()
Write-Output ("exited={0} exitcode={1} elapsed_ms={2}" -f $exited, $(if($exited){$p.ExitCode}else{'n/a'}), $sw.ElapsedMilliseconds)
Start-Sleep -Milliseconds 500
$left = Get-Process peripheral-battery,collector -ErrorAction SilentlyContinue
if ($left) { $left | Select Id,ProcessName | Format-Table -AutoSize | Out-String } else { Write-Output 'no leftover app/collector processes' }
Write-Output '=== last app.log lines'
Get-Content "$env:LOCALAPPDATA\PeripheralBattery\logs\app.log" -Tail 50
