<#
.SYNOPSIS
  Tauri 外壳运行时取证：启动 app、读取窗口物理矩形、全屏截图、进程列表、app.log 快照。

.DESCRIPTION
  - 先把当前 PowerShell 进程设为 Per-Monitor DPI Aware V2，否则 125%/150% 缩放下截到的是
    被系统虚拟化缩放后的图，窗口矩形也不对。
  - 截图存 design\verify\<ShotName>-<HHmmss>.png，日志副本同名前缀 .log。
  - 需要 collector 缺失时的错误态：设置 PB_COLLECTOR 指向不存在的命令再运行。

.EXAMPLE
  $env:PB_SHOW_ON_START="1"; $env:PB_QUIT_AFTER_MS="40000"
  .\scripts\verify-shell.ps1 -WaitMs 3000
#>
param(
    [string]$Exe = "",
    [int]$WaitMs = 4000,
    [string]$ShotName = "tauri-window",
    [switch]$NoStart,
    [switch]$StopAfter
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Exe) { $Exe = Join-Path $root "src-tauri\target\debug\peripheral-battery.exe" }
$verifyDir = Join-Path $root "design\verify"
New-Item -ItemType Directory -Force -Path $verifyDir | Out-Null

# 1) DPI 感知（必须在读取屏幕/窗口几何之前）
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class DpiAware {
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
}
'@
[void][DpiAware]::SetProcessDpiAwarenessContext([IntPtr](-4))  # PER_MONITOR_AWARE_V2

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class WinInfo {
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint flags);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    public struct RECT { public int Left, Top, Right, Bottom; }
}
'@

if (-not (Test-Path $Exe)) { throw "exe 不存在: $Exe" }

$app = $null
if (-not $NoStart) {
    $app = Start-Process -FilePath $Exe -PassThru
    Write-Output "started pid=$($app.Id)"
    Write-Output "exe=$Exe"
}
Start-Sleep -Milliseconds $WaitMs

$proc = if ($app) {
    Get-Process -Id $app.Id -ErrorAction SilentlyContinue
} else {
    Get-Process peripheral-battery -ErrorAction SilentlyContinue | Select-Object -First 1
}

if ($proc) {
    $h = $proc.MainWindowHandle
    $r = New-Object WinInfo+RECT
    if ($h -ne 0 -and [WinInfo]::GetWindowRect($h, [ref]$r)) {
        $w = $r.Right - $r.Left
        $hh = $r.Bottom - $r.Top
        Write-Output ("window pid={0} hwnd={1} visible={2} rect=({3},{4})-({5},{6}) size={7}x{8}" -f `
            $proc.Id, $h, [WinInfo]::IsWindowVisible($h), $r.Left, $r.Top, $r.Right, $r.Bottom, $w, $hh)
    } else {
        Write-Output "window: handle unavailable（可能已隐藏/退出）"
    }
} else {
    Write-Output "app: not running"
}

# 2) 全屏物理像素截图
$vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $vs.Width, $vs.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($vs.Location, [System.Drawing.Point]::Empty, $vs.Size)
$stamp = Get-Date -Format "HHmmss"
$shot = Join-Path $verifyDir ("{0}-{1}.png" -f $ShotName, $stamp)
$bmp.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "screenshot=$shot screen=$($vs.Width)x$($vs.Height)"

# 3) 进程清单
Get-Process |
    Where-Object { $_.ProcessName -match "peripheral-battery|collector|python|msedgewebview2" } |
    Select-Object Id, ProcessName, @{n = 'WS_MB'; e = { [math]::Round($_.WorkingSet64 / 1MB, 1) } } |
    Sort-Object ProcessName, Id |
    Format-Table -AutoSize | Out-String -Width 200

# 4) 日志快照
$log = Join-Path $env:LOCALAPPDATA "PeripheralBattery\logs\app.log"
if (Test-Path $log) {
    $copy = Join-Path $verifyDir ("{0}-{1}.log" -f $ShotName, $stamp)
    Copy-Item $log $copy
    Write-Output "log_copy=$copy"
    Get-Content $log -Tail 40
} else {
    Write-Output "app.log 不存在: $log"
}

if ($StopAfter -and $proc) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $left = Get-Process peripheral-battery, collector -ErrorAction SilentlyContinue
    if ($left) { Write-Output ("WARN leftover: " + ($left.ProcessName -join ',')) }
    else { Write-Output "stopped; no orphan app/collector" }
}
