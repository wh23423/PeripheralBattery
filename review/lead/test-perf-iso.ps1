$ErrorActionPreference='Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$src = Join-Path $root 'src-tauri\target\release'
$iso = Join-Path $env:TEMP 'pb-lead-perf-iso'
if (Test-Path $iso) { Remove-Item -Recurse -Force $iso -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Path $iso -Force | Out-Null
Copy-Item (Join-Path $src 'peripheral-battery.exe') $iso
Copy-Item -Recurse (Join-Path $src 'collector') (Join-Path $iso 'collector')
$data = Join-Path $iso 'data'
New-Item -ItemType Directory -Path $data -Force | Out-Null
$env:LOCALAPPDATA = $data
$env:PB_SINGLE_INSTANCE_NAME = 'lead-perf-iso'
$env:PB_QUIT_AFTER_MS = '340000'
$env:PB_LOG_LEVEL = 'info'
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
Remove-Item Env:PB_COLLECTOR -ErrorAction SilentlyContinue

function All-Processes { Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name }

function Get-AppTree($all, [int]$rootPid) {
  $byParent = @{}
  foreach ($p in $all) { $k=[int]$p.ParentProcessId; if (-not $byParent.ContainsKey($k)) { $byParent[$k]=@() }; $byParent[$k] += [int]$p.ProcessId }
  $ids = New-Object System.Collections.ArrayList
  $stack = New-Object System.Collections.Stack; $stack.Push($rootPid)
  while ($stack.Count -gt 0) { $i=[int]$stack.Pop(); [void]$ids.Add($i); if ($byParent.ContainsKey($i)) { foreach($c in $byParent[$i]){ $stack.Push($c) } } }
  return $ids
}
function Snap([int]$appPid) {
  $all = All-Processes
  $tree = Get-AppTree $all $appPid
  $treeProcs = Get-Process -Id $tree -ErrorAction SilentlyContinue
  $wvIds = @($all | Where-Object { $_.Name -eq 'msedgewebview2' } | ForEach-Object {
      $q = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ProcessId)" -ErrorAction SilentlyContinue
      if ($q -and $q.CommandLine -like '*webview-exe-name=peripheral-battery.exe*' -and $q.CommandLine -like '*EBWebView*') { $_.ProcessId }
  })
  $wvProcs = Get-Process -Id $wvIds -ErrorAction SilentlyContinue
  $allProcs = @($treeProcs) + @($wvProcs)
  [pscustomobject]@{
    T=(Get-Date)
    CPU=[double](($allProcs | Measure-Object CPU -Sum).Sum)
    WS=[double](($allProcs | Measure-Object WorkingSet64 -Sum).Sum)
    Private=[double](($allProcs | Measure-Object PrivateMemorySize64 -Sum).Sum)
    N=$allProcs.Count
    Detail=(($allProcs | ForEach-Object { "$($_.ProcessName):$($_.Id):$([Math]::Round($_.WorkingSet64/1MB,1))" }) -join ' ')
  }
}
$p = Start-Process -FilePath (Join-Path $iso 'peripheral-battery.exe') -PassThru
Write-Output "pid=$($p.Id) iso=$iso"
Start-Sleep -Seconds 20
$prev = Snap $p.Id
Write-Output ("t=20s cpu={0:N2} ws={1:N1}MB private={2:N1}MB n={3}" -f $prev.CPU, ($prev.WS/1MB), ($prev.Private/1MB), $prev.N)
Write-Output ("  " + $prev.Detail)
for ($i=1; $i -le 5; $i++) {
  Start-Sleep -Seconds 60
  $s = Snap $p.Id
  $dt = ($s.T - $prev.T).TotalSeconds
  Write-Output ("t={0:N0}s cpu_delta={1:N2}s cpu_pct={2:N3}% ws={3:N1}MB d_ws={4:+0.0;-0.0}MB private={5:N1}MB d_priv={6:+0.0;-0.0}MB n={7}" -f (20+$i*60), ($s.CPU-$prev.CPU), ((($s.CPU-$prev.CPU)/$dt)*100), ($s.WS/1MB), (($s.WS-$prev.WS)/1MB), ($s.Private/1MB), (($s.Private-$prev.Private)/1MB), $s.N)
  Write-Output ("  " + $s.Detail)
  $prev = $s
}
$exited = $p.WaitForExit(60000)
Write-Output ("exited={0} code={1}" -f $exited, $(if($exited){$p.ExitCode}else{'n/a'}))
