$ErrorActionPreference='Continue'
$out = Join-Path $PSScriptRoot 'mutex-result.txt'
$lines = @()
foreach ($name in @('Global\PBReviewMutexTest','Local\PBReviewMutexTest')) {
  try {
    $m = New-Object System.Threading.Mutex($false, $name)
    $lines += ("CREATED " + $name)
    try { $lines += ("WAIT " + $m.WaitOne(0)) } catch { $lines += ("WAIT_FAIL " + $_.Exception.Message) }
    try { $m.ReleaseMutex() } catch {}
    $m.Dispose()
  } catch {
    $lines += ("FAIL " + $name + " :: " + $_.Exception.Message)
  }
}
$lines += ("WHOAMI " + (whoami))
$lines | Out-File -Encoding utf8 $out
