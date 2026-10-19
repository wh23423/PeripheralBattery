$ErrorActionPreference='Stop'
$exe = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'src-tauri\target\release\peripheral-battery.exe'
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
Remove-Item Env:PB_COLLECTOR -ErrorAction SilentlyContinue
$env:PB_SINGLE_INSTANCE_NAME='lead-perf'
$env:PB_QUIT_AFTER_MS='95000'
$env:PB_LOG_LEVEL='info'

function Get-Tree([int]$rootPid) {
  $all = Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name
  $byParent = @{}
  foreach ($p in $all) {
    if (-not $byParent.ContainsKey([int]$p.ParentProcessId)) { $byParent[[int]$p.ParentProcessId] = @() }
    $byParent[[int]$p.ParentProcessId] += [int]$p.ProcessId
  }
  $ids = New-Object System.Collections.ArrayList
  $stack = New-Object System.Collections.Stack
  $stack.Push($rootPid)
  while ($stack.Count -gt 0) {
    $id = [int]$stack.Pop()
    [void]$ids.Add($id)
    if ($byParent.ContainsKey($id)) { foreach ($c in $byParent[$id]) { $stack.Push($c) } }
  }
  return $ids
}
function Snapshot([int]$rootPid) {
  $ids = Get-Tree $rootPid
  $procs = Get-Process -Id $ids -ErrorAction SilentlyContinue
  $cpu = ($procs | Measure-Object CPU -Sum).Sum
  $ws = ($procs | Measure-Object WorkingSet64 -Sum).Sum
  return @{ CPU = [double]$cpu; WS = [double]$ws; Procs = ($procs | ForEach-Object { "$($_.ProcessName):$($_.Id):$([math]::Round($_.WorkingSet64/1MB,1))MB" }) }
}

$p = Start-Process -FilePath $exe -PassThru
Write-Output "app pid=$($p.Id) started"
Start-Sleep -Seconds 25
$s1 = Snapshot $p.Id
$t1 = Get-Date
Write-Output ("sample1 t=25s procs={0} cpu_s={1:N3} ws_mb={2:N1}" -f ($s1.Procs.Count), $s1.CPU, ($s1.WS/1MB))
Write-Output ("  " + ($s1.Procs -join ', '))
Start-Sleep -Seconds 25
$s2 = Snapshot $p.Id
$t2 = Get-Date
$elapsed = ($t2 - $t1).TotalSeconds
$cpuPct = (($s2.CPU - $s1.CPU) / $elapsed) * 100
Write-Output ("sample2 t=50s procs={0} cpu_s={1:N3} ws_mb={2:N1} cpu_pct_single={3:N3}%" -f ($s2.Procs.Count), $s2.CPU, ($s2.WS/1MB), $cpuPct)
Write-Output ("  " + ($s2.Procs -join ', '))
Start-Sleep -Seconds 25
$s3 = Snapshot $p.Id
$t3 = Get-Date
$elapsed2 = ($t3 - $t2).TotalSeconds
$cpuPct2 = (($s3.CPU - $s2.CPU) / $elapsed2) * 100
$growth = (($s3.WS - $s2.WS) / 1MB)
Write-Output ("sample3 t=75s procs={0} cpu_s={1:N3} ws_mb={2:N1} cpu_pct_single={3:N3}% mem_growth_25s_mb={4:N1}" -f ($s3.Procs.Count), $s3.CPU, ($s3.WS/1MB), $cpuPct2, $growth)
Write-Output ("  " + ($s3.Procs -join ', '))
$exited = $p.WaitForExit(40000)
Write-Output ("exited={0} code={1}" -f $exited, $(if($exited){$p.ExitCode}else{'n/a'}))
