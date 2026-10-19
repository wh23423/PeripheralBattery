<#
.SYNOPSIS
  验证「隐藏期间 Rust 侧仍更新托盘 + resume 后补最新快照」。

.DESCRIPTION
  mock collector 每 1.5s 改一次电量/充电（PB_MOCK_MUTATE_MS）。app 以默认 hidden 启动，
  WebView 在 300ms 后被 TrySuspendAsync 冻结——此时前端无法再调 set_tray_state。
  断言：
    - 隐藏期间 app.log 出现 >=2 条 `tray-state source=rust` 且 level 发生变化；
    - 出现 `webview TrySuspend completed suspended=true`；
    - 第二实例激活显示时 `requested collector snapshot=true`，随后前端补发
      `tray-state source=frontend`，面板显示最新值（截图）。
#>
param(
    [string]$Root = "D:\desktop\电量显示插件"
)

$ErrorActionPreference = "Stop"
Get-Process peripheral-battery -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

$dir = Join-Path $env:LOCALAPPDATA "PeripheralBattery"
$log = Join-Path $dir "logs\app.log"
'{ "pinned": false }' | Set-Content -Encoding utf8 (Join-Path $dir "app.json")

$env:PB_QUIT_AFTER_MS = "26000"
$env:PB_LOG_LEVEL = "debug"
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
Remove-Item Env:PB_FPS_PROBE -ErrorAction SilentlyContinue
Remove-Item Env:PB_TEST_TRAY_ACTION -ErrorAction SilentlyContinue
$env:PB_MOCK_MUTATE_MS = "1500"
$env:PB_COLLECTOR = (Join-Path $Root ".venv\Scripts\python.exe") + " " + (Join-Path $Root "scripts\mock-collector.py")
$env:PB_SINGLE_INSTANCE_NAME = "Global\PBHiddenTrayTest"
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = ""

$exe = Join-Path $Root "src-tauri\target\debug\peripheral-battery.exe"
$app = Start-Process -FilePath $exe -PassThru
Write-Output "app pid=$($app.Id) (hidden, mock mutate 1500ms)"
Start-Sleep -Seconds 10

$marker = "app start version="
$lines = Get-Content $log
$idx = ($lines | Select-String -Pattern ($marker + ".*pid=" + $app.Id)).LineNumber
$runLines = if ($idx) { $lines[($idx - 1)..($lines.Count - 1)] } else { @() }

$rustTray = $runLines | Select-String -Pattern "tray-state source=rust"
Write-Output "--- hidden-phase rust tray updates ---"
$rustTray | ForEach-Object { Write-Output ("LOG " + $_.Line) }
$suspend = $runLines | Select-String -Pattern "TrySuspend completed suspended=true"
Write-Output ("TrySuspend suspended=true lines: " + @($suspend).Count)

$levels = @()
foreach ($l in $rustTray) {
    if ($l.Line -match "level=Some\((\d+)\)") { $levels += [int]$Matches[1] }
    elseif ($l.Line -match "level=None") { $levels += -1 }
}
$distinct = @($levels | Select-Object -Unique)
Write-Output ("rust tray levels observed: " + ($levels -join ',') + " | distinct=" + $distinct.Count)
$hiddenOk = (@($rustTray).Count -ge 2) -and ($distinct.Count -ge 2) -and (@($suspend).Count -ge 1)
Write-Output ("hidden tray update check: " + $(if ($hiddenOk) { "PASS" } else { "FAIL" }))

# 第二实例激活 → show_panel → 补快照
$beforeShow = (Get-Content $log).Count
$B = Start-Process -FilePath $exe -PassThru
[void]$B.WaitForExit(5000)
Start-Sleep -Milliseconds 2500
$after = Get-Content $log | Select-Object -Skip $beforeShow
Write-Output "--- resume phase ---"
$after | Select-String -Pattern "single-instance: activation|requested collector snapshot|tray-state source=frontend|window shown reason=single-instance" |
    ForEach-Object { Write-Output ("LOG " + $_.Line) }
$resumeOk = [bool]($after | Select-String -Pattern "requested collector snapshot=true") -and
            [bool]($after | Select-String -Pattern "tray-state source=frontend")
Write-Output ("resume snapshot check: " + $(if ($resumeOk) { "PASS" } else { "FAIL" }))

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices;
public class DpiH { [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr v); }
'@
[void][DpiH]::SetProcessDpiAwarenessContext([IntPtr](-4))
$vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $vs.Width, $vs.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($vs.Location, [System.Drawing.Point]::Empty, $vs.Size)
$shot = Join-Path $Root "design\verify\tauri-fix-hidden-tray.png"
$bmp.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "screenshot=$shot"

Get-Process -Id $app.Id -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item Env:PB_MOCK_MUTATE_MS -ErrorAction SilentlyContinue
if (-not ($hiddenOk -and $resumeOk)) { exit 1 }
Write-Output "PASS: hidden tray updates by Rust + resume snapshot refresh"
