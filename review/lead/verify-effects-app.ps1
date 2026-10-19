$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class Win32MouseShot {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
  public struct POINT { public int X; public int Y; }
}
"@ | Out-Null
[Win32MouseShot]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName System.Drawing
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$exe = Join-Path $root 'src-tauri\target\release\peripheral-battery.exe'
$py  = Join-Path $root '.venv\Scripts\python.exe'
$data = Join-Path $env:TEMP 'pb-lead-effects-app'
if (Test-Path $data) { Remove-Item -Recurse -Force $data }
New-Item -ItemType Directory -Path (Join-Path $data 'PeripheralBattery') -Force | Out-Null
Set-Content -Path (Join-Path $data 'PeripheralBattery\app.json') -Value '{"pinned":true}' -Encoding utf8
$env:LOCALAPPDATA = $data
$env:PB_SHOW_ON_START='1'
$env:PB_QUIT_AFTER_MS='24000'
$env:PB_SINGLE_INSTANCE_NAME='lead-effects-app'
$env:PB_LOG_LEVEL='debug'
$env:PB_COLLECTOR = $py + ' -m collector --stdio --fake --scenario demo --speed 1'
$orig = New-Object Win32MouseShot+POINT
[Win32MouseShot]::GetCursorPos([ref]$orig) | Out-Null
$p = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 6
$log = Join-Path $data 'PeripheralBattery\logs\app.log'
$line = Select-String -Path $log -Pattern 'window shown origin=\((\d+),(\d+)\) size=(\d+)x(\d+)' | Select-Object -Last 1
if (-not $line) { throw 'no shown line' }
$x=[int]$line.Matches[0].Groups[1].Value; $y=[int]$line.Matches[0].Groups[2].Value
$w=[int]$line.Matches[0].Groups[3].Value; $h=[int]$line.Matches[0].Groups[4].Value
Write-Output ("window=" + $x + "," + $y + " " + $w + "x" + $h)
function Shot($name) {
  $bmp = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen((New-Object System.Drawing.Point($x,$y)), [System.Drawing.Point]::Empty, (New-Object System.Drawing.Size($w,$h)))
  $out = Join-Path $PSScriptRoot $name
  $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
  Write-Output ("saved " + $out)
}
# hero card center (DIP -> physical approx)
$hx = $x + [int](37.5 + 118 * 1.25)
$hy = $y + [int](37.5 + 190 * 1.25)
[Win32MouseShot]::SetCursorPos($hx, $hy) | Out-Null
Start-Sleep -Milliseconds 900
Shot 'effects-app-hero-hover.png'
# first row center
$rx = $x + [int](37.5 + 436 * 1.25)
$ry = $y + [int](37.5 + 100 * 1.25)
[Win32MouseShot]::SetCursorPos($rx, $ry) | Out-Null
Start-Sleep -Milliseconds 900
Shot 'effects-app-row-hover.png'
[Win32MouseShot]::SetCursorPos($orig.X, $orig.Y) | Out-Null
$p.WaitForExit(30000) | Out-Null
Write-Output ("app exit=" + $p.ExitCode)
