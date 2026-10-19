# v0.1.1 A1 默认路径安装/卸载验收（Lead）
$ErrorActionPreference = 'Continue'
$result = [ordered]@{}
function Check([string]$name, [bool]$ok, [string]$detail) {
    Write-Output (("{0} {1} | {2}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail))
    if (-not $script:failures) { $script:failures = @() }
    if (-not $ok) { $script:failures += "$name | $detail" }
}
$script:failures = @()

$setup = 'D:\desktop\电量显示插件\src-tauri\target\release\bundle\nsis\PeripheralBattery_0.1.1_x64-setup.exe'
$inst = Join-Path $env:LOCALAPPDATA 'PeripheralBattery'
$dataNew = Join-Path $env:APPDATA 'PeripheralBattery'
$profile = Join-Path $env:LOCALAPPDATA 'com.peripheralbattery.app'
$bkRoot = Get-ChildItem $env:TEMP -Directory -Filter 'pb-install-test-backup-*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$bk = $bkRoot.FullName
Write-Output "backup=$bk"

# 备份 WebView2 profile（卸载会删，测后恢复原状）
$profileBk = Join-Path $bk 'webview2-profile'
if ((Test-Path $profile) -and -not (Test-Path $profileBk)) {
    Copy-Item -Recurse -Force -Path $profile -Destination $profileBk
    Write-Output "profile backup -> $profileBk"
}

# 0) 前置：无残留安装、无 app 进程
Check '前置：无 app 进程' (@(Get-Process peripheral-battery -ErrorAction SilentlyContinue).Count -eq 0) 'processes=0'
Check '前置：旧数据目录存在（模拟 0.1.0 用户）' (Test-Path (Join-Path $inst 'config.json')) 'config.json present'
Check '前置：无 MANUPRODUCTKEY 残留' (-not (Test-Path 'HKCU:\Software\peripheralbattery')) 'clean registry'

# 1) 默认路径静默安装
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$p = Start-Process -FilePath $setup -ArgumentList '/S' -PassThru -Wait
$sw.Stop()
Check '安装：静默安装 exit=0' ($p.ExitCode -eq 0) ("exit={0} elapsed={1}ms" -f $p.ExitCode, $sw.ElapsedMilliseconds)
Check '安装：INSTDIR 在默认路径' (Test-Path (Join-Path $inst 'peripheral-battery.exe')) $inst
Check '安装：collector 随包' (Test-Path (Join-Path $inst 'collector\collector.exe')) 'collector\collector.exe'
$collectorHash = (Get-FileHash (Join-Path $inst 'collector\collector.exe') -Algorithm SHA256).Hash.ToLower()
Check '安装：collector 是新构建（含 A10/A11）' ($collectorHash -eq '3fda3333eda0fd5d31fd1a4682828d8baed8175ae720288ba706ca38c49b9004') $collectorHash
Check '安装：LICENSE/NOTICES 随包' ((Test-Path (Join-Path $inst 'licenses\LICENSE')) -and (Test-Path (Join-Path $inst 'licenses\THIRD_PARTY_NOTICES.md'))) 'licenses\'
Check '安装：旧数据文件仍在 INSTDIR（升级场景混合目录）' (Test-Path (Join-Path $inst 'config.json')) 'config.json still in INSTDIR'
$regUn = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PeripheralBattery'
Check '安装：注册卸载项存在' (Test-Path $regUn) $regUn
if (Test-Path $regUn) {
    $prop = Get-ItemProperty $regUn
    Check '安装：DisplayVersion=0.1.1' ($prop.DisplayVersion -eq '0.1.1') ("version={0} publisher={1}" -f $prop.DisplayVersion, $prop.Publisher)
    Check '安装：InstallLocation=默认路径' (($prop.InstallLocation -replace '"', '') -eq $inst) $prop.InstallLocation
}

# 2) 首次启动：数据迁移 + 正常退出
$env:PB_COLLECTOR = ('"' + (Join-Path 'D:\desktop\电量显示插件' '.venv\Scripts\python.exe') + '" "' + (Join-Path 'D:\desktop\电量显示插件' 'scripts\mock-collector.py') + '"')
$env:PB_SHOW_ON_START = '1'
$env:PB_QUIT_AFTER_MS = '9000'
$env:PB_LOG_LEVEL = 'info'
$appExe = Join-Path $inst 'peripheral-battery.exe'
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$app = Start-Process -FilePath $appExe -PassThru
$appExited = $app.WaitForExit(25000)
$sw.Stop()
Check '首启：app 正常退出（迁移后 PB_QUIT_AFTER_MS）' ($appExited -and $app.ExitCode -eq 0) ("exited={0} code={1} {2}ms" -f $appExited, $app.ExitCode, $sw.ElapsedMilliseconds)
Check '迁移：新数据目录 %APPDATA%\PeripheralBattery 已创建' (Test-Path $dataNew) $dataNew
foreach ($f in @('config.json', 'app.json', 'aliases.json', 'history.json')) {
    Check ("迁移：{0} 已迁移" -f $f) (Test-Path (Join-Path $dataNew $f)) (Join-Path $dataNew $f)
}
Check '迁移：marker 已写入' (Test-Path (Join-Path $dataNew 'migration-v0.1.1.json')) 'migration-v0.1.1.json'
$appLog = Join-Path $dataNew 'logs\app.log'
Check '迁移：app.log 写到新目录' (Test-Path $appLog) $appLog
if (Test-Path $appLog) {
    $logText = Get-Content $appLog -Raw -ErrorAction SilentlyContinue
    Check '迁移：日志记录 legacy 迁移' ($logText -match 'legacy=') 'legacy='
    $m = [regex]::Match($logText, 'build=([0-9a-f]{7,40})')
    Check '构建号：release build 不再是 unknown' ($m.Success -and $m.Groups[1].Value -ne 'unknown') $m.Groups[1].Value
}
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
Remove-Item Env:PB_QUIT_AFTER_MS -ErrorAction SilentlyContinue
Remove-Item Env:PB_COLLECTOR -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 800

# 3) 静默卸载（不删数据）
$un = Join-Path $inst 'uninstall.exe'
$p = Start-Process -FilePath $un -ArgumentList '/S' -PassThru -Wait
Start-Sleep -Seconds 2
Check '卸载：静默卸载 exit=0' ($p.ExitCode -eq 0) ("exit={0}" -f $p.ExitCode)
Check '卸载：INSTDIR 已删除（递归清理）' (-not (Test-Path $inst)) ("exists={0}" -f (Test-Path $inst))
Check '卸载：卸载注册项已删除' (-not (Test-Path $regUn)) 'registry uninstall key'
Check '卸载：MANUPRODUCTKEY 已删除' (-not (Test-Path 'HKCU:\Software\peripheralbattery')) 'HKCU\Software\peripheralbattery'
Check '卸载：WebView2 profile 已删除' (-not (Test-Path $profile)) 'com.peripheralbattery.app'
Check '卸载：用户数据默认保留（%APPDATA%）' (Test-Path (Join-Path $dataNew 'config.json')) $dataNew
Check '卸载：无遗留 app 进程' (@(Get-Process peripheral-battery -ErrorAction SilentlyContinue).Count -eq 0) 'processes=0'

# 4) 重新安装 + /DELETEAPPDATA 卸载（删真实数据）
$p = Start-Process -FilePath $setup -ArgumentList '/S' -PassThru -Wait
Check '重装：exit=0' ($p.ExitCode -eq 0) ("exit={0}" -f $p.ExitCode)
$p = Start-Process -FilePath (Join-Path $inst 'uninstall.exe') -ArgumentList '/S /DELETEAPPDATA' -PassThru -Wait
Start-Sleep -Seconds 2
Check '/DELETEAPPDATA：静默卸载 exit=0' ($p.ExitCode -eq 0) ("exit={0}" -f $p.ExitCode)
Check '/DELETEAPPDATA：用户数据目录已删除' (-not (Test-Path $dataNew)) ("exists={0}" -f (Test-Path $dataNew))
Check '/DELETEAPPDATA：INSTDIR 已删除' (-not (Test-Path $inst)) 'INSTDIR'
Check '/DELETEAPPDATA：注册表无残留' (-not (Test-Path 'HKCU:\Software\peripheralbattery') -and -not (Test-Path $regUn)) 'registry clean'

# 5) 恢复原状
if (Test-Path (Join-Path $bk 'LocalPeripheralBattery')) {
    Copy-Item -Recurse -Force -Path (Join-Path $bk 'LocalPeripheralBattery') -Destination $inst
    Write-Output "restored local data -> $inst"
}
if (Test-Path $profileBk) {
    Copy-Item -Recurse -Force -Path $profileBk -Destination $profile
    Write-Output "restored webview2 profile"
}
if (Test-Path (Join-Path $bk 'hkcu-peripheralbattery.reg')) {
    & reg.exe import (Join-Path $bk 'hkcu-peripheralbattery.reg') 2>&1 | Out-Null
    Write-Output "restored HKCU peripheralbattery key"
}
if (Test-Path $dataNew) { Remove-Item -Recurse -Force $dataNew; Write-Output "removed test roaming data" }
Write-Output "--- final state ---"
Check '恢复：旧数据目录存在' (Test-Path (Join-Path $inst 'config.json')) 'local data restored'
Check '恢复：无 app 进程' (@(Get-Process peripheral-battery -ErrorAction SilentlyContinue).Count -eq 0) 'processes=0'
Check '恢复：%APPDATA% 测试数据已清理' (-not (Test-Path $dataNew)) 'clean'

# 结果
$out = [ordered]@{
    setup_sha256 = (Get-FileHash $setup -Algorithm SHA256).Hash
    failures = $script:failures
    passed = ($script:failures.Count -eq 0)
}
$out | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 'D:\desktop\电量显示插件\design\verify\a1-install-uninstall.json'
Write-Output ""
Write-Output ("===== {0}: failures={1} =====" -f $(if ($script:failures.Count -eq 0) { 'ALL PASS' } else { 'HAS FAILURES' }), $script:failures.Count)
foreach ($f in $script:failures) { Write-Output ("  FAIL: " + $f) }
