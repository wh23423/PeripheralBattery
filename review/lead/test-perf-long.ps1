$ErrorActionPreference='Stop'
$exe = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'src-tauri\target\release\peripheral-battery.exe'
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
Remove-Item Env:PB_COLLECTOR -ErrorAction SilentlyContinue
$env:PB_SINGLE_INSTANCE_NAME='lead-perf-long'
$env:PB_QUIT_AFTER_MS='330000'
$env:PB_LOG_LEVEL='info'

function Get-Tree([int]$rootPid) {
  $all = Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId
  $byParent = @{}
  foreach ($p in $all) {
    if (-not $byParent.ContainsKey([int]$p.ParentProcessId)) { $byParent[[int]$p.ParentProcessId] = @() }
    $byParent[[int]$p.ParentProcessId] += [int]$p.ProcessId
  }
  $ids = New-Object System.Collections.ArrayList
  $stack = New-Object System.Collections.Stack
  $stack.Push($rootPid)
  while ($stack.Count -gt 0) {
    $id = [int]$stack.Pop(); [void]$ids.Add($id)
    if ($byParent.ContainsKey($id)) { foreach ($c in $byParent[$id]) { $stack.Push($c) } }
  }
  return $ids
}
function Snap([int]$rootPid) {
  $ids = Get-Tree $rootPid
  $procs = Get-Process -Id $ids -ErrorAction SilentlyContinue
  [pscustomobject]@{
    T = (Get-Date)
    CPU = [double](($procs | Measure-Object CPU -Sum).Sum)
    WS  = [double](($procs | Measure-Object WorkingSet64 -Sum).Sum)
    Private = [double](($procs | Measure-Object PrivateMemorySize64 -Sum).Sum)
    Count = $procs.Count
  }
}
$p = Start-Process -FilePath $exe -PassThru
Write-Output "pid=$($p.Id)"
$prev = Snap $p.Id
Write-Output ("t=0 cpu={0:N2}s ws={1:N1}MB private={2:N1}MB procs={3}" -f $prev.CPU, ($prev.WS/1MB), ($prev.Private/1MB), $prev.Count)
for ($i=1; $i -le 6; $i++) {
  Start-Sleep -Seconds 50
  $s = Snap $p.Id
  $dt = ($s.T - $prev.T).TotalSeconds
  $cpuPct = (($s.CPU - $prev.CPU) / $dt) * 100
  $dws = ($s.WS - $prev.WS)/1MB
  $dpv = ($s.Private - $prev.Private)/1MB
  Write-Output ("t={0:N0}s cpu={1:N2}s cpu_pct={2:N3}% ws={3:N1}MB d_ws=+{4:N1}MB private={5:N1}MB d_private=+{6:N1}MB procs={7}" -f ((($s.T - $prev.T).TotalSeconds) + ($i*50-50)), $s.CPU, $cpuPct, ($s.WS/1MB), $dws, ($s.Private/1MB), $dpv, $s.Count)
  $prev = $s
}
Write-Output ("total_cpu={0:N2}s total_d_ws=+{1:N1}MB total_d_private=+{2:N1}MB" -f $prev.CPU, (($prev.WS - (Snap $p.Id).WS)/1MB), 0)
$exited = $p.WaitForExit(60000)
Write-Output ("exited={0} code={1}" -f $exited, $(if($exited){$p.ExitCode}else{'n/a'}))
