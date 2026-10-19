$exe = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'src-tauri\target\release\peripheral-battery.exe'
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
$env:PB_SHOW_ON_START='1'
$env:PB_QUIT_AFTER_MS='30000'
$env:PB_SINGLE_INSTANCE_NAME='lead-cdp'
$env:PB_LOG_LEVEL='debug'
$env:PB_COLLECTOR='D:\desktop\电量显示插件\.venv\Scripts\python.exe -m collector --stdio --fake --scenario demo'
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--remote-debugging-port=9223'
$p = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 5
& node (Join-Path $PSScriptRoot 'cdp-check.js')
$code = $LASTEXITCODE
$p.WaitForExit(30000) | Out-Null
Write-Output "cdp_exit=$code app_exit=$($p.ExitCode)"
Remove-Item Env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS
