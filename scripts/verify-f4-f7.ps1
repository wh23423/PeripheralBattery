<#
.SYNOPSIS
  验证 runtime-audit F4（ui_ready 2s 兜底误判）与 F7（collector stat 摘要落盘）。

.DESCRIPTION
  F4：PB_SHOW_ON_START=1 + pinned=false 启动，ui_ready 后让窗口失焦隐藏（优先真实
      SetForegroundWindow；若被全屏窗口干扰则退化为托盘左键隐藏——两者都会把
      window_visible 置 false）。2s 兜底线程不得再 show，日志不得出现
      `ui_ready not received within 2000ms`。
  F7：mock collector 会发 stat；app.log 必须出现
      `collector stat: timers_per_s=... key_migrations=... providers=... slowest=...`
#>
param(
    [string]$Root = "D:\desktop\电量显示插件"
)

$ErrorActionPreference = "Stop"
Get-Process peripheral-battery -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

$dir = Join-Path $env:LOCALAPPDATA "PeripheralBattery"
$log = Join-Path $dir "logs\app.log"
'{ "pinned": false }' | Set-Content -Encoding utf8 (Join-Path $dir "app.json")

$env:PB_SHOW_ON_START = "1"
$env:PB_QUIT_AFTER_MS = "16000"
$env:PB_LOG_LEVEL = "debug"
Remove-Item Env:PB_FPS_PROBE -ErrorAction SilentlyContinue
Remove-Item Env:PB_TEST_TRAY_ACTION -ErrorAction SilentlyContinue
Remove-Item Env:PB_MOCK_MUTATE_MS -ErrorAction SilentlyContinue
Remove-Item Env:PB_HIDE_AFTER_MS -ErrorAction SilentlyContinue
$env:PB_COLLECTOR = (Join-Path $Root ".venv\Scripts\python.exe") + " " + (Join-Path $Root "scripts\mock-collector.py")
$env:PB_SINGLE_INSTANCE_NAME = "Global\PBF4F7Test"

Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public class F47 {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassNameW(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr h, uint msg, IntPtr w, IntPtr l);

  public static IntPtr FindOtherWindow(uint myPid) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, l) => {
      if (!IsWindowVisible(h)) return true;
      uint p; GetWindowThreadProcessId(h, out p);
      if (p == myPid) return true;
      var t = new StringBuilder(256); GetWindowTextW(h, t, 256);
      var c = new StringBuilder(256); GetClassNameW(h, c, 256);
      // 排除 shell 的特殊窗口，选一个普通应用窗口
      if (t.Length > 2 && c.ToString() != "Progman" && c.ToString() != "WorkerW") { found = h; return false; }
      return true;
    }, IntPtr.Zero);
    return found;
  }
  public static IntPtr FindTrayHost(uint pid) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, l) => {
      uint p; GetWindowThreadProcessId(h, out p);
      if (p == pid) { var c = new StringBuilder(256); GetClassNameW(h, c, 256); if (c.ToString() == "tray_icon_app") { found = h; return false; } }
      return true;
    }, IntPtr.Zero);
    return found;
  }
}
'@

$exe = Join-Path $Root "src-tauri\target\debug\peripheral-battery.exe"
$app = Start-Process -FilePath $exe -PassThru
Write-Output "app pid=$($app.Id)  PB_SHOW_ON_START=1  pinned=false"
# 在 2s 兜底检查之前就隐藏窗口（模拟 audit 场景：ui_ready 已到、270ms 失焦自动隐藏）
Start-Sleep -Milliseconds 900
$mechanism = ""
$other = [F47]::FindOtherWindow([uint32]$app.Id)
if ($other -ne [IntPtr]::Zero) {
    [void][F47]::SetForegroundWindow($other)
    Start-Sleep -Milliseconds 700
    $lines = Get-Content $log
    $idx = ($lines | Select-String -Pattern ("app start version=.*pid=" + $app.Id)).LineNumber
    $runLines = if ($idx) { $lines[($idx - 1)..($lines.Count - 1)] } else { @() }
    if ($runLines | Select-String -Pattern "auto-hide triggered|window hidden reason=focus-lost") {
        $mechanism = "SetForegroundWindow 触发失焦自动隐藏"
    }
}
if (-not $mechanism) {
    # 环境干扰（全屏 topmost 应用抢不回焦点）→ 托盘左键在 ~1.6s 隐藏，
    # 同样让 window_visible 在 2s 兜底检查前变为 false。
    $tray = [F47]::FindTrayHost([uint32]$app.Id)
    [void][F47]::PostMessageW($tray, 6002, [IntPtr]1, [IntPtr]0x0202)  # WM_LBUTTONUP
    $mechanism = "synthetic tray left-click hide"
    Start-Sleep -Milliseconds 400
}
Write-Output ("hide mechanism: $mechanism")

# 等过 2s 兜底时间点
Start-Sleep -Seconds 3
$lines = Get-Content $log
$idx = ($lines | Select-String -Pattern ("app start version=.*pid=" + $app.Id)).LineNumber
$runLines = if ($idx) { $lines[($idx - 1)..($lines.Count - 1)] } else { @() }

Write-Output "--- relevant log ---"
$runLines | Select-String -Pattern "command ui_ready|ui_ready received; fallback|ui_ready not received|ui-ready-fallback|window hidden reason|collector stat:" |
    ForEach-Object { Write-Output ("LOG " + $_.Line) }

$statOk = [bool]($runLines | Select-String -Pattern "collector stat: timers_per_s=.* key_migrations=.* slowest=")
$misjudge = [bool]($runLines | Select-String -Pattern "ui_ready not received within 2000ms")
$fallbackShow = [bool]($runLines | Select-String -Pattern "window show reason=ui-ready-fallback")
$fallbackExit = [bool]($runLines | Select-String -Pattern "ui_ready received; fallback thread exits")
Write-Output ("F7 stat summary visible: " + $(if ($statOk) { "PASS" } else { "FAIL" }))
Write-Output ("F4 no misjudge warn:     " + $(if (-not $misjudge) { "PASS" } else { "FAIL" }))
Write-Output ("F4 no fallback show:     " + $(if (-not $fallbackShow) { "PASS" } else { "FAIL" }))
Write-Output ("F4 fallback thread exits:" + $(if ($fallbackExit) { "PASS" } else { "FAIL" }))

$copy = Join-Path $Root "design\verify\tauri-fix-f4-f7.log"
$runLines | Set-Content -Encoding utf8 $copy
Write-Output "log_copy=$copy"
Get-Process -Id $app.Id -ErrorAction SilentlyContinue | Stop-Process -Force
if (-not ($statOk -and -not $misjudge -and -not $fallbackShow -and $fallbackExit)) { exit 1 }
Write-Output "PASS: F4 fallback 不再误判，F7 stat 摘要落盘"
