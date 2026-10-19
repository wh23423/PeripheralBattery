$ErrorActionPreference='Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$exe = Join-Path $root 'src-tauri\target\release\peripheral-battery.exe'
$data = Join-Path $env:TEMP 'pb-lead-data-kill'
if (Test-Path $data) { Remove-Item -Recurse -Force $data }
New-Item -ItemType Directory -Path $data -Force | Out-Null
$env:LOCALAPPDATA = $data
$env:PB_SINGLE_INSTANCE_NAME = 'lead-kill'
$env:PB_LOG_LEVEL = 'debug'
$env:PB_QUIT_AFTER_MS = '60000'
Remove-Item Env:PB_SHOW_ON_START -ErrorAction SilentlyContinue
Remove-Item Env:PB_COLLECTOR -ErrorAction SilentlyContinue
$p = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 4
function Get-Desc([int]$rootPid) {
  $all = Get-CimInstance Win32_Process
  $byParent = @{}
  foreach ($q in $all) { $k=[int]$q.ParentProcessId; if (-not $byParent.ContainsKey($k)) { $byParent[$k]=@() }; $byParent[$k] += [int]$q.ProcessId }
  $ids=@(); $st=New-Object System.Collections.Stack; $st.Push($rootPid)
  while ($st.Count) { $i=[int]$st.Pop(); $ids+=$i; if ($byParent.ContainsKey($i)) { foreach($c in $byParent[$i]){$st.Push($c)} } }
  return $ids
}
$before = Get-Desc $p.Id
Write-Output ("before kill: app=$($p.Id) tree=" + ($before -join ','))
$wv = $before | Where-Object { $_ -ne $p.Id } | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue } | Where-Object { $_.ProcessName -eq 'msedgewebview2' } | Select-Object -ExpandProperty Id
Write-Output ("webview2 children before: " + ($wv -join ','))
Stop-Process -Id $p.Id -Force
Write-Output 'app force-killed'
Start-Sleep -Seconds 10
$alive = @()
foreach ($id in $wv) { if (Get-Process -Id $id -ErrorAction SilentlyContinue) { $alive += $id } }
Write-Output ("webview2 still alive after 10s: " + ($alive -join ','))
$collector = Get-CimInstance Win32_Process -Filter "Name='collector.exe'" | Where-Object { $_.ExecutablePath -like "*$data*" -or $_.CommandLine -like "*$data*" }
Write-Output ("collector left: " + ($collector.ProcessId -join ','))
if ($alive.Count -gt 0) {
  foreach ($id in $alive) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
  Write-Output 'cleaned up surviving webview2 children'
}
