$ErrorActionPreference='Stop'
$exe = 'D:\desktop\电量显示插件\src-tauri\target\release\peripheral-battery.exe'
$data = Join-Path $env:TEMP 'pb-lead-data-basic'
if (Test-Path $data) { Remove-Item -Recurse -Force $data }
New-Item -ItemType Directory -Path $data -Force | Out-Null
$env:LOCALAPPDATA = $data
$env:PB_SHOW_ON_START = '1'
$env:PB_QUIT_AFTER_MS = '8000'
$env:PB_SINGLE_INSTANCE_NAME = 'lead-basic'
$env:PB_LOG_LEVEL = 'info'
$cmd = 'powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath ''' + $exe + ''' -Wait"'
runas /trustlevel:0x20000 $cmd
Start-Sleep -Seconds 12
$log = Join-Path $data 'PeripheralBattery\logs\app.log'
if (Test-Path $log) { Get-Content $log -Tail 35 } else { Write-Output 'NO LOG' }
Write-Output '--- leftover'
Get-Process peripheral-battery,collector -ErrorAction SilentlyContinue | Select Id,ProcessName | Format-Table -AutoSize | Out-String
