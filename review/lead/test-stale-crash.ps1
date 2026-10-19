$ErrorActionPreference='Stop'
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class DpiAware3 { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
"@ | Out-Null
[DpiAware3]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName System.Drawing
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$exe = Join-Path $root 'src-tauri\target\debug\peripheral-battery.exe'
$py = Join-Path $root '.venv\Scripts\python.exe'
$env:PB_SHOW_ON_START='1'
$env:PB_QUIT_AFTER_MS='22000'
$env:PB_SINGLE_INSTANCE_NAME='lead-stale'
$env:PB_LOG_LEVEL='debug'
$env:PB_COLLECTOR = $py + ' ' + (Join-Path $PSScriptRoot 'slow_crash_collector.py')
$p = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 6
Write-Output '--- should be hidden now (collector still alive, will die at ~8s)'
Start-Sleep -Seconds 4
Write-Output '--- collector dead; activate via second instance'
$p2 = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 2
$log = "$env:LOCALAPPDATA\PeripheralBattery\logs\app.log"
$line = Select-String -Path $log -Pattern 'window shown origin=\((\d+),(\d+)\) size=(\d+)x(\d+)' | Select-Object -Last 1
if ($line) {
  $x=[int]$line.Matches[0].Groups[1].Value; $y=[int]$line.Matches[0].Groups[2].Value
  $w=[int]$line.Matches[0].Groups[3].Value; $h=[int]$line.Matches[0].Groups[4].Value
  $bmp = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen((New-Object System.Drawing.Point($x,$y)), [System.Drawing.Point]::Empty, (New-Object System.Drawing.Size($w,$h)))
  $out = Join-Path $PSScriptRoot 'ui-stale-after-crash.png'
  $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
  Write-Output ("saved " + $out + " rect=" + $x + "," + $y + " " + $w + "x" + $h)
} else { Write-Output 'no shown line' }
$p.WaitForExit(30000) | Out-Null
Write-Output '--- last 45 log lines'
Get-Content $log -Tail 45
