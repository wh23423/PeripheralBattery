#!/usr/bin/env pwsh
# One-click M0 demo / acceptance run for the Python collector.
#
#   1. --probe --fake demo   human-readable event stream (devices, alert,
#                            replaces, gone, tick, summary)
#   2. --stdio --fake        real pipe: hello is flushed at once, commands get a
#                            tick, shutdown exits; the captured stdout is fed to
#                            tools/collector_ndjson_check.py
#   3. --once --fake         one-shot NDJSON, also validated
#
# Usage:  pwsh tools/collector_demo.ps1 [-Scenario demo|static|migration] [-Speed 8] [-Duration 46]
[CmdletBinding()]
param(
    [ValidateSet('demo', 'static', 'migration')]
    [string]$Scenario = 'demo',
    [double]$Speed = 8,
    [double]$Duration = 46
)

$ErrorActionPreference = 'Stop'
# The collector emits UTF-8 (protocol.md §0); make the console match so device
# names with Chinese characters are not mojibake in this script's output.
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = 'C:\Users\28482\AppData\Local\Programs\Python\Python312\python.exe'
}
if (-not (Test-Path $python)) { throw "Python not found (looked for .venv and the system Python)" }

$data = Join-Path ([IO.Path]::GetTempPath()) ('pb-collector-demo-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $data | Out-Null
$checker = Join-Path $PSScriptRoot 'collector_ndjson_check.py'
$failed = $false

Push-Location $root
try {
    Write-Host "== 1. probe --fake --scenario $Scenario (human readable) ==" -ForegroundColor Cyan
    & $python -m collector --probe --fake --scenario $Scenario --speed $Speed `
        --duration $Duration --data-dir (Join-Path $data 'probe')
    if ($LASTEXITCODE -ne 0) { throw "probe exited with $LASTEXITCODE" }

    Write-Host "`n== 2. --stdio pipe: hello / get snapshot / shutdown ==" -ForegroundColor Cyan
    $outFile = Join-Path $data 'stdio.ndjson'
    $errFile = Join-Path $data 'stdio.stderr.txt'
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $python
    foreach ($argument in @('-m', 'collector', '--stdio', '--fake', '--scenario', $Scenario,
                            '--speed', $Speed, '--data-dir', (Join-Path $data 'stdio'))) {
        $psi.ArgumentList.Add($argument)
    }
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
    $proc = [System.Diagnostics.Process]::Start($psi)
    $lines = [System.Collections.Generic.List[string]]::new()
    # hello is the first line and must appear without any extra flush request
    $first = $proc.StandardOutput.ReadLine()
    if ($first -eq $null) { throw 'collector wrote no hello line' }
    $lines.Add($first)
    Start-Sleep -Milliseconds 700
    $proc.StandardInput.WriteLine('{"t":"get","what":"snapshot"}')
    $proc.StandardInput.Flush()
    Start-Sleep -Milliseconds 500
    $proc.StandardInput.WriteLine('{"t":"shutdown"}')
    $proc.StandardInput.Flush()
    if (-not $proc.WaitForExit(15000)) {
        $proc.Kill()
        throw 'collector did not exit after shutdown'
    }
    while ($null -ne ($line = $proc.StandardOutput.ReadLine())) { $lines.Add($line) }
    $stderr = $proc.StandardError.ReadToEnd()
    [IO.File]::WriteAllLines($outFile, $lines, [Text.UTF8Encoding]::new($false))
    if ($stderr) { [IO.File]::WriteAllText($errFile, $stderr) }
    Write-Host "captured $($lines.Count) line(s) -> $outFile"
    & $python $checker $outFile
    if ($LASTEXITCODE -ne 0) { throw 'stdio NDJSON validation failed' }
    if ($stderr -match 'Traceback') { throw "collector stderr had a traceback: $stderr" }

    Write-Host "`n== 3. --once NDJSON ==" -ForegroundColor Cyan
    $onceFile = Join-Path $data 'once.ndjson'
    & $python -m collector --once --fake --scenario $Scenario --speed $Speed `
        --data-dir (Join-Path $data 'once') | Tee-Object -FilePath $onceFile
    if ($LASTEXITCODE -ne 0) { throw "--once exited with $LASTEXITCODE" }
    & $python $checker $onceFile
    if ($LASTEXITCODE -ne 0) { throw '--once NDJSON validation failed' }

    Write-Host "`nPASS: collector M0 demo completed. Artefacts in $data" -ForegroundColor Green
}
catch {
    $failed = $true
    Write-Host "FAIL: $_" -ForegroundColor Red
}
finally {
    Pop-Location
}
if ($failed) { exit 1 }
exit 0
