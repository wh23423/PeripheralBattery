# v0.1.2 安装包默认/自定义路径安装 → 真机启动 → 卸载 验收（Lead）
# 用 /D= 临时目录，避免动用户默认安装/遗留数据；安装后运行真实采集器截图，再静默卸载。
$ErrorActionPreference = 'Continue'
$script:failures = @()
function Check([string]$name, [bool]$ok, [string]$detail) {
    Write-Output (("{0} {1} | {2}" -f $(if ($ok) { 'PASS' } else { 'FAIL' }), $name, $detail))
    if (-not $ok) { $script:failures += "$name | $detail" }
}

$root = 'D:\desktop\电量显示插件'
$setup = Join-Path $root 'src-tauri\target\release\bundle\nsis\PeripheralBattery_0.1.2_x64-setup.exe'
$inst = Join-Path $env:TEMP 'PB-v012-install'
$data = Join-Path $env:TEMP 'PB-v012-data'
$regUn = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PeripheralBattery'
$py = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $setup)) { Write-Output "FAIL setup missing: $setup"; exit 1 }
Write-Output "setup=$setup"
Write-Output ("setup_sha256=" + (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower())

# 0) 前置
Check '前置：无 app 进程' (@(Get-Process peripheral-battery -ErrorAction SilentlyContinue).Count -eq 0) 'processes=0'
if (Test-Path $inst) { Remove-Item -Recurse -Force $inst -ErrorAction SilentlyContinue }
if (Test-Path $data) { Remove-Item -Recurse -Force $data -ErrorAction SilentlyContinue }

# 1) 静默安装到临时目录
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$p = Start-Process -FilePath $setup -ArgumentList '/S', "/D=$inst" -PassThru -Wait
$sw.Stop()
Check '安装：静默安装 exit=0' ($p.ExitCode -eq 0) ("exit={0} elapsed={1}ms" -f $p.ExitCode, $sw.ElapsedMilliseconds)
$appExe = Join-Path $inst 'peripheral-battery.exe'
Check '安装：app 在目标目录' (Test-Path $appExe) $appExe
Check '安装：collector 随包' (Test-Path (Join-Path $inst 'collector\collector.exe')) 'collector\collector.exe'
Check '安装：licenses 随包' ((Test-Path (Join-Path $inst 'licenses\LICENSE')) -and (Test-Path (Join-Path $inst 'licenses\THIRD_PARTY_NOTICES.md'))) 'licenses\'
if (Test-Path $appExe) {
    $vi = (Get-Item $appExe).VersionInfo
    Check '安装：exe 版本 0.1.2' (($vi.FileVersion -like '0.1.2*') -and ($vi.ProductVersion -like '0.1.2*')) ("file={0} product={1}" -f $vi.FileVersion, $vi.ProductVersion)
}
$instCollector = Join-Path $inst 'collector\collector.exe'
$buildInfo = Get-Content (Join-Path $root 'src-tauri\target\release\bundle\nsis\build-info.json') -Raw | ConvertFrom-Json
$expectedCollector = ($buildInfo.artifacts | Where-Object { $_.name -eq 'collector.exe' }).sha256
$instHash = (Get-FileHash $instCollector -Algorithm SHA256).Hash.ToLower()
Check '安装：随包 collector 与构建产物一致' ($instHash -eq $expectedCollector) $instHash
Check '安装：注册卸载项存在' (Test-Path $regUn) $regUn
if (Test-Path $regUn) {
    $prop = Get-ItemProperty $regUn
    Check '安装：DisplayVersion=0.1.2' ($prop.DisplayVersion -eq '0.1.2') ("version={0} publisher={1}" -f $prop.DisplayVersion, $prop.Publisher)
    Check '安装：InstallLocation=自定义临时目录' (($prop.InstallLocation -replace '"', '') -eq $inst) $prop.InstallLocation
}

# 2) 安装版真机启动 + 截图（真实采集器、真实设备；AppRun 用隔离 APPDATA/LOCALAPPDATA）
$realData = Join-Path $env:APPDATA 'PeripheralBattery'
$realBefore = @(Get-ChildItem $realData -Recurse -File -ErrorAction SilentlyContinue | Sort-Object FullName | ForEach-Object { "{0}|{1}|{2}" -f $_.FullName, $_.Length, $_.LastWriteTimeUtc.Ticks })
$env:PB_SHOW_ON_START = '1'
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $py
$psi.Arguments = 'tools\verify_v012_ui.py --exe "' + $appExe + '" --out v012-ui-installed.png --log-prefix v012-installed'
$psi.WorkingDirectory = $root
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$ui = [System.Diagnostics.Process]::Start($psi)
$uiOut = $ui.StandardOutput.ReadToEnd()
$uiErr = $ui.StandardError.ReadToEnd()
$ui.WaitForExit(120000) | Out-Null
$sw.Stop()
$uiCode = $ui.ExitCode
Write-Output $uiOut
if ($uiErr) { Write-Output ("ui_stderr=" + $uiErr.Substring(0, [Math]::Min(1500, $uiErr.Length))) }
Check '安装版：真实设备 UI 截图完成' ($uiCode -eq 0) ("exit={0} {1}ms" -f $uiCode, $sw.ElapsedMilliseconds)
Check '安装版：截图文件存在' (Test-Path (Join-Path $root 'design\verify\v012-ui-installed.png')) 'v012-ui-installed.png'

# 3) 退出后无孤儿 collector（安装目录来源）
Start-Sleep -Seconds 1
$orphans = @(Get-CimInstance Win32_Process -Filter "name='collector.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($inst, [System.StringComparison]::OrdinalIgnoreCase) })
Check '退出：无安装目录 collector 孤儿' ($orphans.Count -eq 0) ("orphans={0}" -f $orphans.Count)
Check '退出：无 app 进程残留' (@(Get-Process peripheral-battery -ErrorAction SilentlyContinue).Count -eq 0) 'processes=0'
$realAfter = @(Get-ChildItem $realData -Recurse -File -ErrorAction SilentlyContinue | Sort-Object FullName | ForEach-Object { "{0}|{1}|{2}" -f $_.FullName, $_.Length, $_.LastWriteTimeUtc.Ticks })
$changed = @(Compare-Object $realBefore $realAfter -ErrorAction SilentlyContinue)
Check '退出：真实用户数据目录未被污染' ($changed.Count -eq 0) ("files={0} changed={1}" -f $realAfter.Count, $changed.Count)
$instLog = Join-Path $root 'design\verify\v012-installed-app.log'
Check '安装版：日志显示隔离数据目录 + 版本 0.1.2' ((Test-Path $instLog) -and (Select-String -Path $instLog -Pattern 'version":"0.1.2' -Quiet) -and (Select-String -Path $instLog -Pattern 'pb-verify-v012-ui' -Quiet)) $instLog

# 4) 静默卸载（保留用户数据；WebView2 profile 会被卸载钩子无条件删除 -> 先备份后还原）
$profile = Join-Path $env:LOCALAPPDATA 'com.peripheralbattery.app'
$profileBak = Join-Path $env:TEMP 'PB-v012-profile-backup'
$hadProfile = Test-Path $profile
$profileSize = 0
if ($hadProfile) {
    $profileSize = [math]::Round(((Get-ChildItem $profile -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum) / 1MB, 1)
    if (Test-Path $profileBak) { Remove-Item -Recurse -Force $profileBak }
    Copy-Item $profile $profileBak -Recurse -Force
    Check '卸载前：已备份真实 WebView2 profile' (Test-Path (Join-Path $profileBak 'EBWebView')) ("{0} MB -> {1}" -f $profileSize, $profileBak)
} else {
    Write-Output 'SKIP 卸载前：无真实 WebView2 profile 可备份'
}
$un = Join-Path $inst 'uninstall.exe'
if (Test-Path $un) {
    $pu = Start-Process -FilePath $un -ArgumentList '/S' -PassThru -Wait
    Start-Sleep -Seconds 2
    Check '卸载：静默卸载 exit=0' ($pu.ExitCode -eq 0) ("exit={0}" -f $pu.ExitCode)
} else {
    Check '卸载：uninstall.exe 存在' $false $un
}
Check '卸载：安装目录已删除' (-not (Test-Path $inst)) $inst
Check '卸载：注册表卸载项已删除' (-not (Test-Path $regUn)) $regUn
if ($hadProfile) {
    Check '卸载：WebView2 profile 已被清理' (-not (Test-Path $profile)) $profile
    Copy-Item $profileBak $profile -Recurse -Force
    Check '恢复：用户 WebView2 profile 已还原' ((Test-Path (Join-Path $profile 'EBWebView')) -and ((Get-ChildItem $profile -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum -gt 0)) $profile
}
Check '卸载：用户数据目录保留（未传 /DELETEAPPDATA）' (Test-Path (Join-Path $env:APPDATA 'PeripheralBattery')) (Join-Path $env:APPDATA 'PeripheralBattery')

Write-Output ''
if ($script:failures.Count -eq 0) {
    Write-Output '===== INSTALL/UNINSTALL: ALL PASSED ====='
    exit 0
} else {
    Write-Output '===== INSTALL/UNINSTALL FAILURES ====='
    $script:failures | ForEach-Object { Write-Output " - $_" }
    exit 1
}
