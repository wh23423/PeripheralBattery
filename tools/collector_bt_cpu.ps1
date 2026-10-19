#!/usr/bin/env pwsh
# Measure the hidden-state CPU cost of the Bluetooth watcher at a given
# connection-poll interval (P2 acceptance evidence).
#
#   pwsh tools/collector_bt_cpu.ps1 -WatchMs 2000  -Seconds 60   # baseline
#   pwsh tools/collector_bt_cpu.ps1 -WatchMs 10000 -Seconds 60   # new default
#
# Runs the real collector (--stdio, no fake devices), waits for the watcher,
# samples the process-tree CPU for N seconds (single-core percentage), then
# shuts the collector down. Prints python / PowerShell / tree sums plus the
# interval actually logged by the collector.
[CmdletBinding()]
param(
    [int]$WatchMs = 10000,
    [int]$Seconds = 60,
    [string]$DataDir = ""
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = 'C:\Users\28482\AppData\Local\Programs\Python\Python312\python.exe'
}
if (-not $DataDir) {
    $DataDir = Join-Path ([IO.Path]::GetTempPath()) ('pb-bt-cpu-' + [guid]::NewGuid().ToString('N'))
}
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

function Get-Descendants([int]$RootPid) {
    $all = New-Object System.Collections.Generic.List[object]
    $queue = New-Object System.Collections.Generic.Queue[int]
    $queue.Enqueue($RootPid)
    while ($queue.Count -gt 0) {
        $parent = $queue.Dequeue()
        foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$parent" -ErrorAction SilentlyContinue)) {
            $all.Add($child)
            $queue.Enqueue($child.ProcessId)
        }
    }
    return $all
}

function Get-CpuSeconds([int]$ProcessId) {
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) { return 0.0 }
    try { return $process.TotalProcessorTime.TotalSeconds } catch { return 0.0 }
}

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $python
foreach ($argument in @('-m', 'collector', '--stdio', '--data-dir', $DataDir)) {
    $psi.ArgumentList.Add($argument)
}
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false
$psi.Environment['PB_BT_WATCH_MS'] = [string]$WatchMs
$proc = [System.Diagnostics.Process]::Start($psi)

$log = Join-Path $DataDir 'logs\collector.log'
$deadline = (Get-Date).AddSeconds(20)
$loggedMs = $null
while ((Get-Date) -lt $deadline) {
    if (Test-Path $log) {
        $match = Select-String -Path $log -Pattern 'connection poll interval:\s*(\d+) ms' -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($match) { $loggedMs = [int]$match.Matches[0].Groups[1].Value; break }
    }
    Start-Sleep -Milliseconds 250
}
if ($null -eq $loggedMs) { throw "collector logged no interval (log: $log)" }

Start-Sleep -Seconds 8          # let the watcher settle
$tree = @(Get-Descendants $proc.Id)
$pids = @($tree | ForEach-Object { $_.ProcessId })
$names = @{}
foreach ($item in $tree) { $names[$item.ProcessId] = $item.Name }
if ($pids.Count -eq 0) { throw 'no collector descendants found' }

$before = @{}
foreach ($processId in $pids) { $before[$processId] = Get-CpuSeconds $processId }
Start-Sleep -Seconds $Seconds
$pythonDelta = 0.0
$powershellDelta = 0.0
foreach ($processId in $pids) {
    $delta = (Get-CpuSeconds $processId) - $before[$processId]
    if ($delta -lt 0) { $delta = 0 }
    if ($names[$processId] -match 'powershell|pwsh') { $powershellDelta += $delta }
    else { $pythonDelta += $delta }
}
$treeDelta = $pythonDelta + $powershellDelta

$proc.StandardInput.WriteLine('{"t":"shutdown"}')
$proc.StandardInput.Flush()
if (-not $proc.WaitForExit(15000)) { $proc.Kill() }

$result = [pscustomobject]@{
    watch_ms_logged = $loggedMs
    seconds         = $Seconds
    python_cpu_pct  = [math]::Round(($pythonDelta / $Seconds) * 100, 3)
    powershell_pct  = [math]::Round(($powershellDelta / $Seconds) * 100, 3)
    tree_cpu_pct    = [math]::Round(($treeDelta / $Seconds) * 100, 3)
    children        = (@($names.Values | Sort-Object -Unique) -join ',')
    data_dir        = $DataDir
}
$result | Format-List | Out-String -Width 200
