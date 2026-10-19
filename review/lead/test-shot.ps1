$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class DpiAware2 { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
"@ | Out-Null
[DpiAware2]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName System.Drawing
$exe = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'src-tauri\target\release\peripheral-battery.exe'
$env:PB_SHOW_ON_START='1'
$env:PB_QUIT_AFTER_MS='22000'
$env:PB_SINGLE_INSTANCE_NAME='lead-shot'
$env:PB_LOG_LEVEL='debug'
$env:PB_COLLECTOR='D:\desktop\电量显示插件\.venv\Scripts\python.exe -m collector --stdio --fake --scenario demo'
$p = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 6
$log = "$env:LOCALAPPDATA\PeripheralBattery\logs\app.log"
$line = Select-String -Path $log -Pattern 'window shown origin=\((\d+),(\d+)\) size=(\d+)x(\d+)' | Select-Object -Last 1
if (-not $line) { throw 'no window shown line' }
$x=[int]$line.Matches[0].Groups[1].Value; $y=[int]$line.Matches[0].Groups[2].Value
$w=[int]$line.Matches[0].Groups[3].Value; $h=[int]$line.Matches[0].Groups[4].Value
Write-Output ("rect=(" + $x + "," + $y + ") " + $w + "x" + $h)
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen((New-Object System.Drawing.Point($x,$y)), [System.Drawing.Point]::Empty, (New-Object System.Drawing.Size($w,$h)))
$out = Join-Path $PSScriptRoot 'ui-real.png'
$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output ("saved " + $out)
$p.WaitForExit(30000) | Out-Null
