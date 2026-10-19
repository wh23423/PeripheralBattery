$ErrorActionPreference='SilentlyContinue'
foreach ($p in 12164,24760) { $x = Get-CimInstance Win32_Process -Filter "ProcessId=$p"; if ($x) { Write-Output "$p $($x.Name) $($x.ExecutablePath)"; Write-Output ("  " + $x.CommandLine) } else { Write-Output "$p NOT RUNNING" } }
Write-Output '=== leftover Tauri windows/classes?'
Get-Process | Where-Object { $_.MainWindowTitle -like '*外设*' } | Select-Object Id,ProcessName,MainWindowTitle | Format-Table -AutoSize | Out-String -Width 200
