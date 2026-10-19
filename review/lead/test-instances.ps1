$ErrorActionPreference='Stop'
$exe = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'src-tauri\target\release\peripheral-battery.exe'
$py  = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) '.venv\Scripts\python.exe'
$env:PB_LOG_LEVEL='debug'
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue

function Show-Log($n) { Get-Content "$env:LOCALAPPDATA\PeripheralBattery\logs\app.log" -Tail $n }

Write-Output '######## TEST A: single instance ########'
$env:PB_SINGLE_INSTANCE_NAME='lead-single'
$env:PB_QUIT_AFTER_MS='15000'
Remove-Item Env:PB_COLLECTOR -ErrorAction SilentlyContinue
$p1=Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 3
$sw=[Diagnostics.Stopwatch]::StartNew()
$p2=Start-Process -FilePath $exe -PassThru
$e2=$p2.WaitForExit(5000); $sw.Stop()
Write-Output ("second instance exited={0} code={1} elapsed_ms={2}" -f $e2, $(if($e2){$p2.ExitCode}else{'n/a'}), $sw.ElapsedMilliseconds)
$e1=$p1.WaitForExit(30000)
Write-Output ("first instance exited={0} code={1}" -f $e1, $(if($e1){$p1.ExitCode}else{'n/a'}))
Show-Log 25

Write-Output '######## TEST B: hanging collector shutdown ########'
$env:PB_SINGLE_INSTANCE_NAME='lead-hang'
$env:PB_QUIT_AFTER_MS='6000'
$env:PB_COLLECTOR="$py $PSScriptRoot\hang_collector.py"
$sw=[Diagnostics.Stopwatch]::StartNew()
$p=Start-Process -FilePath $exe -PassThru
$e=$p.WaitForExit(30000); $sw.Stop()
Write-Output ("hang test exited={0} code={1} elapsed_ms={2}" -f $e, $(if($e){$p.ExitCode}else{'n/a'}), $sw.ElapsedMilliseconds)
Start-Sleep -Milliseconds 500
$orphan = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*hang_collector*' }
if ($orphan) { Write-Output "ORPHAN python: $($orphan.ProcessId)" } else { Write-Output 'no orphan hang_collector python' }
Show-Log 30

Write-Output '######## TEST C: collector spawn failure ########'
$env:PB_SINGLE_INSTANCE_NAME='lead-missing'
$env:PB_QUIT_AFTER_MS='5000'
$env:PB_COLLECTOR='C:\nonexistent\collector.exe'
$sw=[Diagnostics.Stopwatch]::StartNew()
$p=Start-Process -FilePath $exe -PassThru
$e=$p.WaitForExit(30000); $sw.Stop()
Write-Output ("missing collector test exited={0} code={1} elapsed_ms={2}" -f $e, $(if($e){$p.ExitCode}else{'n/a'}), $sw.ElapsedMilliseconds)
Start-Sleep -Milliseconds 500
$left = Get-Process peripheral-battery,collector -ErrorAction SilentlyContinue
if ($left) { Write-Output 'LEFTOVER:'; $left | Select Id,ProcessName | Format-Table -AutoSize | Out-String } else { Write-Output 'no leftover processes' }
Show-Log 30

Write-Output '######## TEST D: collector crash after hello ########'
$env:PB_SINGLE_INSTANCE_NAME='lead-crash'
$env:PB_QUIT_AFTER_MS='12000'
$env:PB_COLLECTOR="$py $PSScriptRoot\crash_collector.py"
$p=Start-Process -FilePath $exe -PassThru
$e=$p.WaitForExit(30000)
Write-Output ("crash test exited={0} code={1}" -f $e, $(if($e){$p.ExitCode}else{'n/a'}))
Show-Log 30
