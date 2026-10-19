# Lead 验收：DPI 感知的整屏截图（否则 125% 缩放下 CopyFromScreen 只拿到逻辑尺寸，面板会被裁掉）。
param(
    [Parameter(Mandatory = $true)][string]$Out
)

$ErrorActionPreference = "Stop"
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class DpiAware {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
"@ | Out-Null
[DpiAware]::SetProcessDPIAware() | Out-Null

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
$dir = Split-Path -Parent $Out
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()
Write-Output ("captured {0} ({1}x{2})" -f $Out, $b.Width, $b.Height)
