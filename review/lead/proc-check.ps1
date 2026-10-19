$ErrorActionPreference='SilentlyContinue'
Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" | Select-Object ProcessId,ParentProcessId | Format-Table -AutoSize | Out-String -Width 200
Write-Output '=== app procs'
Get-Process peripheral-battery,collector -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime | Format-Table -AutoSize | Out-String -Width 200
